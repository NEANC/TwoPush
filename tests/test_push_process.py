#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""CLI 推送进程控制器测试。"""

import json
import os
import sys
import threading
import time
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


@pytest.fixture
def manager(tmp_path):
    """创建使用临时程序目录的控制器。"""
    return PushProcessManager(program_dir=tmp_path)


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
    task_id = manager.start_file_push(push_file, config_file)
    process.finish()
    time.sleep(0.05)

    assert calls[0][0] == [sys.executable, str(tmp_path / "TwoPush.py"), "--config", str(config_file), "--push", str(push_file)]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"
    assert calls[0][1]["bufsize"] == 1
    assert calls[0][1]["env"]["PYTHONUNBUFFERED"] == "1"
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
    time.sleep(0.05)
    assert manager.get_status()["task_id"] == task_id
    assert not temp_file.exists()


def test_manager_rejects_concurrent_task_and_stop_prefers_stopped(monkeypatch, manager, tmp_path):
    """运行中只能有一个任务，停止状态应覆盖进程退出竞态。"""
    process = FakeProcess(code=0)
    monkeypatch.setattr("modules.push_process.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("modules.push_process.os.name", "nt")
    kill_calls = []
    monkeypatch.setattr("modules.push_process.subprocess.run", lambda *args, **kwargs: kill_calls.append((args, kwargs)))
    manager.start_file_push(tmp_path / "a.json", tmp_path / "c.ini")
    with pytest.raises(RuntimeError):
        manager.start_file_push(tmp_path / "b.json", tmp_path / "c.ini")
    manager.stop()
    process.finish()
    time.sleep(0.05)
    assert manager.get_status()["status"] == "stopped"
    assert kill_calls[0][0][0] == ["taskkill", "/PID", "1234", "/T", "/F"]
