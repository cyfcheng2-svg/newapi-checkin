#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare Turnstile 求解器（Camoufox 无头浏览器）

手法复刻自 TheFloodDragon/newapi-checkin（scripts/newapi_turnstile.py）：
1. 路由拦截站点请求返回空白承载页（同 origin，不下载站点 SPA bundle）
2. 主世界注入 bootstrap：自载 challenges.cloudflare.com api.js 并 render widget
3. managed 模式通常自动签发；未签发则用真实鼠标事件点击复选框
4. token 写 DOM 属性桥接（Camoufox 的 page.evaluate 跑在隔离世界，window 不共享，DOM 共享）
5. token 由调用方（checkin.py）在 Python HTTP 层提交，浏览器与 HTTP 同机同 IP，
   满足 CF token 的 (sitekey, hostname, IP) 绑定

依赖：pip install "camoufox[geoip]" && python -m camoufox fetch
未安装时 solve() 返回 None，签到回退到原有直连流程。
"""

import re
import time
from pathlib import Path

# TheFloodDragon 的 bootstrap：主世界 IIFE，自载 api.js + render + DOM data-token 桥接
_BOOTSTRAP_TPL = re.search(
    r'_WIDGET_BOOTSTRAP_JS = r"""(.*?)"""',
    open(Path(__file__).parent / '_widget_bootstrap.js', encoding='utf-8').read(),
    re.S).group(1)

_STATE_JS = """() => {
    const h = document.getElementById('ck-ts-host');
    return h ? {state: h.getAttribute('data-state'),
                token: h.getAttribute('data-token') || '',
                err: h.getAttribute('data-error') || ''} : null;
}"""

_BOX_JS = """() => { const h = document.getElementById('ck-ts-host');
    if (!h) return null; const r = h.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height}; }"""


def fetch_sitekey(base_url):
    """从 GET /api/status 读 turnstile_check + turnstile_site_key。不开 Turnstile 返回 None。"""
    try:
        import requests
        resp = requests.get(base_url.rstrip('/') + '/api/status', timeout=15,
                            headers={'User-Agent': 'Mozilla/5.0'})
        data = (resp.json().get('data') or {})
        if data.get('turnstile_check') and data.get('turnstile_site_key'):
            return data['turnstile_site_key']
    except Exception as e:
        print(f'[Turnstile] /api/status 读取失败: {e}')
    return None


def solve(base_url, sitekey, timeout_s=90):
    """启动 Camoufox 无头求解 Turnstile token。失败返回 None。"""
    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        print('[Turnstile] 未安装 camoufox（pip install "camoufox[geoip]" && python -m camoufox fetch），跳过求解')
        return None

    blank = '<!doctype html><html><head><title>checkin</title></head><body></body></html>'
    origin = base_url.rstrip('/')
    t0 = time.time()
    try:
        with Camoufox(headless=True, humanize=0.6, geoip=True) as browser:
            page = browser.new_page()

            def route(r):
                # 站点全部请求回空白页（不下载 SPA），仅放行 CF challenges 域
                if 'challenges.cloudflare.com' in r.request.url:
                    return r.continue_()
                return r.fulfill(status=200, content_type='text/html; charset=utf-8', body=blank)

            page.route('**/*', route)
            page.goto(origin + '/ts', wait_until='domcontentloaded')
            page.add_script_tag(content=_BOOTSTRAP_TPL.replace('__SITEKEY__', sitekey))
            clicked = False
            while time.time() - t0 < timeout_s:
                time.sleep(2)
                st = page.evaluate(_STATE_JS) or {}
                if st.get('token'):
                    print(f"[Turnstile] +{int(time.time()-t0)}s 拿到 token (len={len(st['token'])})")
                    return st['token']
                if 'error' in (st.get('state') or '') or st.get('err'):
                    print(f"[Turnstile] 求解失败: state={st.get('state')} err={st.get('err')}")
                    return None
                # 14s 起点击一次复选框（interactive 模式需要；managed 自动签发无需）
                if not clicked and time.time() - t0 >= 14:
                    box = page.evaluate(_BOX_JS)
                    if box and box['w'] > 50:
                        x, y = box['x'] + 34, box['y'] + box['h'] / 2
                        page.mouse.move(x - 80, y - 60)
                        page.mouse.move(x - 15, y - 5, steps=2)
                        page.mouse.click(x, y)
                        print(f'[Turnstile] +{int(time.time()-t0)}s 点击复选框')
                    clicked = True
    except Exception as e:
        print(f'[Turnstile] Camoufox 异常: {e}')
    return None
