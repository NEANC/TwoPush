#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows Job Object 的跨平台安全接口。"""

import ctypes
import os


def _is_windows():
    """返回当前运行平台是否为 Windows。"""
    return os.name == 'nt'


class _CtypesBackend:
    """使用 ctypes 调用 Windows Job 和进程控制 API。"""

    def __init__(self):
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.ntdll = ctypes.WinDLL('ntdll', use_last_error=True)

    def create_job(self):
        """创建 Job Object。"""
        return self.kernel32.CreateJobObjectW(None, None)

    def assign_process(self, job_handle, process_handle):
        """把进程加入 Job Object。"""
        return self.kernel32.AssignProcessToJobObject(job_handle, process_handle)

    def resume_process(self, process_handle):
        """恢复挂起进程的全部线程。"""
        return self.ntdll.NtResumeProcess(process_handle) == 0

    def send_ctrl_break(self, process_id):
        """向进程组发送 CTRL_BREAK_EVENT。"""
        return self.kernel32.GenerateConsoleCtrlEvent(1, process_id)

    def terminate_job(self, job_handle):
        """终止 Job 中的进程。"""
        return self.kernel32.TerminateJobObject(job_handle, 1)

    def close_handle(self, handle):
        """关闭系统句柄。"""
        return self.kernel32.CloseHandle(handle)


class WindowsJob:
    """封装 Windows Job Object；非 Windows 平台不伪装可用。"""

    def __init__(self, backend=None):
        """创建 Job 包装对象，并允许测试注入后端。"""
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
        """恢复挂起的 Windows 进程。"""
        self._require_available()
        if not self._backend.resume_process(process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def send_ctrl_break(self, process_id):
        """尝试向进程组发送 CTRL_BREAK_EVENT。"""
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
