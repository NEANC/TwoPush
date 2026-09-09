#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""CLI 推送进程控制器测试。"""

import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.push_process import PushProcessManager


class FakeStream:
    """提供可迭代的模拟输出流。"""

    def __init__(self, lines):
        self.lines = lines

    def __iter__(self):
        return iter(self.lines)

    def close(self):
        pass


class BlockingStream(FakeStream):
    """提供可控制结束时机的模拟输出流。"""

    def __init__(self, release_event):
        super().__init__([])
        self.release_event = release_event

    def __iter__(self):
        self.release_event.wait(10)
        return iter([])


class FakeProcess:
    """提供进程控制器所需的最小模拟进程接口。"""

    def __init__(self, code=0):
        self.stdout = FakeStream(["标准输出\n"])
        self.stderr = FakeStream(["错误输出\n"])
        self.pid = 1234
        self.returncode = code
        self._done = threading.Event()

    def wait(self):
        self._done.wait(1)
        return self.returncode

    def finish(self):
        self._done.set()

    def terminate(self):
        self.finish()


class FailingStream(FakeStream):
    """迭代输出时抛出异常的模拟流。"""

    def __iter__(self):
        yield "开始输出\n"
        raise OSError("读取失败")


class FailingStopProcess(FakeProcess):
    """停止操作失败的模拟进程。"""

    def terminate(self):
        raise OSError("停止失败")


@pytest.fixture
def manager(tmp_path):
    """创建使用临时程序目录的控制器。"""
    return PushProcessManager(program_dir=tmp_path)


def test_reader_failure_marks_task_failed_and_is_not_overwritten(
        monkeypatch, manager, tmp_path):
    """读取输出异常应标记任务失败，即使进程退出码为零也不能覆盖。"""
    process = FakeProcess(code=0)
    process.stdout = FailingStream([])
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    manager.start_file_push(tmp_path / "a.json", tmp_path / "c.ini")
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()

    assert manager.get_status()["status"] == "failed"


def test_reader_failure_still_allows_stop_until_task_finishes(
        monkeypatch, manager, tmp_path):
    """reader 失败后进程仍活动时，stop 应终止进程并保留 stopped 状态。"""
    process = FakeProcess(code=0)
    process.stdout = FailingStream([])
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "posix")
    manager.start_file_push(tmp_path / "a.json", tmp_path / "c.ini")

    manager._reader_threads[0].join(1)
    assert manager.get_status()["status"] == "failed"
    assert manager.stop() is True
    assert manager.get_status()["status"] == "stopped"
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()


def test_cleanup_failure_does_not_leave_task_thread_unhandled(
        monkeypatch, manager, tmp_path):
    """临时文件清理异常不得让收尾线程失控或改变任务状态。"""
    process = FakeProcess(code=0)
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(Path, "unlink", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("清理失败")))
    manager.start_payload_push({"title": "内容"}, tmp_path / "配置.ini")
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()

    assert manager.get_status()["status"] == "success"
    assert manager._wait_thread is not None


def test_stop_failure_marks_task_failed(monkeypatch, manager, tmp_path):
    """非 Windows 停止异常应报告失败而不是伪装为已停止。"""
    process = FailingStopProcess()
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "posix")
    manager.start_file_push(tmp_path / "a.json", tmp_path / "c.ini")

    assert manager.stop() is False
    assert manager.get_status()["status"] == "failed"
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()


def test_payload_file_is_atomic_before_process_start(monkeypatch, manager, tmp_path):
    """子进程启动时只能看到写入完成的最终临时文件。"""
    process = FakeProcess()
    observed = {}

    def fake_popen(command, **kwargs):
        """检查启动瞬间的临时文件内容。"""
        observed["files"] = list((tmp_path / "Temp").glob("*.json"))
        observed["content"] = observed["files"][0].read_text(encoding="utf-8")
        return process

    monkeypatch.setattr("modules.push_process.subprocess.Popen", fake_popen)
    manager.start_payload_push({"title": "内容"}, tmp_path / "配置.ini")

    assert observed["content"] == '{\n  "title": "内容"\n}'
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()


def test_log_messages_use_message_field_and_sequence_is_global(
        monkeypatch, manager, tmp_path):
    """不同推送任务的日志应使用统一字段并保持全局序号递增。"""
    first = FakeProcess()
    second = FakeProcess()
    processes = iter([first, second])
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: next(processes))

    manager.start_file_push(tmp_path / "a.json", tmp_path / "c.ini")
    first.finish()
    manager._wait_thread.join(1)
    first_outputs = manager.get_status()['outputs']

    manager.start_file_push(tmp_path / "b.json", tmp_path / "c.ini")
    second.finish()
    manager._wait_thread.join(1)
    second_outputs = manager.get_status()['outputs']

    assert all('message' in item and 'text' not in item for item in first_outputs + second_outputs)
    assert [item['sequence'] for item in first_outputs] == [1, 2]
    assert [item['sequence'] for item in second_outputs] == [3, 4]
    assert manager.get_status(cursor=2)['outputs'] == second_outputs
