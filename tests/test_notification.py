#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""notification 模块单元测试"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.notification import send_notification


class RecordingExecutor:
    """记录 ThreadPoolExecutor 初始化参数的测试执行器"""

    created_max_workers = []

    def __init__(self, max_workers=None):
        self.created_max_workers.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def submit(self, func, *args, **kwargs):
        class ImmediateFuture:
            """立即返回执行结果的 Future 替身"""

            def result(self):
                """返回任务执行结果"""
                return func(*args, **kwargs)

        return ImmediateFuture()


def test_send_notification_empty_channels_returns_empty():
    """空通道列表应返回空结果"""
    result = send_notification(
        title="test",
        content="test content",
        channels=[],
    )
    assert result == []


def test_send_notification_missing_provider():
    """缺少 provider 键的通道应被跳过"""
    result = send_notification(
        title="test",
        content="test content",
        channels=[{"some_key": "some_value"}],
    )
    assert result[0][1] is False


def test_render_template_vars():
    """模板变量渲染应包含 host_name、current_time、short_current_time"""
    from modules.notification import render_template_vars
    vars_ = render_template_vars()
    assert 'host_name' in vars_
    assert 'current_time' in vars_
    assert 'short_current_time' in vars_
    assert '/' in vars_['current_time']
    assert ':' in vars_['short_current_time']


def test_send_notification_limits_thread_pool_workers(monkeypatch):
    """多通道推送应限制线程池最大并发数"""
    import modules.notification as notification

    RecordingExecutor.created_max_workers.clear()
    monkeypatch.setattr(notification, 'ThreadPoolExecutor', RecordingExecutor)
    monkeypatch.setattr(notification, '_notify_single_channel', lambda *args: True)

    channels = [{'provider': f'provider-{index}'} for index in range(20)]
    result = send_notification(
        title='test',
        content='test content',
        channels=channels,
    )

    assert len(result) == 20
    assert RecordingExecutor.created_max_workers == [8]
