#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务模式 JSONL 协议的固定接口。"""

import json
import threading


class ServerProtocolWriter:
    """线程安全地写入服务协议事件。"""

    protocol_version = 1
    capabilities = ["http_stop", "signal_stop", "bearer_auth", "launch_token", "health"]

    def __init__(self, stream, diagnostic=None):
        """保存输出流和安全诊断回调。"""
        self.stream = stream
        self.diagnostic = diagnostic
        self._lock = threading.Lock()
        self._state = "new"
        self._disabled = False

    def ready(self, *, pid, bind_host, bind_port, url, auth_required,
              launch_token_included, message=None):
        """输出唯一的服务就绪事件。"""
        with self._lock:
            self._validate_ready(pid, bind_host, bind_port, url, auth_required,
                                 launch_token_included, message)
            if self._state != "new":
                raise RuntimeError("ready 只能在初始状态输出")
            self._state = "ready"
            payload = {
                "protocol_version": self.protocol_version,
                "event": "server_ready",
                "pid": pid,
                "bind_host": bind_host,
                "bind_port": bind_port,
                "url": url,
                "auth_required": auth_required,
                "launch_token_included": launch_token_included,
                "capabilities": self.capabilities.copy(),
            }
            if message is not None:
                payload["message"] = message
            self._write_locked(payload)

    def stopping(self, *, reason):
        """输出唯一的服务停止事件。"""
        with self._lock:
            if type(reason) is not str or reason not in {"api", "signal", "error"}:
                raise ValueError("reason 必须是 api、signal 或 error")
            if self._state != "ready":
                raise RuntimeError("stopping 只能在 ready 状态输出")
            self._state = "stopping"
            self._write_locked({
                "protocol_version": self.protocol_version,
                "event": "server_stopping",
                "reason": reason,
            })

    def error(self, *, code, message):
        """输出启动或运行错误事件。"""
        with self._lock:
            if type(code) is not str or type(message) is not str:
                raise TypeError("code 和 message 必须是字符串")
            if self._disabled:
                return
            if self._state not in {"new", "stopping"}:
                raise RuntimeError("server_error 只能在启动失败或 stopping 后输出")
            self._state = "error"
            self._write_locked({
                "protocol_version": self.protocol_version,
                "event": "server_error",
                "code": code,
                "message": message,
            })

    @staticmethod
    def _validate_ready(pid, bind_host, bind_port, url, auth_required,
                        launch_token_included, message):
        """校验 ready 事件字段类型。"""
        if type(pid) is not int:
            raise TypeError("pid 必须是整数")
        if type(bind_host) is not str or type(url) is not str:
            raise TypeError("bind_host 和 url 必须是字符串")
        if type(bind_port) is not int:
            raise TypeError("bind_port 必须是整数")
        if type(auth_required) is not bool or type(launch_token_included) is not bool:
            raise TypeError("认证字段必须是布尔值")
        if message is not None and type(message) is not str:
            raise TypeError("message 必须是字符串")

    def _write_locked(self, payload):
        """在锁内完整写入一行并刷新，输出失败后永久禁用。"""
        if self._disabled:
            return
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            self.stream.write(line)
            self.stream.flush()
        except (BrokenPipeError, OSError):
            self._disabled = True
            if self.diagnostic is not None:
                try:
                    self.diagnostic("协议输出不可用")
                except Exception:
                    pass


JsonLineProtocol = ServerProtocolWriter
