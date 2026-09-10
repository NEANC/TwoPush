#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务认证存储。"""

import threading
import time
import secrets


class ServerAuthStore:
    """保存长期令牌、启动令牌和进程内会话。"""

    def __init__(self, access_token, clock=None):
        """初始化认证存储。"""
        self.access_token = access_token
        self.clock = clock or time.time
        self._launch_tokens = {}
        self._sessions = set()
        self._lock = threading.Lock()

    def issue_launch_token(self):
        """仅为有效长期令牌签发一次性启动令牌。"""
        if not isinstance(self.access_token, str) or len(self.access_token) < 16:
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._launch_tokens[token] = self.clock() + 60
        return token

    def has_launch_token(self, token):
        """检查启动令牌是否存在且未过期，不消耗令牌。"""
        if not isinstance(token, str):
            return False
        with self._lock:
            expires_at = self._launch_tokens.get(token)
            return expires_at is not None and self.clock() < expires_at

    def consume_launch_token(self, token):
        """原子兑换未过期启动令牌并创建进程内会话。"""
        if not isinstance(token, str):
            return None
        with self._lock:
            expires_at = self._launch_tokens.pop(token, None)
            if expires_at is None or self.clock() >= expires_at:
                return None
            session = secrets.token_urlsafe(32)
            self._sessions.add(session)
            return session

    def is_session_valid(self, session):
        """检查进程内会话是否有效。"""
        with self._lock:
            return isinstance(session, str) and session in self._sessions

    def clear(self):
        """清除启动令牌和所有进程内会话。"""
        with self._lock:
            self._launch_tokens.clear()
            self._sessions.clear()
