#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows Job 接口契约测试。"""

import ctypes
import io
import os
import subprocess
import sys

import pytest

from modules.windows_job import WindowsJob, WindowsLaunchedProcess, WindowsProcessLauncher




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

        def create_pipe(self):
            self.calls.append({'pipe': True})
            return 111, 222

        def make_inheritable(self, handle):
            self.calls.append({'inherit': handle})

        def make_non_inheritable(self, handle):
            self.calls.append({'noninherit': handle})

        def handle_stream(self, handle):
            stream = io.BytesIO()
            stream.handle = handle
            original_close = stream.close
            def close():
                self.calls.append(('close', handle))
                original_close()
            stream.close = close
            return stream

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
    assert call['creation_flags'] == 0x204 | windows_job._CREATE_UNICODE_ENVIRONMENT
    assert call['environment'].startswith('A=1\x00B=2\x00')
    assert (process.pid, process.process_handle, process.thread_handle) == (789, 123, 456)
    process.close()
    assert {'close': 456} in backend.calls
    assert {'close': 123} in backend.calls


def test_launcher_wraps_pipe_handles_as_binary_streams_and_closes_failure(monkeypatch):
    """PIPE 应创建可读二进制流，并在启动失败时清理全部句柄。"""
    import modules.windows_job as windows_job

    class PipeBackend:
        def __init__(self):
            self.calls = []

        def create_pipe(self):
            self.calls.append(('pipe',))
            return 11, 12

        def make_inheritable(self, handle):
            self.calls.append(('inherit', handle))

        def make_non_inheritable(self, handle):
            self.calls.append(('noninherit', handle))

        def create_process(self, **kwargs):
            self.calls.append(('create', kwargs))
            return 101, 102, 103

        def close_handle(self, handle):
            self.calls.append(('close', handle))

        def terminate_process(self, handle, code):
            self.calls.append(('terminate', handle, code))
            return True

        def handle_stream(self, handle):
            stream = io.BytesIO()
            original_close = stream.close
            def close():
                self.calls.append(('close', handle))
                original_close()
            stream.close = close
            return stream

    backend = PipeBackend()
    monkeypatch.setattr(windows_job, '_is_windows', lambda: True)
    process = WindowsProcessLauncher(backend).launch(
        ['python.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout.readable() and process.stderr.readable()
    assert not isinstance(process.stdout, int) and not isinstance(process.stderr, int)
    process.close()
    assert ('close', 12) in backend.calls and ('close', 11) in backend.calls
    assert ('close', 102) in backend.calls and ('close', 101) in backend.calls


def test_launcher_closes_created_streams_without_reclosing_transferred_reads(monkeypatch):
    """第二路包装失败时应关闭已创建流，并只关闭仍由启动器拥有的句柄。"""
    import modules.windows_job as windows_job

    class PipeBackend:
        def __init__(self):
            self.calls = []
            self.streams = {}
            self.pipe_count = 0

        def create_pipe(self):
            handles = ((11, 12), (21, 22))[self.pipe_count]
            self.pipe_count += 1
            self.calls.append(('pipe', handles))
            return handles

        def make_inheritable(self, handle):
            self.calls.append(('inherit', handle))

        def make_non_inheritable(self, handle):
            self.calls.append(('noninherit', handle))

        def create_process(self, **kwargs):
            self.calls.append(('create', kwargs))
            return 101, 102, 103

        def close_handle(self, handle):
            self.calls.append(('close_handle', handle))

        def terminate_process(self, handle, code):
            self.calls.append(('terminate', handle, code))
            return True

        def handle_stream(self, handle):
            if handle == 21:
                raise RuntimeError('stderr 包装失败')
            stream = io.BytesIO()
            original_close = stream.close

            def close():
                self.calls.append(('close_stream', handle))
                original_close()

            stream.close = close
            self.streams[handle] = stream
            return stream

    backend = PipeBackend()
    monkeypatch.setattr(windows_job, '_is_windows', lambda: True)

    with pytest.raises(RuntimeError, match='stderr 包装失败'):
        WindowsProcessLauncher(backend).launch(
            ['python.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    assert backend.streams[11].closed
    assert ('close_stream', 11) in backend.calls
    assert ('close_handle', 11) not in backend.calls
    assert ('close_handle', 12) in backend.calls
    assert ('close_handle', 21) in backend.calls
    assert ('close_handle', 22) in backend.calls
    assert ('terminate', 101, 1) in backend.calls
    assert ('close_handle', 101) in backend.calls
    assert ('close_handle', 102) in backend.calls


def test_launcher_keeps_startup_error_when_every_cleanup_operation_fails(monkeypatch):
    """启动异常清理失败时应继续尝试全部资源并保留首因。"""
    import modules.windows_job as windows_job

    class FailingBackend:
        def __init__(self):
            self.calls = []

        def create_pipe(self):
            handles = ((11, 12), (21, 22))[len([call for call in self.calls if call[0] == 'pipe'])]
            self.calls.append(('pipe', handles))
            return handles

        def make_inheritable(self, handle):
            pass

        def make_non_inheritable(self, handle):
            pass

        def create_process(self, **kwargs):
            return 101, 102, 103

        def close_handle(self, handle):
            self.calls.append(('close_handle', handle))
            raise OSError(f'关闭句柄失败: {handle}')

        def terminate_process(self, handle, code):
            self.calls.append(('terminate', handle, code))
            raise OSError('终止失败')

        def handle_stream(self, handle):
            if handle == 21:
                raise RuntimeError('包装首因')
            stream = io.BytesIO()
            original_close = stream.close

            def close():
                self.calls.append(('close_stream', handle))
                original_close()
                raise OSError('关闭流失败')

            stream.close = close
            return stream

    backend = FailingBackend()
    monkeypatch.setattr(windows_job, '_is_windows', lambda: True)

    with pytest.raises(RuntimeError, match='包装首因'):
        WindowsProcessLauncher(backend).launch(
            ['python.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    assert backend.calls.count(('close_stream', 11)) == 1
    assert [call for call in backend.calls if call[0] == 'close_handle'] == [
        ('close_handle', 12), ('close_handle', 21), ('close_handle', 22),
        ('close_handle', 102), ('close_handle', 101)]
    assert backend.calls.count(('terminate', 101, 1)) == 1


def test_launched_process_close_removes_failed_resources_and_is_idempotent():
    """包装器关闭失败时仍应收尾全部资源且重复关闭不重试。"""
    class FailingBackend:
        def __init__(self):
            self.calls = []

        def close_handle(self, handle):
            self.calls.append(('close_handle', handle))
            raise OSError(f'关闭句柄失败: {handle}')

    backend = FailingBackend()
    streams = []
    for name in ('stdout', 'stderr'):
        stream = io.BytesIO()
        original_close = stream.close

        def close(name=name, original_close=original_close):
            streams.append(name)
            original_close()
            raise OSError(f'关闭流失败: {name}')

        stream.close = close
        streams.append(f'{name}:created')
        if name == 'stdout':
            stdout = stream
        else:
            stderr = stream

    process = WindowsLaunchedProcess(backend, 101, 102, 103, stdout, stderr)
    with pytest.raises(OSError, match='102'):
        process.close()
    process.close()

    assert backend.calls == [('close_handle', 102), ('close_handle', 101)]
    assert streams == ['stdout:created', 'stderr:created', 'stdout', 'stderr']
    assert process.thread_handle is None
    assert process.process_handle is None
    assert process.stdout is None
    assert process.stderr is None


def test_launcher_preserves_flags_without_environment(monkeypatch):
    """未传环境时不得追加 Unicode 环境标志。"""
    import modules.windows_job as windows_job
    class Backend:
        def create_process(self, **kwargs):
            self.call = kwargs
            return 1, 2, 3
        def close_handle(self, handle):
            pass
    backend = Backend()
    monkeypatch.setattr(windows_job, '_is_windows', lambda: True)
    process = WindowsProcessLauncher(backend).launch(['x'], creationflags=7)
    assert backend.call['creation_flags'] == 7 | windows_job._CREATE_SUSPENDED | windows_job._CREATE_NEW_PROCESS_GROUP
    assert backend.call['environment'] is None
    process.close()


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
