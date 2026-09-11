#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows Job 接口契约测试。"""

import os

import pytest

from modules.windows_job import WindowsJob


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
