#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务协议 JSONL 写入器测试。"""

import io
import json
import threading

import pytest

from modules.server_protocol import ServerProtocolWriter


class RecordingStringIO(io.StringIO):
    """记录写入和刷新次数的文本流。"""

    def __init__(self):
        super().__init__()
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1
        return super().flush()


def ready_kwargs():
    """返回有效 ready 字段。"""
    return {
        "pid": 123,
        "bind_host": "127.0.0.1",
        "bind_port": 52233,
        "url": "http://127.0.0.1:52233/",
        "auth_required": False,
        "launch_token_included": False,
    }


def test_ready_preserves_chinese_as_one_json_line_and_flushes():
    """ready 保留中文原文、单行输出并立即刷新。"""
    stream = RecordingStringIO()
    protocol = ServerProtocolWriter(stream)

    protocol.ready(**ready_kwargs(), message="服务已就绪")

    output = stream.getvalue()
    assert output.count("\n") == 1
    assert "服务已就绪" in output
    assert "\\u670d" not in output
    assert json.loads(output)["protocol_version"] == 1
    assert stream.flush_count == 1


@pytest.mark.parametrize(
    "invoke, expected_event",
    [
        (lambda protocol: protocol.ready(**ready_kwargs()), "server_ready"),
        (lambda protocol: (protocol.ready(**ready_kwargs()),
                           protocol.stopping(reason="api"))[1], "server_stopping"),
        (lambda protocol: protocol.error(code="BIND_FAILED", message="x"), "server_error"),
    ],
)
def test_every_protocol_event_has_version_one(invoke, expected_event):
    """每类事件都携带固定协议版本。"""
    stream = RecordingStringIO()
    protocol = ServerProtocolWriter(stream)

    invoke(protocol)

    payload = json.loads(stream.getvalue().splitlines()[-1])
    assert payload["protocol_version"] == 1


@pytest.mark.parametrize(
    "method, kwargs",
    [
        ("ready", {**ready_kwargs(), "pid": "1"}),
        ("ready", {**ready_kwargs(), "bind_port": "1"}),
        ("ready", {**ready_kwargs(), "auth_required": 0}),
        ("stopping", {"reason": "other"}),
        ("error", {"code": 123, "message": "x"}),
        ("error", {"code": "X", "message": 123}),
    ],
)
def test_protocol_rejects_invalid_field_types(method, kwargs):
    """协议入口拒绝错误字段类型。"""
    protocol = ServerProtocolWriter(io.StringIO())
    with pytest.raises((TypeError, ValueError)):
        getattr(protocol, method)(**kwargs)


def test_state_transitions_are_strict_and_errors_are_ordered():
    """状态转换必须遵循启动、停止、错误顺序。"""
    protocol = ServerProtocolWriter(io.StringIO())
    with pytest.raises(RuntimeError, match="ready"):
        protocol.stopping(reason="api")
    protocol.ready(**ready_kwargs())
    with pytest.raises(RuntimeError, match="ready"):
        protocol.ready(**ready_kwargs())
    with pytest.raises(RuntimeError, match="stopping"):
        protocol.error(code="SERVER_RUNTIME_ERROR", message="x")
    protocol.stopping(reason="error")
    protocol.error(code="SERVER_RUNTIME_ERROR", message="x")
    with pytest.raises(RuntimeError, match="error"):
        protocol.error(code="CLEANUP_FAILED", message="x")


def test_startup_error_can_be_emitted_directly():
    """启动失败允许不经过 ready 直接输出错误。"""
    stream = RecordingStringIO()
    protocol = ServerProtocolWriter(stream)
    protocol.error(code="SERVER_START_FAILED", message="启动失败")
    assert json.loads(stream.getvalue())["event"] == "server_error"


def test_concurrent_ready_writes_only_one_complete_line():
    """并发 ready 只能生成一行且不会交错。"""
    stream = RecordingStringIO()
    protocol = ServerProtocolWriter(stream)
    errors = []

    def emit():
        try:
            protocol.ready(**ready_kwargs())
        except RuntimeError:
            pass
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=emit) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "server_ready"


def test_broken_pipe_disables_writer_and_diagnoses_without_propagating():
    """断管后永久禁用 writer，且诊断回调不泄露异常细节。"""
    diagnostics = []

    class BrokenStream:
        def write(self, value):
            raise BrokenPipeError("secret pipe detail")

        def flush(self):
            raise AssertionError("flush must not run")

    protocol = ServerProtocolWriter(BrokenStream(), diagnostics.append)
    protocol.error(code="BIND_FAILED", message="安全摘要")
    protocol.error(code="SERVER_START_FAILED", message="不会输出")

    assert len(diagnostics) == 1
    assert "secret" not in diagnostics[0]


def test_oserror_disables_writer_permanently():
    """普通输出错误同样永久禁用且不向业务传播。"""
    calls = []

    class FailedStream:
        def write(self, value):
            raise OSError("secret detail")

    protocol = ServerProtocolWriter(FailedStream(), calls.append)
    protocol.error(code="BIND_FAILED", message="x")
    protocol.error(code="SERVER_START_FAILED", message="y")
    assert len(calls) == 1


def test_reason_must_be_one_of_fixed_values():
    """停止原因只能使用固定枚举。"""
    protocol = ServerProtocolWriter(io.StringIO())
    protocol.ready(**ready_kwargs())
    with pytest.raises(ValueError, match="reason"):
        protocol.stopping(reason="other")


def test_first_stopping_reason_wins_without_second_line():
    """停止请求只接受首个原因并只输出一次。"""
    stream = RecordingStringIO()
    protocol = ServerProtocolWriter(stream)
    protocol.ready(**ready_kwargs())
    protocol.stopping(reason="signal")
    with pytest.raises(RuntimeError, match="stopping"):
        protocol.stopping(reason="api")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["reason"] == "signal"
