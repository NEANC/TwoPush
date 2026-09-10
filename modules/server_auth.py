#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务认证存储的固定接口。"""


class ServerAuthStore:
    """保存服务长期认证信息的基础接口。"""

    def __init__(self, access_token, clock=None):
        """保存长期访问令牌和可选时钟。"""
        self.access_token = access_token
        self.clock = clock

    def issue_launch_token(self):
        """签发一次性启动令牌。"""
        raise NotImplementedError

    def consume_launch_token(self, token):
        """兑换一次性启动令牌。"""
        raise NotImplementedError

    def is_session_valid(self, session):
        """检查会话是否有效。"""
        raise NotImplementedError

    def clear(self):
        """清除令牌和会话。"""
        raise NotImplementedError
