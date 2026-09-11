#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows Job Object 的跨平台安全接口。"""

import ctypes
import os
from ctypes import wintypes


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_BASIC_LIMIT_INFORMATION_SIZE = 40
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_SIZE = 144


def _is_windows():
    """返回当前运行平台是否为 Windows。"""
    return os.name == 'nt'


class _CtypesBackend:
    """使用 ctypes 调用 Windows Job 和进程控制 API。"""

    def __init__(self):
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        self.kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel32.ResumeThread.restype = wintypes.DWORD
        self.kernel32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
        self.kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel32.TerminateJobObject.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def create_job(self):
        """创建 Job Object。"""
        return self.kernel32.CreateJobObjectW(None, None)

    def configure_kill_on_close(self, job_handle):
        """配置 Job 关闭时终止其中的全部进程。"""
        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                        ('PerJobUserTimeLimit', ctypes.c_longlong),
                        ('LimitFlags', ctypes.c_uint32),
                        ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', ctypes.c_uint32),
                        ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', ctypes.c_uint32),
                        ('SchedulingClass', ctypes.c_uint32)]

        class IoCounters(ctypes.Structure):
            _fields_ = [('ReadOperationCount', ctypes.c_uint64),
                        ('WriteOperationCount', ctypes.c_uint64),
                        ('OtherOperationCount', ctypes.c_uint64),
                        ('ReadTransferCount', ctypes.c_uint64),
                        ('WriteTransferCount', ctypes.c_uint64),
                        ('OtherTransferCount', ctypes.c_uint64)]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimitInformation),
                        ('IoInfo', IoCounters),
                        ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]

        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        return self.kernel32.SetInformationJobObject(
            job_handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info))

    def assign_process(self, job_handle, process_handle):
        """把进程加入 Job Object。"""
        return self.kernel32.AssignProcessToJobObject(job_handle, process_handle)

    def resume_process(self, process_handle):
        """使用 ResumeThread 恢复挂起线程句柄。"""
        return self.kernel32.ResumeThread(process_handle) != 0xFFFFFFFF

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
        """创建 Job，并在创建后立即启用关闭时终止。"""
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
        """恢复挂起的 Windows 线程。"""
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
        """幂等关闭 Job 句柄；进程句柄由 Popen 所有。"""
        if self._handle:
            self._backend.close_handle(self._handle)
            self._handle = None