def test_start_file_push_builds_cli_without_modifying_source(monkeypatch, manager, tmp_path):
    """文件推送应使用当前解释器调用 TwoPush.py 并保留源文件。"""
    push_file = tmp_path / "推送 配置.json"
    config_file = tmp_path / "配置.ini"
    push_file.write_text('{"title":"标题"}', encoding="utf-8")
    config_file.write_text("[Push]\n", encoding="utf-8")
    process = FakeProcess()
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr("modules.push_process.subprocess.Popen", fake_popen)
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    task_id = manager.start_file_push(push_file, config_file)
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()

    assert calls[0][0] == [sys.executable, str(tmp_path / "TwoPush.py"), "-c", str(config_file), "-p", str(push_file)]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"
    assert calls[0][1]["bufsize"] == 1
    assert calls[0][1]["env"]["PYTHONUNBUFFERED"] == "1"
    assert calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert push_file.read_text(encoding="utf-8") == '{"title":"标题"}'
    assert manager.get_status()["task_id"] == task_id
    assert manager.get_status()["status"] == "success"
    assert [item["sequence"] for item in manager.get_status()["outputs"]] == [1, 2]


def test_start_payload_push_names_and_cleans_temp_file(monkeypatch, manager, tmp_path):
    """载荷推送应保留中文空格、替换非法字符并在结束后清理文件。"""
    process = FakeProcess()
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    task_id = manager.start_payload_push({"title": "内容"}, tmp_path / "配置.ini", "中文 配置:测试")
    temp_file = tmp_path / "Temp" / "Temp_中文 配置_测试.json"
    assert temp_file.exists()
    assert json.loads(temp_file.read_text(encoding="utf-8")) == {"title": "内容"}
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()
    assert manager.get_status()["task_id"] == task_id
    assert not temp_file.exists()


def test_manager_rejects_concurrent_task_and_stop_prefers_stopped(monkeypatch, manager, tmp_path):
    """运行中只能有一个任务，停止状态应覆盖进程退出竞态。"""
    process = FakeProcess(code=0)
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "nt")
    kill_calls = []

    def fake_run(*args, **kwargs):
        """记录 Windows 进程树终止调用。"""
        kill_calls.append((args, kwargs))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("modules.push_process.subprocess.run", fake_run)
    manager.start_file_push(tmp_path / "a.json", tmp_path / "c.ini")
    with pytest.raises(RuntimeError):
        manager.start_file_push(tmp_path / "b.json", tmp_path / "c.ini")
    manager.stop()
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()
    assert manager.get_status()["status"] == "stopped"
    assert kill_calls[0][0][0] == ["taskkill", "/PID", "1234", "/T", "/F"]
    assert kill_calls[0][1]["check"] is False


def test_stop_success_shutdown_timeout_preserves_stopped_and_defers_cleanup(
        monkeypatch, manager, tmp_path):
    """成功停止后 shutdown 超时仍保持 stopped，临时文件延后清理。"""
    process = FakeProcess()
    release_reader = threading.Event()
    process.stdout = BlockingStream(release_reader)
    process.stderr = BlockingStream(release_reader)
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "posix")
    manager.start_payload_push({'title': '内容'}, tmp_path / '配置.ini')
    temp_file = next((tmp_path / 'Temp').glob('*.json'))

    manager.shutdown()

    assert manager.get_status()['status'] == 'stopped'
    assert manager._wait_thread.is_alive()
    assert temp_file.exists()

    release_reader.set()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()
    assert not temp_file.exists()


def test_stop_then_immediate_start_waits_for_previous_task_cleanup(
        monkeypatch, manager, tmp_path):
    """停止后旧任务未收尾时不得启动新任务。"""
    first = FakeProcess()
    second = FakeProcess()
    processes = iter([first, second])
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: next(processes))
    monkeypatch.setattr("modules.push_process.os.name", "posix")
    manager.start_file_push(tmp_path / "a.json", tmp_path / "c.ini")
    assert manager.stop() is True
    with pytest.raises(RuntimeError):
        manager.start_file_push(tmp_path / "b.json", tmp_path / "c.ini")
    first.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()


def test_reader_updates_only_its_own_task():
    """旧任务 reader 不能把输出写入当前新任务。"""
    manager = PushProcessManager()
    old_task = {'task_id': 'old', 'status': 'stopped', 'exit_code': 0, 'outputs': [], 'reader_failed': False}
    new_task = {'task_id': 'new', 'status': 'running', 'exit_code': None, 'outputs': [], 'reader_failed': False}
    manager._task = new_task
    manager._read_stream(FakeStream(['旧任务\n']), 'stdout', old_task)
    assert new_task['outputs'] == []
    assert old_task['outputs'][0]['message'] == '旧任务'


