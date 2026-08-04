# -*- coding: utf-8 -*-
"""
飞书通知模块
使用应用消息 API 发送签到结果到飞书会话
"""

import json
import os
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

try:
    import requests
except ImportError:
    requests = None


class FeishuNotifier:
    """飞书应用消息通知类"""

    def __init__(self, app_id: str, app_secret: str, chat_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self._token = None
        self._token_expire_at = 0

    def _get_tenant_access_token(self) -> Optional[str]:
        """获取 tenant_access_token（带缓存，到期前 5 分钟复用）"""
        now = time.time()
        if self._token and now < self._token_expire_at - 300:
            return self._token

        resp = requests.post(
            'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
            json={'app_id': self.app_id, 'app_secret': self.app_secret},
            timeout=10,
        )
        data = resp.json()
        if data.get('code') == 0:
            self._token = data['tenant_access_token']
            self._token_expire_at = now + data.get('expire', 7200)
            return self._token
        print(f'[飞书通知] 获取 token 失败: {data.get("msg", data)}')
        return None

    def send_text(self, content: str) -> bool:
        """发送文本消息到指定会话"""
        if requests is None:
            print('[飞书通知] 错误: 未安装 requests 库')
            return False

        token = self._get_tenant_access_token()
        if not token:
            return False

        url = 'https://open.feishu.cn/open-apis/im/v1/messages'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8',
        }
        params = {'receive_id_type': 'chat_id'}
        payload = {
            'receive_id': self.chat_id,
            'msg_type': 'text',
            'content': json.dumps({'text': content}, ensure_ascii=False),
        }

        try:
            resp = requests.post(url, headers=headers, params=params,
                                 json=payload, timeout=10)
            data = resp.json()
            if data.get('code') == 0:
                print('[飞书通知] 消息发送成功')
                return True
            print(f'[飞书通知] 发送失败: {data.get("msg", data)}')
            return False
        except Exception as e:
            print(f'[飞书通知] 发送异常: {e}')
            return False


def format_quota(quota: int) -> str:
    """格式化额度显示"""
    if quota >= 1000000:
        return f'{quota / 1000000:.2f}M'
    elif quota >= 1000:
        return f'{quota / 1000:.2f}K'
    return str(quota)


def build_checkin_report(results: List[Dict[str, Any]], execution_time: str) -> str:
    """构建签到报告文本"""
    success_list = [r for r in results if r.get('success')]
    fail_list = [r for r in results if not r.get('success')]

    lines = [
        '📋 NewAPI 签到报告',
        f'执行时间: {execution_time}',
        '──────────────',
    ]

    if success_list:
        lines.append(f'✅ 成功 ({len(success_list)}个)')
        for r in success_list:
            name = r.get('name', '未知账号')
            quota = r.get('quota_awarded', 0)
            quota_str = f'+{format_quota(quota)}' if quota else '-'
            checkin_count = r.get('checkin_count')
            detail = f'已签 {checkin_count} 天' if checkin_count else r.get('message', '成功')
            lines.append(f'· {name}: {quota_str} ({detail})')
        lines.append('')

    if fail_list:
        lines.append(f'❌ 失败 ({len(fail_list)}个)')
        for r in fail_list:
            name = r.get('name', '未知账号')
            message = r.get('message', '未知错误')
            lines.append(f'· {name}: {message}')
        lines.append('')

    total = len(results)
    success_count = len(success_list)
    fail_count = len(fail_list)

    if fail_count == 0:
        summary = f'汇总: 全部成功 ✨ ({success_count}/{total})'
    elif success_count == 0:
        summary = f'汇总: 全部失败 ⚠️ ({fail_count}/{total})'
    else:
        summary = f'汇总: 成功 {success_count}，失败 {fail_count}'

    expired = [r for r in fail_list if r.get('session_expired') or
               'session' in r.get('message', '').lower() or
               '认证' in r.get('message', '') or
               '过期' in r.get('message', '')]
    if expired:
        summary += '\n⚠️ 注意: 部分账号 Session 已失效，请及时更新 Cookie！'

    lines.append(summary)
    return '\n'.join(lines)


def send_checkin_notification(results: List[Dict[str, Any]],
                              execution_time: Optional[str] = None) -> bool:
    """发送签到通知到飞书"""
    app_id = os.environ.get('FEISHU_APP_ID', '')
    app_secret = os.environ.get('FEISHU_APP_SECRET', '')
    chat_id = os.environ.get('FEISHU_CHAT_ID', '')

    if not (app_id and app_secret and chat_id):
        print('[飞书通知] 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID，跳过通知')
        return False

    if not execution_time:
        execution_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    report = build_checkin_report(results, execution_time)
    notifier = FeishuNotifier(app_id, app_secret, chat_id)
    return notifier.send_text(report)


# 测试入口
if __name__ == '__main__':
    test_results = [
        {'name': '主力站', 'success': True, 'message': '签到成功',
         'quota_awarded': 500000, 'checkin_count': 15},
        {'name': '备用站', 'success': True, 'message': '签到成功',
         'quota_awarded': 100000, 'checkin_count': 8},
        {'name': '测试站', 'success': False, 'message': 'Session 已过期',
         'session_expired': True},
    ]

    report = build_checkin_report(test_results, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=== 消息预览 ===')
    print(report)
    print('================')

    if os.environ.get('FEISHU_APP_ID'):
        send_checkin_notification(test_results)
    else:
        print('\n提示: 设置 FEISHU_APP_ID 等环境变量后可测试实际发送')
