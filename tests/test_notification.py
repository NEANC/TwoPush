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


def test_send_notification_success_does_not_log_title(monkeypatch, caplog):
    """成功发送不应输出成功日志与独立标题日志"""
    import modules.notification as notification

    class DummyResponse:
        status_code = 200
        text = 'ok'

        def json(self):
            return {'errcode': 0, 'errmsg': 'ok'}

    monkeypatch.setattr(notification, 'get_notifier', lambda provider: type(
        'Notifier', (), {'notify': lambda self=None, **kwargs: DummyResponse()}
    )())

    with caplog.at_level('INFO'):
        result = notification.send_notification(
            title='# 测试推送',
            content='正文',
            channels=[{'provider': 'dingtalk'}],
        )

    assert result == [('dingtalk', True)]
    assert '通知发送成功 [dingtalk]' not in caplog.text
    assert '通知标题:' not in caplog.text
    assert '# 测试推送' not in caplog.text


def test_send_notification_failure_logs_branch_tag_and_masks_reason(monkeypatch, caplog):
    """失败日志应区分路由分支并对失败原因脱敏"""
    import modules.notification as notification

    def fail_notify(*args, **kwargs):
        raise RuntimeError('手机号 13800138000 access_token=abc sign=xyz secret=SECa')

    monkeypatch.setattr(notification, 'get_notifier', lambda provider: type(
        'Notifier', (), {'notify': fail_notify}
    )())

    with caplog.at_level('ERROR'):
        result = notification.send_notification(
            title='标题',
            content='正文',
            channels=[{'provider': 'dingtalk'}],
        )

    assert result == [('dingtalk', False)]
    assert '通道 [dingtalk(onepush)] 通知发送失败' in caplog.text
    assert '138****8000' in caplog.text
    assert 'access_token=***' in caplog.text
    assert 'sign=***' in caplog.text
    assert 'secret=***' in caplog.text
    assert '13800138000' not in caplog.text
    assert 'access_token=abc' not in caplog.text


def test_builtin_dingtalk_failure_logs_builtin_branch(monkeypatch, caplog):
    """内置直发路径失败应输出 builtin 分支标识"""
    import modules.notification as notification

    def fail_webhook(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(notification, '_send_dingtalk_webhook', fail_webhook)

    with caplog.at_level('ERROR'):
        result = notification.send_notification(
            title='标题',
            content='正文',
            channels=[{'provider': 'dingtalk', 'msgtype': 'markdown'}],
        )

    assert result == [('dingtalk', False)]
    assert '通道 [dingtalk(builtin)] 通知发送失败' in caplog.text


def test_other_provider_failure_keeps_original_provider(monkeypatch, caplog):
    """其他渠道失败应保留原 provider 作为路由标识"""
    import modules.notification as notification

    def fail_send(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(notification, 'get_notifier', lambda provider: type(
        'Notifier', (), {'notify': fail_send}
    )())

    with caplog.at_level('ERROR'):
        result = notification.send_notification(
            title='标题',
            content='正文',
            channels=[{'provider': 'serverchan'}],
        )

    assert result == [('serverchan', False)]
    assert '通道 [serverchan] 通知发送失败' in caplog.text
    assert 'dingtalk(onepush)' not in caplog.text
    assert 'dingtalk(builtin)' not in caplog.text
