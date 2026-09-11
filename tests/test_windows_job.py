#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows Job 接口契约测试。"""

import os
import subprocess
import sys

import pytest

from modules.windows_job import WindowsJob, WindowsProcessLauncher




def test_launcher_uses_create_process_backend_and_preserves_launch_contract(monkeypatch):
    """启动器应传递安全命令行、环境块、目录和创建标志，并返回包装器。"""
    import modules.windows_job as windows_job

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def create_process(self, **kwargs):
            self.calls.append(kwargs)
            return (123, 456, 789)

        def close_handle(self, handle):
            self.calls.append({'close': handle})

    backend = FakeBackend()
    monkeypatch.setattr(windows_job, '_is_windows', lambda: True)
    launcher = WindowsProcessLauncher(backend=backend)
    process = launcher.launch(
        ['python.exe', 'a b.py', '"quoted"'], cwd='C:/work',
        env={'B': '2', 'A': '1'}, stdout=None, stderr=None,
        creationflags=0x204)

    call = backend.calls[0]
    assert call['command_line'] == subprocess.list2cmdline(
        ['python.exe', 'a b.py', '"quoted"'])
    assert call['cwd'] == 'C:/work'
    assert call['creation_flags'] == 0x204
    assert call['environment'].startswith('A=1\x00B=2\x00')
    assert (process.pid, process.process_handle, process.thread_handle) == (789, 123, 456)
    process.close()
    assert {'close': 456} in backend.calls
    assert {'close': 123} in backend.calls


def test_launcher_is_importable_without_windows_backend(monkeypatch):
    """非 Windows 导入不应初始化 WinDLL。"""
    import modules.windows_job as windows_job

    monkeypatch.setattr(windows_job, '_is_windows', lambda: False)
    process = WindowsProcessLauncher().launch([sys.executable, '-c', 'pass'])
    assert process.process_handle is not None
    process.wait()


def test_windows_job_does_not_fake_availability_on_non_windows():
    """非 Windows 平台应明确拒绝 Job 操作。"""
    if os.name == 'nt':
        pytest.skip('当前平台为 Windows')
    job = WindowsJob()
    assert job._available is False
    with pytest.raises(OSError):
        job.assign_process(1)


class FakeBackend:
    """记录 Job 后端调用。"""

    def __init__(self, create_result=99, assign_result=True):
        self.create_result = create_result
        self.assign_result = assign_result
        self.calls = []
        self.configure_result = True
        self.resume_result = True

    def create_job(self):
        self.calls.append(('create_job',))
        return self.create_result

    def configure_kill_on_close(self, job_handle):
        self.calls.append(('configure_kill_on_close', job_handle))
        return self.configure_result

    def assign_process(self, job_handle, process_handle):
        self.calls.append(('assign_process', job_handle, process_handle))
        return self.assign_result

    def resume_process(self, process_handle):
        self.calls.append(('resume_process', process_handle))
        return True

    def send_ctrl_break(self, process_id):
        self.calls.append(('send_ctrl_break', process_id))
        return True

    def terminate_job(self, job_handle):
        self.calls.append(('terminate_job', job_handle))
        return True

    def close_handle(self, handle):
        self.calls.append(('close_handle', handle))
        return True


def test_windows_job_fake_backend_covers_lifecycle_without_platform_patch():
    """注入后端应覆盖创建、加入、恢复、控制台中断、终止和关闭。"""
    backend = FakeBackend()
    job = WindowsJob(backend=backend)
    job.assign_process(123)
    job.resume_process(123)
    job.send_ctrl_break(456)
    job.terminate()
    job.close()
    job.close()
    assert backend.calls == [
        ('create_job',),
        ('configure_kill_on_close', 99),
        ('assign_process', 99, 123),
        ('resume_process', 123),
        ('send_ctrl_break', 456),
        ('terminate_job', 99),
        ('close_handle', 99),
    ]


def test_windows_job_fake_backend_closes_handle_after_assign_failure():
    """加入 Job 失败时调用方仍可关闭已创建的句柄。"""
    backend = FakeBackend(assign_result=False)
    job = WindowsJob(backend=backend)
    with pytest.raises(OSError):
        job.assign_process(123)
    job.close()
    assert ('close_handle', 99) in backend.calls


def test_windows_job_close_is_idempotent():
    """关闭 Job 句柄应幂等。"""
    job = WindowsJob()
    job.close()
    job.close()
