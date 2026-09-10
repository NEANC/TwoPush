#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务认证存储测试。"""

from modules.server_auth import ServerAuthStore


def test_issue_launch_token_requires_valid_long_lived_access_token():
    """无效长期令牌不得签发启动令牌。"""
    for access_token in ('', 'short-token'):
        store = ServerAuthStore(access_token)
        assert store.issue_launch_token() is None


def test_clear_is_idempotent():
    """认证清理可重复调用。"""
    store = ServerAuthStore('x' * 16)
    store.clear()
    store.clear()


def test_launch_token_is_secure_expiring_and_single_use():
    """启动令牌具有足够熵、会过期且只能使用一次。"""
    now = [100.0]
    store = ServerAuthStore('x' * 16, clock=lambda: now[0])
    token = store.issue_launch_token()
    assert len(token) >= 43
    assert token != store.access_token
    assert store.consume_launch_token(token) is not None
    assert store.consume_launch_token(token) is None


def test_launch_token_expires_after_sixty_seconds():
    now = [100.0]
    store = ServerAuthStore('legacy-access-token', clock=lambda: now[0])
    token = store.issue_launch_token()
    now[0] = 160.001
    assert store.consume_launch_token(token) is None


def test_clear_invalidates_sessions_and_launch_tokens():
    store = ServerAuthStore('legacy-access-token')
    token = store.issue_launch_token()
    session = store.consume_launch_token(token)
    assert store.is_session_valid(session)
    store.clear()
    assert not store.is_session_valid(session)
    assert store.consume_launch_token(token) is None
