#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QD 每日签到站专用客户端（qd.arityflow.top）

该站是独立于标准 NewAPI 的前端签到站：
  登录  POST /api/auth/login      {"username","password"} -> {"token"}  (JWT, 7天)
  鉴权  后续请求 Authorization: Bearer <token>
  状态  GET  /api/checkin/status   -> enabled/checked_today/reward_mode
  验证码 GET  /api/captcha         -> {"captcha_id","image_url","length"}
  签到  POST /api/checkin          {"code","captcha_id","captcha_answer"}

与 NewAPICheckin 完全独立：不共用其 user/self、session 续期、CF 绕过逻辑。
认证方式为"账号密码登录 → 换 JWT"，天然自动续期，token 过期无需人工。
签到码(code)由站长发放，可能轮换/过期，过期时明确报错提示更新 QD_CODE。
"""

import os
import json
import time
import urllib.request
import urllib.error
import ssl

try:
    import ddddocr
    DDDDOCR_AVAILABLE = True
except ImportError:
    DDDDOCR_AVAILABLE = False

# 签到保留原样打印，这里只放一个供引用常量
QD_CHECKIN_PATH = '/api/checkin'


def _load_env(path: str = None) -> dict:
    """读取 .env（简单 key=value 解析，不引第三方依赖）。"""
    result = {}
    p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(p):
        return result
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        result[k.strip()] = v.strip()
    return result


def _mask(text: str) -> str:
    if not text:
        return ''
    return text[:1] + '***' if len(text) > 4 else '***'


class QDCheckin:
    """独立 QD 站签到客户端：登录 → 验证码 → 签到，全程重试抗 CF 波动。

    构造参数优先级从高到低：显式传参 > 环境变量(QD_*) > .env 文件。"""
    def __init__(self, username=None, password=None, code=None, base_url=None,
                 user_id=None, env: dict = None):
        env = env or _load_env()
        self.username = username or env.get('QD_USERNAME') or os.environ.get('QD_USERNAME', '')
        self.password = password or env.get('QD_PASSWORD') or os.environ.get('QD_PASSWORD', '')
        self.code = code or env.get('QD_CODE') or os.environ.get('QD_CODE', '')
        self.base_url = (base_url or env.get('QD_URL') or os.environ.get('QD_URL', '')).rstrip('/')
        self.user_id = user_id or env.get('QD_USER_ID') or os.environ.get('QD_USER_ID', '')
        self._token = None
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE
        self._ocr = ddddocr.DdddOcr(show_ad=False) if DDDDOCR_AVAILABLE else None
        self._use_playwright = False  # 仅 requests 一直 403 时降级

    # ---------- 内部：请求 ----------
    def _request(self, method: str, path: str, body=None, headers=None, retries: int = 6):
        """发请求，遇 CF 1010(403) 退避重试，仍失败抛异常。"""
        last_err = None
        for i in range(retries):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(self.base_url + path, data=data, method=method)
            req.add_header('Content-Type', 'application/json')
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            try:
                resp = urllib.request.urlopen(req, timeout=20, context=self._ssl_ctx)
                raw = resp.read()
                result = json.loads(raw.decode('utf-8', 'replace')) if raw else {}
                # 401 => token 失效，清掉重取
                if result.get('detail') and 'token' in str(result.get('detail')).lower():
                    self._token = None
                return result
            except urllib.error.HTTPError as e:
                if e.code == 403:  # CF 1010 间歇拦截
                    last_err = e
                    time.sleep(2 + i)
                    continue
                raw = e.read() if hasattr(e, 'read') else b''
                try:
                    return json.loads(raw.decode('utf-8', 'replace')) if raw else {}
                except Exception:
                    raise e
            except Exception as e:
                last_err = e
                time.sleep(2 + i)
        raise last_err

    def _auth_headers(self):
        return {'Authorization': 'Bearer ' + self._token} if self._token else {}

    # ---------- 登录 ----------
    def login(self):
        """账号密码登录换 JWT token。"""
        if not (self.username and self.password):
            raise RuntimeError('QD 站未配置 QD_USERNAME / QD_PASSWORD（填入 .env）')
        payload = {'username': self.username, 'password': self.password}
        resp = self._request('POST', '/api/auth/login', payload)
        token = resp.get('token')
        if not token:
            raise RuntimeError(f'QD 登录失败: {resp}')
        self._token = token
        return token

    # ---------- 验证码 ----------
    def _get_captcha(self):
        """取图片验证码并 OCR 识别。返回 (captcha_id, answer) 或 None。"""
        if not self._ocr:
            raise RuntimeError('缺少 ddddocr，无法识别验证码（pip install ddddocr）')
        for _ in range(4):
            try:
                c = self._request('GET', '/api/captcha', headers=self._auth_headers())
                captcha_id = c.get('captcha_id')
                img_url = c.get('image_url')
                if not captcha_id or not img_url:
                    continue
                img = urllib.request.urlopen(img_url, timeout=15, context=self._ssl_ctx).read()
                ans = self._ocr.classification(img)
                if ans and len(ans) == c.get('length', 4):
                    return captcha_id, ans.lower()
            except Exception:
                time.sleep(1)
        return None

    # ---------- 签到 ----------
    def checkin(self):
        """执行 QD 签到，返回兼容现有 checkin_results 的 dict。"""
        if not self._token:
            try:
                self.login()
            except Exception as e:
                return {'success': False, 'message': f'QD 登录失败: {e}'}
        if not self.code:
            return {'success': False, 'message': 'QD 未配置签到码 QD_CODE（向站长获取）'}

        # 先看状态
        try:
            status = self._request('GET', '/api/checkin/status', headers=self._auth_headers())
        except Exception as e:
            return {'success': False, 'message': f'QD 获取状态失败: {e}'}
        if status.get('checked_today'):
            return {'success': True, 'already_checked': True,
                    'message': '今日已签到', 'checkin_date': None,
                    'quota_awarded': None}
        if not status.get('enabled'):
            return {'success': False, 'message': 'QD 签到功能未开启'}

        # 取验证码识别
        captcha = self._get_captcha()
        if not captcha:
            return {'success': False, 'message': 'QD 验证码识别失败（多次重试）'}

        captcha_id, answer = captcha
        payload = {'code': self.code, 'captcha_id': captcha_id, 'captcha_answer': answer, 'cf_token': ''}
        try:
            resp = self._request('POST', '/api/checkin', payload, headers=self._auth_headers())
        except Exception as e:
            return {'success': False, 'message': f'QD 签到请求失败: {e}'}

        reward = resp.get('reward')
        detail = resp.get('detail', '')
        if reward is not None:
            return {'success': True, 'message': f'签到成功，获得 {reward} 额度',
                    'quota_awarded': reward, 'checkin_date': resp.get('date')}
        # 签到码问题
        if '签到码' in detail or 'code' in str(detail).lower():
            return {'success': False,
                    'message': f'QD 签到码已失效或不正确，请更新 QD_CODE（{detail}）'}
        # 验证码问题
        if '验证码' in detail:
            return {'success': False, 'message': f'QD 验证码错误，请重试（{detail}）'}
        return {'success': False, 'message': f'QD 签到失败: {resp}'}


def run_qd_checkin() -> dict:
    """便捷入口：从 .env/环境变量构建 QDCheckin 并签到。"""
    qd = QDCheckin()
    if not qd.username:
        return {'success': False, 'message': 'QD 未配置，跳过'}
    return qd.checkin()