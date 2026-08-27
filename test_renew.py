#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Session 自动续期 _try_login 的最小自检（不碰网络，纯 mock）。"""
import json
from unittest import mock
from checkin import NewAPICheckin


def _fake_resp(payload):
    r = mock.Mock()
    r.status_code = 200
    r.text = json.dumps(payload)
    r.json = lambda: payload
    return r


def test_no_creds_returns_false():
    c = NewAPICheckin('https://x.test', 'tok', '1', auth_type='bearer')
    assert c._try_login() is False, '无凭据必须返回 False，不应触发网络请求'


def test_bearer_success_renews_token():
    c = NewAPICheckin('https://x.test', 'oldtok', '1', auth_type='bearer',
                      login_username='u', login_password='p')
    with mock.patch.object(c.session, 'post',
                           return_value=_fake_resp({'success': True, 'data': {'access_token': 'NEWTOK', 'id': 1}})) as m:
        assert c._try_login(verbose=True) is True
        assert m.call_args.args[0].endswith('/api/user/login')
        assert m.call_args.kwargs['json'] == {'username': 'u', 'password': 'p'}
        assert c.session.headers.get('Authorization') == 'Bearer NEWTOK'


def test_login_failure_returns_false():
    c = NewAPICheckin('https://x.test', 'oldtok', '1', auth_type='bearer',
                      login_username='u', login_password='p')
    r = _fake_resp({'success': False})
    r.status_code = 401
    with mock.patch.object(c.session, 'post', return_value=r):
        assert c._try_login() is False


if __name__ == '__main__':
    test_no_creds_returns_false()
    test_bearer_success_renews_token()
    test_login_failure_returns_false()
    print('renew self-check OK')