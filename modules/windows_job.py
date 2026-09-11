#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows Job Object 和原生进程启动接口。"""

import ctypes
import io
import os
import subprocess
from ctypes import wintypes


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_CREATE_SUSPENDED = 0x00000004
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_INFINITE = 0xFFFFFFFF
_STD_INPUT_HANDLE = -10
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12
_STARTF_USESTDHANDLES = 0x00000100


def _is_windows():
    """返回当前运行平台是否为 Windows。"""
    return os.name == 'nt'


class _StartupInfo(ctypes.Structure):
    """CreateProcessW 的启动信息结构。"""

    _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR),
                ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
                ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
                ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD),
                ('dwXCountChars', wintypes.DWORD), ('dwYCountChars', wintypes.DWORD),
                ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                ('lpReserved2', ctypes.POINTER(ctypes.c_ubyte)),
                ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE),
                ('hStdError', wintypes.HANDLE)]


class _ProcessInformation(ctypes.Structure):
    """CreateProcessW 返回的句柄和 ID。"""

    _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]


class _CtypesBackend:
    """使用 ctypes 调用 Windows API。"""

    def __init__(self):
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._configure()

    def _configure(self):
        """配置 Windows API 签名。"""
        k = self.kernel32
        k.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
                                     wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL,
                                     wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
                                     ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation)]
        k.CreateProcessW.restype = wintypes.BOOL
        k.GetStdHandle.argtypes = [wintypes.DWORD]
        k.GetStdHandle.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID, wintypes.DWORD]
        k.CreatePipe.restype = wintypes.BOOL
        k.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        k.SetHandleInformation.restype = wintypes.BOOL
        k.ResumeThread.argtypes = [wintypes.HANDLE]
        k.ResumeThread.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL

    def create_pipe(self):
        """创建匿名管道并返回读写句柄。"""
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        if not self.kernel32.CreatePipe(ctypes.byref(read_handle), ctypes.byref(write_handle), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return read_handle.value, write_handle.value

    def make_inheritable(self, handle):
        """允许句柄被子进程继承。"""
        if not self.kernel32.SetHandleInformation(handle, 1, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def make_non_inheritable(self, handle):
        """禁止句柄被子进程继承。"""
        if not self.kernel32.SetHandleInformation(handle, 1, 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def handle_stream(self, handle):
        """将 Windows 读句柄包装为二进制文件流。"""
        import msvcrt
        descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY)
        return os.fdopen(descriptor, 'rb', closefd=True)

    def create_process(self, command_line, cwd, environment, creation_flags, stdout_handle, stderr_handle):
        """调用 CreateProcessW，并返回真实句柄和 PID。"""
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = _STARTF_USESTDHANDLES
        startup.hStdInput = self.kernel32.GetStdHandle(_STD_INPUT_HANDLE)
        startup.hStdOutput = self.kernel32.GetStdHandle(_STD_OUTPUT_HANDLE) if stdout_handle is None else wintypes.HANDLE(stdout_handle)
        startup.hStdError = self.kernel32.GetStdHandle(_STD_ERROR_HANDLE) if stderr_handle is None else wintypes.HANDLE(stderr_handle)
        info = _ProcessInformation()
        command_buffer = ctypes.create_unicode_buffer(command_line)
        env_buffer = ctypes.create_unicode_buffer(environment) if environment is not None else None
        ok = self.kernel32.CreateProcessW(None, command_buffer, None, None, True,
                                          creation_flags, env_buffer, cwd,
                                          ctypes.byref(startup), ctypes.byref(info))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return info.hProcess, info.hThread, info.dwProcessId

    def close_handle(self, handle):
        """关闭 Windows 句柄。"""
        if handle:
            self.kernel32.CloseHandle(handle)

    def resume_process(self, handle):
        """恢复挂起主线程。"""
        return self.kernel32.ResumeThread(handle) != 0xFFFFFFFF

    def wait(self, handle, timeout):
        """等待进程句柄。"""
        return self.kernel32.WaitForSingleObject(handle, timeout)

    def exit_code(self, handle):
        """读取进程退出码。"""
        code = wintypes.DWORD()
        if not self.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def terminate_process(self, handle, code):
        """终止进程。"""
        return self.kernel32.TerminateProcess(handle, code)

    def create_job(self):
        """创建 Job Object。"""
        return self.kernel32.CreateJobObjectW(None, None)

    def configure_kill_on_close(self, job_handle):
        """配置 Job 关闭时终止进程。"""
        class Basic(ctypes.Structure):
            _fields_ = [('user', ctypes.c_longlong), ('job', ctypes.c_longlong), ('flags', ctypes.c_uint32),
                        ('min', ctypes.c_size_t), ('max', ctypes.c_size_t), ('active', ctypes.c_uint32),
                        ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32), ('scheduling', ctypes.c_uint32)]

        class Io(ctypes.Structure):
            _fields_ = [('a', ctypes.c_uint64), ('b', ctypes.c_uint64), ('c', ctypes.c_uint64),
                        ('d', ctypes.c_uint64), ('e', ctypes.c_uint64), ('f', ctypes.c_uint64)]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        info = Extended()
        info.basic.flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        return self.kernel32.SetInformationJobObject(job_handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                      ctypes.byref(info), ctypes.sizeof(info))

    def assign_process(self, job_handle, process_handle):
        """将进程加入 Job。"""
        return self.kernel32.AssignProcessToJobObject(job_handle, process_handle)

    def send_ctrl_break(self, process_id):
        """发送 CTRL_BREAK_EVENT。"""
        return self.kernel32.GenerateConsoleCtrlEvent(1, process_id)

    def terminate_job(self, job_handle):
        """终止 Job 中的进程。"""
        return self.kernel32.TerminateJobObject(job_handle, 1)


class WindowsLaunchedProcess:
    """拥有 CreateProcessW 句柄的最小 Popen 兼容包装。"""

    def __init__(self, backend, process_handle, thread_handle, pid, stdout=None, stderr=None):
        self._backend = backend
        self.process_handle = process_handle
        self.thread_handle = thread_handle
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = None

    def poll(self):
        """返回退出码，进程运行时返回 None。"""
        if self.returncode is not None:
            return self.returncode
        if self._backend.wait(self.process_handle, 0) == _WAIT_OBJECT_0:
            self.returncode = self._backend.exit_code(self.process_handle)
        return self.returncode

    def wait(self, timeout=None):
        """等待进程并返回退出码。"""
        milliseconds = _INFINITE if timeout is None else max(0, int(timeout * 1000))
        if self._backend.wait(self.process_handle, milliseconds) == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(None, timeout)
        self.returncode = self._backend.exit_code(self.process_handle)
        return self.returncode

    def kill(self):
        """终止进程。"""
        if self.poll() is None and not self._backend.terminate_process(self.process_handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    terminate = kill

    def close_thread_handle(self):
        """关闭启动线程句柄而保留进程句柄。"""
        if self.thread_handle is not None:
            self._backend.close_handle(self.thread_handle)
            self.thread_handle = None

    def close(self):
        """关闭包装器拥有的全部句柄和流。"""
        self.close_thread_handle()
        if self.process_handle:
            self._backend.close_handle(self.process_handle)
            self.process_handle = None
        for stream_name in ('stdout', 'stderr'):
            stream = getattr(self, stream_name)
            if stream is not None:
                stream.close()
                setattr(self, stream_name, None)


class WindowsProcessLauncher:
    """使用 CreateProcessW 启动挂起进程，避免依赖 Popen 私有属性。"""

    def __init__(self, backend=None):
        self._backend = backend

    def launch(self, command, **kwargs):
        """启动进程并返回 WindowsLaunchedProcess。"""
        if not _is_windows():
            process = subprocess.Popen(command, **kwargs)
            return WindowsLaunchedProcess(_SubprocessBackend(process), process, None, process.pid,
                                          process.stdout, process.stderr)
        backend = self._backend or _CtypesBackend()
        flags = kwargs.pop('creationflags', 0) | _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP
        command_line = subprocess.list2cmdline([os.fspath(item) for item in command])
        env = kwargs.pop('env', None)
        environment = None if env is None else ''.join(f'{key}={env[key]}\x00' for key in sorted(env)) + '\x00'
        if environment is not None:
            flags |= _CREATE_UNICODE_ENVIRONMENT
        cwd = os.fspath(kwargs.pop('cwd', None)) if kwargs.get('cwd') is not None else None
        stdout = kwargs.pop('stdout', None)
        stderr = kwargs.pop('stderr', None)
        kwargs.pop('shell', None)
        kwargs.pop('encoding', None)
        kwargs.pop('errors', None)
        kwargs.pop('universal_newlines', None)
        kwargs.pop('bufsize', None)
        process_handle = thread_handle = None
        pipe_handles = []
        streams = {}
        created_streams = {}
        try:
            for stream_name, value in (('stdout', stdout), ('stderr', stderr)):
                if value == subprocess.PIPE:
                    read_handle, write_handle = backend.create_pipe()
                    pipe_handles.extend((read_handle, write_handle))
                    backend.make_inheritable(write_handle)
                    backend.make_non_inheritable(read_handle)
                    streams[stream_name] = (read_handle, write_handle)
            process_handle, thread_handle, pid = backend.create_process(
                command_line=command_line, cwd=cwd, environment=environment,
                creation_flags=flags,
                stdout_handle=streams.get('stdout', (None, None))[1],
                stderr_handle=streams.get('stderr', (None, None))[1])
            stdout_stream = (backend.handle_stream(streams['stdout'][0])
                             if 'stdout' in streams else stdout)
            if 'stdout' in streams:
                created_streams['stdout'] = stdout_stream
                pipe_handles.remove(streams['stdout'][0])
            stderr_stream = (backend.handle_stream(streams['stderr'][0])
                             if 'stderr' in streams else stderr)
            if 'stderr' in streams:
                created_streams['stderr'] = stderr_stream
                pipe_handles.remove(streams['stderr'][0])
            for read_handle, write_handle in streams.values():
                if write_handle in pipe_handles:
                    backend.close_handle(write_handle)
                    pipe_handles.remove(write_handle)
            return WindowsLaunchedProcess(backend, process_handle, thread_handle, pid,
                                          stdout_stream, stderr_stream)
        except Exception as error:
            for stream in created_streams.values():
                try:
                    stream.close()
                except Exception:
                    pass
            for handle in pipe_handles:
                backend.close_handle(handle)
            if thread_handle:
                backend.close_handle(thread_handle)
            if process_handle:
                backend.terminate_process(process_handle, 1)
                backend.close_handle(process_handle)
            raise error


class _SubprocessBackend:
    """把 POSIX Popen 适配到包装器接口。"""

    def __init__(self, process):
        self.process = process

    def wait(self, handle, timeout):
        """等待 POSIX 进程并返回等待状态。"""
        try:
            self.process.wait(None if timeout == _INFINITE else timeout / 1000)
        except subprocess.TimeoutExpired:
            return _WAIT_TIMEOUT
        return _WAIT_OBJECT_0

    def exit_code(self, handle):
        return self.process.returncode

    def terminate_process(self, handle, code):
        self.process.kill()
        return True

    def close_handle(self, handle):
        return None


class WindowsJob:
    """封装 Windows Job Object；非 Windows 平台不伪装可用。"""

    def __init__(self, backend=None):
        self._handle = None
        self._available = _is_windows() or backend is not None
        self._backend = backend
        if backend is not None:
            self._handle = backend.create_job()
        elif self._available:
            self._backend = _CtypesBackend()
            self._handle = self._backend.create_job()
        if self._available and not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if self._handle and not self._backend.configure_kill_on_close(self._handle):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def _require_available(self):
        """检查 Job 是否可用。"""
        if not self._available:
            raise OSError('Windows Job 仅支持 Windows')

    def assign_process(self, process_handle):
        """将进程句柄加入 Job。"""
        self._require_available()
        if not self._backend.assign_process(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume_process(self, process_handle):
        """恢复挂起线程。"""
        self._require_available()
        if not self._backend.resume_process(process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def send_ctrl_break(self, process_id):
        """向进程组发送 CTRL_BREAK_EVENT。"""
        self._require_available()
        if not self._backend.send_ctrl_break(process_id):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        """终止 Job 中的全部进程。"""
        self._require_available()
        if not self._backend.terminate_job(self._handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        """幂等关闭 Job 句柄。"""
        if self._handle:
            self._backend.close_handle(self._handle)
            self._handle = None
