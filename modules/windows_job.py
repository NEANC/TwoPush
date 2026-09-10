#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Windows Job Object 的跨平台安全接口。"""


class WindowsJob:
    """封装 Windows Job Object；导入阶段不访问平台专属 API。"""

    def __init__(self, *args, **kwargs):
        """创建 Job 包装对象。"""
        self._available = False

    def assign_process(self, process_handle):
        """将进程句柄加入 Job。"""
        raise NotImplementedError

    def close(self):
        """关闭 Job 句柄。"""
        raise NotImplementedError