def test_reader_failure_isolated_to_its_own_task():
    """旧任务 reader 异常不能改变新任务状态。"""
    manager = PushProcessManager()
    old_task = {'task_id': 'old', 'status': 'running', 'exit_code': None, 'outputs': [], 'reader_failed': False}
    new_task = {'task_id': 'new', 'status': 'running', 'exit_code': None, 'outputs': [], 'reader_failed': False}
    manager._task = new_task
    manager._read_stream(FailingStream([]), 'stderr', old_task)
    assert old_task['status'] == 'failed'
    assert old_task['reader_failed'] is True
    assert new_task['status'] == 'running'


def test_shutdown_returns_when_stop_fails_and_cleans_temp(monkeypatch, manager, tmp_path):
    """停止失败时 shutdown 也应有限返回并清理临时文件。"""
    process = FailingStopProcess()
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "posix")
    manager.start_payload_push({'title': '内容'}, tmp_path / '配置.ini')
    temp_file = next((tmp_path / 'Temp').glob('*.json'))
    manager.shutdown()
    assert manager.get_status()['status'] == 'failed'
    assert not temp_file.exists()
    assert not manager._wait_thread.is_alive()


def test_shutdown_timeout_defers_temp_cleanup_until_wait_thread_finishes(
        monkeypatch, manager, tmp_path):
    """shutdown 超时时不得在 reader/wait 线程结束前删除临时文件。"""
    process = FakeProcess()
    release_reader = threading.Event()
    process.stdout = BlockingStream(release_reader)
    process.stderr = BlockingStream(release_reader)
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "posix")
    manager.start_payload_push({'title': '内容'}, tmp_path / '配置.ini')
    temp_file = next((tmp_path / 'Temp').glob('*.json'))

    manager.shutdown()

    assert manager.get_status()['status'] == 'stopped'
    assert manager._wait_thread.is_alive()
    assert temp_file.exists()

    release_reader.set()
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()
    assert not temp_file.exists()


def test_shutdown_does_not_repeat_stop_after_explicit_stop(manager, monkeypatch):
    """显式停止后 shutdown 不应再次触发停止副作用。"""
    manager._stop_requested = True
    monkeypatch.setattr(manager, 'stop', lambda: (_ for _ in ()).throw(RuntimeError('重复停止')))

    manager.shutdown()


def test_build_command_uses_short_options(manager, tmp_path):
    """CLI 命令应支持配置和推送参数的短参数。"""
    command = manager._build_command(tmp_path / "push.json", tmp_path / "config.ini")
    assert command[-4:] == ["-c", str(tmp_path / "config.ini"), "-p", str(tmp_path / "push.json")]


def test_payload_temp_file_uses_exclusive_collision_suffix(monkeypatch, manager, tmp_path):
    """临时文件已存在时应使用独占创建和后缀文件。"""
    existing = tmp_path / "Temp" / "Temp_push.json"
    existing.parent.mkdir()
    existing.write_text("旧内容", encoding="utf-8")
    process = FakeProcess()
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    manager.start_payload_push({"title": "新内容"}, tmp_path / "配置.ini", "push")
    created = tmp_path / "Temp" / "Temp_push_1.json"
    assert existing.read_text(encoding="utf-8") == "旧内容"
    assert json.loads(created.read_text(encoding="utf-8")) == {"title": "新内容"}
    process.finish()
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()


def test_payload_write_failure_cleans_temp_file(monkeypatch, manager, tmp_path):
    """载荷写入失败时应清理已创建的临时文件。"""
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("写入失败")))
    with pytest.raises(OSError):
        manager.start_payload_push({"title": "内容"}, tmp_path / "配置.ini")
    assert not list((tmp_path / "Temp").glob("*.json"))


def test_wait_failure_marks_failed_and_cleans_temp(monkeypatch, manager, tmp_path):
    """等待进程异常时应标记失败并清理临时文件。"""
    process = FakeProcess()
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(process, "wait", lambda: (_ for _ in ()).throw(OSError("等待失败")))
    manager.start_payload_push({"title": "内容"}, tmp_path / "配置.ini")
    manager._wait_thread.join(1)
    assert not manager._wait_thread.is_alive()
    assert manager.get_status()["status"] == "failed"
    assert not list((tmp_path / "Temp").glob("*.json"))


def test_shutdown_waits_for_task_and_is_idempotent(monkeypatch, manager, tmp_path):
    """关闭应等待任务线程完成并确保停止状态，重复停止无副作用。"""
    process = FakeProcess()
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "posix")
    terminate_calls = []
    monkeypatch.setattr(process, "terminate", lambda: (terminate_calls.append(True), process.finish()))
    manager.start_payload_push({"title": "内容"}, tmp_path / "配置.ini")
    assert manager.stop() is True
    manager.shutdown()
    assert manager.stop() is False
    assert manager.get_status()["status"] == "stopped"
    assert terminate_calls == [True]
    assert not list((tmp_path / "Temp").glob("*.json"))
