#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务模式 JSONL 协议的固定接口。"""


class ServerProtocolWriter:
    """服务协议写入器的基础接口。"""

    def __init__(self, stream, diagnostic=None):
        """保存输出流和安全诊断回调。"""
        self.stream = stream
        self.diagnostic = diagnostic


JsonLineProtocol = ServerProtocolWriter
