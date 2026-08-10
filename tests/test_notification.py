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
            channels=[{'provider': 'dingtalk', 'token': 'token-only'}],
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
            channels=[{'provider': 'dingtalk', 'token': 'token-only'}],
            retry_settings={'interval': 0, 'max_count': 1},
        )

    assert result == [('dingtalk', False)]
    assert '通道 [dingtalk(onepush)] 通知发送失败' in caplog.text
    assert '138****8000' in caplog.text
    assert 'access_token=***' in caplog.text
    assert 'sign=***' in caplog.text
    assert 'secret=***' in caplog.text
    assert '13800138000' not in caplog.text
    assert 'access_token=abc' not in caplog.text
    assert '已超过最大重试次数' not in caplog.text


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
            channels=[{'provider': 'dingtalk', 'msgtype': 'markdown', 'token': 'token-only'}],
            retry_settings={'interval': 0, 'max_count': 1},
        )

    assert result == [('dingtalk', False)]
    assert '通道 [dingtalk(builtin)] 通知发送失败' in caplog.text


def test_is_mobile_number_accepts_valid_mobile_segments():
    """11 位且第二位为 3-9 的数字应判定为手机号"""
    from modules.notification import _is_mobile_number

    assert _is_mobile_number('13800138000') is True


def test_is_mobile_number_rejects_other_segments():
    """1 开头但第二位非 3-9 的 11 位数字不应判定为手机号"""
    from modules.notification import _is_mobile_number

    assert _is_mobile_number('12000000000') is False
    assert _is_mobile_number('11000000000') is False
    assert _is_mobile_number('10000000000') is False


def test_is_mobile_number_rejects_non_numeric():
    """非 11 位纯数字字符串不应判定为手机号"""
    from modules.notification import _is_mobile_number

    assert _is_mobile_number('1380013800a') is False
    assert _is_mobile_number('1380013800') is False
    assert _is_mobile_number('') is False


def test_is_mobile_number_accepts_country_code_prefix():
    """带 +86/86 前缀的 11 位手机号应判定为手机号"""
    from modules.notification import _is_mobile_number

    assert _is_mobile_number('+8613800138000') is True
    assert _is_mobile_number('8613800138000') is True
    assert _is_mobile_number('13800138000') is True
    assert _is_mobile_number('+8612000000000') is False
    assert _is_mobile_number('861380013800') is False


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
            retry_settings={'interval': 0, 'max_count': 1},
        )

    assert result == [('serverchan', False)]
    assert '通道 [serverchan] 通知发送失败' in caplog.text
    assert 'dingtalk(onepush)' not in caplog.text
    assert 'dingtalk(builtin)' not in caplog.text


def test_config_error_value_error_is_not_retried():
    """配置性 ValueError（如缺 token）应记录一次错误后直接返回 False，不进入重试"""
    import modules.notification as notification
    from unittest import mock

    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'dingtalk', 'msgtype': 'markdown'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert '缺少 token' in log.error.call_args[0][0]


def test_onepush_path_missing_token_not_retried(monkeypatch):
    """非增强（onepush）路径缺 token 时应前置拦截，不调用 onepush 也不重试"""
    import modules.notification as notification
    from unittest import mock

    def boom_notifier(provider):
        raise AssertionError("缺 token 时不应调用 get_notifier")

    monkeypatch.setattr(notification, 'get_notifier', boom_notifier)
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'dingtalk'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert '缺少 token' in log.error.call_args[0][0]


def test_missing_access_token_in_full_url_not_retried(monkeypatch):
    """完整 Webhook URL 缺少 access_token 时应前置拦截，不调用 onepush 也不重试"""
    import modules.notification as notification
    from unittest import mock

    def boom_notifier(provider):
        raise AssertionError("完整 URL 缺 access_token 时不应调用 get_notifier")

    monkeypatch.setattr(notification, 'get_notifier', boom_notifier)
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'dingtalk', 'token': 'https://oapi.dingtalk.com/robot/send'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert 'access_token' in log.error.call_args[0][0]


def test_blank_token_not_retried(monkeypatch):
    """纯空白 token 应作为配置错误前置拦截，不调用 onepush 也不重试"""
    import modules.notification as notification
    from unittest import mock

    calls = []

    def boom_notifier(provider):
        calls.append(provider)
        raise AssertionError("空白 token 时不应调用 get_notifier")

    monkeypatch.setattr(notification, 'get_notifier', boom_notifier)
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'dingtalk', 'token': '   '},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert '缺少 token' in log.error.call_args[0][0]
    assert calls == []


def test_retryable_exception_still_retries(monkeypatch):
    """普通异常（RuntimeError）仍应按 max_count 次数重试"""
    import modules.notification as notification
    from unittest import mock

    def fail_webhook(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(notification, '_send_dingtalk_webhook', fail_webhook)
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'dingtalk', 'msgtype': 'markdown', 'token': 'abc'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 3


def test_unknown_provider_not_retried(monkeypatch):
    """未知推送渠道（NoSuchNotifierError）应视为配置错误，记录一次后不重试"""
    import modules.notification as notification
    from unittest import mock
    from onepush.exceptions import NoSuchNotifierError

    def unknown_notifier(provider):
        raise NoSuchNotifierError(provider)

    monkeypatch.setattr(notification, 'get_notifier', unknown_notifier)
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'no_such_provider_xyz'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert '未知' in log.error.call_args[0][0]
    assert 'no_such_provider_xyz' in log.error.call_args[0][0]


def test_unknown_provider_runtime_error_still_retries(monkeypatch):
    """get_notifier 抛普通 RuntimeError 时仍应按 max_count 重试（守护 except Exception）"""
    import modules.notification as notification
    from unittest import mock

    def fail_notifier(provider):
        raise RuntimeError('boom')

    monkeypatch.setattr(notification, 'get_notifier', fail_notifier)
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'no_such_provider_xyz'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 3
