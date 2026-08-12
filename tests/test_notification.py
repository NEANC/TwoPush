#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""notification 模块单元测试"""

import base64
import hashlib
import hmac
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

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


def test_send_notification_summary_shows_route_labels(monkeypatch, caplog):
    """推送汇总日志应为钉钉通道标注 builtin/onepush 路由标识"""
    import modules.notification as notification

    monkeypatch.setattr(notification, '_notify_single_channel', lambda *args: True)

    with caplog.at_level('INFO'):
        result = notification.send_notification(
            title='标题',
            content='正文',
            channels=[
                {'provider': 'dingtalk', 'token': 'tok'},
                {'provider': 'dingtalk', 'token': 'tok', 'msgtype': 'markdown'},
            ],
        )

    assert result == [('dingtalk', True), ('dingtalk', True)]
    assert '共 2 个推送通道: dingtalk(onepush), dingtalk(builtin)' in caplog.text


def test_send_notification_summary_keeps_other_provider(monkeypatch, caplog):
    """推送汇总日志应为非钉钉渠道保留原 provider 名"""
    import modules.notification as notification

    monkeypatch.setattr(notification, '_notify_single_channel', lambda *args: True)

    with caplog.at_level('INFO'):
        result = notification.send_notification(
            title='标题',
            content='正文',
            channels=[{'provider': 'serverchan', 'sckey': 'SCTx'}],
        )

    assert result == [('serverchan', True)]
    assert '共 1 个推送通道: serverchan' in caplog.text


def test_describe_channel_routes_returns_route_labels():
    """describe_channel_routes 应为各通道返回对应路由标识"""
    import modules.notification as notification

    routes = notification.describe_channel_routes([
        {'provider': 'dingtalk'},
        {'provider': 'dingtalk', 'msgtype': 'markdown'},
        {'provider': 'serverchan', 'sckey': 'SCTx'},
        {},
    ])

    assert routes == ['dingtalk(onepush)', 'dingtalk(builtin)', 'serverchan', '?']


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


@pytest.mark.parametrize("enhanced", [False, True])
@pytest.mark.parametrize(
    ("token", "error_text"),
    [
        ("http://oapi.dingtalk.com/robot/send?access_token=abc", "HTTPS"),
        ("ht\ntps://oapi.dingtalk.com/robot/send?access_token=abc", "控制字符"),
        ("https://user@oapi.dingtalk.com/robot/send?access_token=abc", "用户信息"),
        ("https://oapi.dingtalk.com:444/robot/send?access_token=abc", "443"),
        ("https://oapi.dingtalk.com:bad/robot/send?access_token=abc", "端口"),
    ],
)
def test_invalid_full_url_is_rejected_before_both_dingtalk_routes(
    monkeypatch, enhanced, token, error_text
):
    """完整 URL 配置错误应在两条钉钉路由发送前记录一次且不重试。"""
    import modules.notification as notification
    from unittest import mock

    send_calls = []

    def record_notifier(provider):
        send_calls.append(('onepush', provider))
        raise AssertionError("HTTP URL 不应调用 get_notifier")

    def record_webhook(*args, **kwargs):
        send_calls.append(('builtin', args, kwargs))
        raise AssertionError("HTTP URL 不应调用内置 Webhook")

    monkeypatch.setattr(notification, 'get_notifier', record_notifier)
    monkeypatch.setattr(notification, '_send_dingtalk_webhook', record_webhook)

    channel = {'provider': 'dingtalk', 'token': token}
    if enhanced:
        channel['msgtype'] = 'markdown'
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        channel,
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert error_text in log.error.call_args[0][0]
    assert send_calls == []


@pytest.mark.parametrize("enhanced", [False, True])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_unicode_surrogate_is_rejected_before_both_dingtalk_routes(
    monkeypatch, enhanced, surrogate
):
    """Unicode Cs 配置错误应在两条钉钉路由发送前记录一次且不重试。"""
    import modules.notification as notification
    from unittest import mock

    send_calls = []

    def record_notifier(provider):
        send_calls.append(('onepush', provider))
        raise AssertionError("Unicode 代理字符不应调用 get_notifier")

    def record_webhook(*args, **kwargs):
        send_calls.append(('builtin', args, kwargs))
        raise AssertionError("Unicode 代理字符不应调用内置 Webhook")

    monkeypatch.setattr(notification, 'get_notifier', record_notifier)
    monkeypatch.setattr(notification, '_send_dingtalk_webhook', record_webhook)

    channel = {'provider': 'dingtalk', 'token': f'abc{surrogate}123'}
    if enhanced:
        channel['msgtype'] = 'markdown'
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        channel,
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert '代理字符' in log.error.call_args[0][0]
    assert send_calls == []


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


def test_other_provider_value_error_still_retries(monkeypatch):
    """非钉钉渠道 notify 阶段抛 ValueError（如 SMTP 断连）时仍应按 max_count 重试"""
    import modules.notification as notification
    from unittest import mock

    def fail_notify(*args, **kwargs):
        raise ValueError('SMTPServerDisconnected 临时断连')

    monkeypatch.setattr(notification, 'get_notifier', lambda provider: type(
        'Notifier', (), {'notify': fail_notify}
    )())
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'smtp', 'host': 'smtp.example.com'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 3


def test_enhanced_dingtalk_value_error_still_retries(monkeypatch):
    """增强路径 _send_dingtalk_webhook 抛 ValueError（如响应解析失败）时仍应按 max_count 重试"""
    import modules.notification as notification
    from unittest import mock

    def fail_webhook(*args, **kwargs):
        raise ValueError('请求响应解析失败')

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


@pytest.mark.parametrize("enhanced", [False, True])
def test_signed_dingtalk_url_capacity_error_is_not_retried(
        monkeypatch, enhanced):
    """两条钉钉路由均应按最坏签名长度前置拒绝超限 URL。"""
    import modules.notification as notification
    from unittest import mock

    prefix = 'https://oapi.dingtalk.com/robot/send?access_token='
    token = prefix + 'a' * (
        notification.DINGTALK_WEBHOOK_MAX_URL_LENGTH - len(prefix)
    )
    channel = {
        'provider': 'dingtalk',
        'token': token,
        'secret': 'SECtest',
    }
    if enhanced:
        channel['msgtype'] = 'markdown'

    send_calls = []
    sign_calls = []

    def make_sign(secret):
        """记录容量校验不应触发的动态签名。"""
        sign_calls.append(secret)
        return '1', 's' * 44

    monkeypatch.setattr(notification, '_make_dingtalk_sign', make_sign)
    monkeypatch.setattr(
        notification,
        '_send_dingtalk_webhook',
        lambda *args, **kwargs: send_calls.append('builtin'),
    )
    monkeypatch.setattr(
        notification,
        'get_notifier',
        lambda provider: send_calls.append('onepush'),
    )
    sleep = mock.MagicMock()
    monkeypatch.setattr(notification.time, 'sleep', sleep)
    log = mock.MagicMock()

    result = notification._notify_single_channel(
        channel,
        '标题',
        '内容',
        retry_interval=1,
        max_count=3,
        log=log,
    )

    assert result is False
    assert send_calls == []
    assert sign_calls == []
    assert log.error.call_count == 1
    assert sleep.call_count == 0


def _signed_dingtalk_boundary_base_url(notification, final_length):
    """按独立编码结果构造指定最终长度的签名基础 URL。"""
    timestamp = '9' * 13
    sign = '/' * 42 + '8' + '='
    prefix = (
        f'{notification.DINGTALK_WEBHOOK_BASE_URL}'
        '?access_token=abc&padding='
    )
    signed_suffix = '&' + urlencode([
        ('timestamp', timestamp),
        ('sign', sign),
    ])
    padding_length = (
        final_length - len(prefix) - len(signed_suffix)
    )
    return prefix + 'a' * padding_length


def test_dingtalk_webhook_max_url_length_contract():
    """钉钉 Webhook URL 长度上限应保持为稳定契约。"""
    from modules.notification import DINGTALK_WEBHOOK_MAX_URL_LENGTH

    assert DINGTALK_WEBHOOK_MAX_URL_LENGTH == 8192


@pytest.mark.parametrize('enhanced', [False, True])
def test_signed_dingtalk_secret_surrogate_encoding_failure_is_not_retried(
        monkeypatch, enhanced):
    """真实 secret 代理字符编码失败应分类为配置错误且不重试。"""
    import modules.notification as notification
    from unittest import mock

    secret = 'SEC-real-private-\ud800-\x01'
    channel = {
        'provider': 'dingtalk',
        'token': 'abc',
        'secret': secret,
    }
    if enhanced:
        channel['msgtype'] = 'markdown'

    sign = mock.MagicMock(wraps=notification._make_dingtalk_sign)
    builtin_send = mock.MagicMock()
    get_notifier = mock.MagicMock()
    sleep = mock.MagicMock()
    monkeypatch.setattr(notification, '_make_dingtalk_sign', sign)
    monkeypatch.setattr(notification, '_send_dingtalk_webhook', builtin_send)
    monkeypatch.setattr(notification, 'get_notifier', get_notifier)
    monkeypatch.setattr(notification.time, 'sleep', sleep)
    log = mock.MagicMock()

    result = notification._notify_single_channel(
        channel, '标题', '内容', retry_interval=1, max_count=3, log=log,
    )

    assert result is False
    sign.assert_called_once_with(secret)
    builtin_send.assert_not_called()
    get_notifier.assert_not_called()
    assert sleep.call_count == 0
    assert log.error.call_count == 1
    log_message = log.error.call_args.args[0]
    assert '配置错误' in log_message
    assert secret not in log_message


@pytest.mark.parametrize('enhanced', [False, True])
def test_signed_dingtalk_signer_output_over_44_character_contract_final_length_validation_is_not_retried(
        monkeypatch, enhanced):
    """签名器返回超出 44 字符契约时最终长度校验不应重试。"""
    import modules.notification as notification
    from unittest import mock

    secret = 'SEC-private-anomalous-signer'
    channel = {
        'provider': 'dingtalk',
        'token': _signed_dingtalk_boundary_base_url(
            notification,
            notification.DINGTALK_WEBHOOK_MAX_URL_LENGTH,
        ),
        'secret': secret,
    }
    if enhanced:
        channel['msgtype'] = 'markdown'

    sign = mock.MagicMock(return_value=('9' * 13, 's' * 133))
    builtin_send = mock.MagicMock()
    get_notifier = mock.MagicMock()
    sleep = mock.MagicMock()
    monkeypatch.setattr(notification, '_make_dingtalk_sign', sign)
    monkeypatch.setattr(notification, '_send_dingtalk_webhook', builtin_send)
    monkeypatch.setattr(notification, 'get_notifier', get_notifier)
    monkeypatch.setattr(notification.time, 'sleep', sleep)
    log = mock.MagicMock()

    result = notification._notify_single_channel(
        channel, '标题', '内容', retry_interval=1, max_count=3, log=log,
    )

    assert result is False
    sign.assert_called_once_with(secret)
    builtin_send.assert_not_called()
    get_notifier.assert_not_called()
    sleep.assert_not_called()
    log.error.assert_called_once()
    log_message = log.error.call_args.args[0]
    assert '配置错误' in log_message
    assert '长度不得超过' in log_message
    assert str(notification.DINGTALK_WEBHOOK_MAX_URL_LENGTH) in log_message
    assert secret not in log_message


@pytest.mark.parametrize('final_length', [8191, 8192])
def test_signed_dingtalk_capacity_accepts_valid_worst_base64_boundary(
        monkeypatch, final_length):
    """合法最坏 Base64 签名的 8191/8192 字节边界应通过容量预算。"""
    import modules.notification as notification

    sign = '/' * 42 + '8' + '='
    assert len(sign) == 44
    assert len(base64.b64decode(sign, validate=True)) == 32
    assert base64.b64encode(base64.b64decode(sign)).decode('ascii') == sign

    base_url = _signed_dingtalk_boundary_base_url(
        notification,
        final_length,
    )
    assert notification._validate_dingtalk_signed_url_capacity(base_url) == base_url
    monkeypatch.setattr(
        notification,
        '_make_dingtalk_sign',
        lambda secret: ('9' * 13, sign),
    )

    signed_url = notification._build_dingtalk_webhook_url(base_url, 'SECtest')

    assert len(signed_url.encode('ascii')) == final_length


def test_signed_dingtalk_capacity_rejects_valid_worst_base64_over_limit():
    """合法最坏 Base64 签名超过容量一个字节时应拒绝。"""
    import modules.notification as notification

    base_url = _signed_dingtalk_boundary_base_url(
        notification,
        notification.DINGTALK_WEBHOOK_MAX_URL_LENGTH + 1,
    )

    with pytest.raises(ValueError, match='长度不得超过'):
        notification._validate_dingtalk_signed_url_capacity(base_url)


def test_signed_dingtalk_capacity_uses_current_timestamp_length(monkeypatch):
    """时间戳超过 13 位后容量预算不得低估实际长度。"""
    import modules.notification as notification

    base_url = _signed_dingtalk_boundary_base_url(
        notification,
        notification.DINGTALK_WEBHOOK_MAX_URL_LENGTH,
    )
    monkeypatch.setattr(notification.time, 'time', lambda: 10_000_000_000)

    with pytest.raises(ValueError, match='长度不得超过'):
        notification._validate_dingtalk_signed_url_capacity(base_url)


@pytest.mark.parametrize('enhanced', [False, True])
def test_signed_dingtalk_timestamp_grows_between_capacity_check_and_signing(
        monkeypatch, enhanced):
    """预检后时间戳跨位应由最终长度校验拒绝且不发送或重试。"""
    import modules.notification as notification
    from unittest import mock

    secret = 'SEC-cross-stage-boundary-7'
    timestamp_13 = '1000000000000'
    timestamp_14 = '10000000000000'
    sign_13 = base64.b64encode(hmac.new(
        secret.encode(),
        f'{timestamp_13}\n{secret}'.encode(),
        digestmod=hashlib.sha256,
    ).digest()).decode('ascii')
    sign_14 = base64.b64encode(hmac.new(
        secret.encode(),
        f'{timestamp_14}\n{secret}'.encode(),
        digestmod=hashlib.sha256,
    ).digest()).decode('ascii')
    limit = notification.DINGTALK_WEBHOOK_MAX_URL_LENGTH
    prefix = (
        f'{notification.DINGTALK_WEBHOOK_BASE_URL}'
        '?access_token=abc&padding='
    )
    signed_suffix = '&' + urlencode([
        ('timestamp', timestamp_13),
        ('sign', sign_13),
    ])
    padding_length = limit - len(prefix) - len(signed_suffix)
    base_url = prefix + 'a' * padding_length
    channel = {
        'provider': 'dingtalk',
        'token': base_url,
        'secret': secret,
    }
    if enhanced:
        channel['msgtype'] = 'markdown'

    returned_times = []

    def advancing_time():
        """首次返回 13 位毫秒时间戳，后续稳定返回 14 位。"""
        value = 1_000_000_000 if not returned_times else 10_000_000_000
        returned_times.append(value)
        return value

    time_call = mock.MagicMock(side_effect=advancing_time)
    hmac_new = mock.MagicMock(wraps=notification.hmac.new)
    builtin_send = mock.MagicMock()
    get_notifier = mock.MagicMock()
    sleep = mock.MagicMock()
    monkeypatch.setattr(notification.time, 'time', time_call)
    monkeypatch.setattr(notification, 'DINGTALK_SIGN_WORST_BASE64', sign_13)
    monkeypatch.setattr(notification.hmac, 'new', hmac_new)
    monkeypatch.setattr(notification, '_send_dingtalk_webhook', builtin_send)
    monkeypatch.setattr(notification, 'get_notifier', get_notifier)
    monkeypatch.setattr(notification.time, 'sleep', sleep)
    log = mock.MagicMock()

    assert len((base_url + signed_suffix).encode('ascii')) == limit
    actual_signed_suffix = '&' + urlencode([
        ('timestamp', timestamp_14),
        ('sign', sign_14),
    ])
    assert len((base_url + actual_signed_suffix).encode('ascii')) == limit + 1

    result = notification._notify_single_channel(
        channel, '标题', '内容', retry_interval=1, max_count=3, log=log,
    )

    assert result is False
    timestamps = [str(round(value * 1000)) for value in returned_times]
    assert len(timestamps) >= 2
    assert len(timestamps[0]) == 13
    assert any(len(timestamp) == 14 for timestamp in timestamps[1:])
    hmac_new.assert_called_once_with(
        secret.encode(),
        f'{timestamp_14}\n{secret}'.encode(),
        digestmod=hashlib.sha256,
    )
    builtin_send.assert_not_called()
    get_notifier.assert_not_called()
    log.error.assert_called_once()
    log_message = log.error.call_args.args[0]
    assert '配置错误' in log_message
    assert '长度不得超过' in log_message
    assert str(limit) in log_message
    sleep.assert_not_called()


@pytest.mark.parametrize("enhanced", [False, True])
def test_signed_dingtalk_retries_with_fresh_matching_url(
        monkeypatch, enhanced):
    """每次重试应重签一次并把当次构造对象原样交给对应 transport。"""
    import modules.notification as notification
    from unittest import mock

    channel = {
        'provider': 'dingtalk',
        'token': (
            'HTTPS://OAPI.DINGTALK.COM:443/robot/send?'
            'access_token=abc&timestamp=old&sign=old'
        ),
        'secret': 'SECtest',
    }
    if enhanced:
        channel['msgtype'] = 'markdown'
    original = dict(channel)
    signatures = iter([
        ('1000000000001', 'first/sign='),
        ('1000000000002', 'second+sign='),
        ('1000000000003', 'third/sign='),
    ])
    sign_calls = []
    built_signed_urls = []
    sent_urls = []
    real_builder = notification._build_dingtalk_webhook_url

    def make_sign(secret):
        """记录签名次数并为重复调用返回不同结果。"""
        sign_calls.append(secret)
        return next(signatures)

    def build_url(token, secret=None):
        """记录每次动态签名构造返回的对象。"""
        url = real_builder(token, secret)
        if secret:
            built_signed_urls.append(url)
        return url

    def failed_response():
        """构造可重试的钉钉失败响应。"""
        response = mock.MagicMock(status_code=200)
        response.json.return_value = {'errcode': 1, 'errmsg': 'retry'}
        return response

    def fake_direct(params, title, content, validated_url=None):
        """记录 builtin 实际发送的 URL 对象。"""
        sent_urls.append(validated_url)
        return failed_response()

    class FakeNotifier:
        """记录 OnePush 实际发送参数的测试替身。"""

        def notify(self, **kwargs):
            """保存 token 对象并确认 secret 已移除。"""
            assert 'secret' not in kwargs
            sent_urls.append(kwargs['token'])
            return failed_response()

    monkeypatch.setattr(notification, '_make_dingtalk_sign', make_sign)
    monkeypatch.setattr(notification, '_build_dingtalk_webhook_url', build_url)
    monkeypatch.setattr(notification, '_send_dingtalk_webhook', fake_direct)
    monkeypatch.setattr(
        notification, 'get_notifier', lambda provider: FakeNotifier())

    result = notification._notify_single_channel(
        channel, '标题', '内容', retry_interval=0, max_count=3,
        log=mock.MagicMock(),
    )

    assert result is False
    assert sign_calls == ['SECtest'] * 3
    assert len(built_signed_urls) == 3
    assert len(sent_urls) == 3
    assert all(sent is built for sent, built in zip(sent_urls, built_signed_urls))
    assert len(set(sent_urls)) == 3
    for index, url in enumerate(sent_urls, start=1):
        query_pairs = parse_qsl(urlsplit(url).query)
        assert query_pairs.count(('timestamp', f'100000000000{index}')) == 1
        assert len([value for key, value in query_pairs if key == 'sign']) == 1
        assert all(value != 'old' for key, value in query_pairs
                   if key in ('timestamp', 'sign'))
    assert channel == original


def test_unsigned_dingtalk_reuses_short_base_url_for_retries(monkeypatch):
    """无 secret 的正常短 URL 应只构造一次并在重试时复用。"""
    import modules.notification as notification
    from unittest import mock

    channel = {
        'provider': 'dingtalk',
        'token': 'short-token',
    }
    original = dict(channel)
    build_calls = []
    sent_urls = []
    real_builder = notification._build_dingtalk_webhook_url

    def build_url(token, secret=None):
        """记录无签名基础 URL 的构造次数。"""
        build_calls.append((token, secret))
        return real_builder(token, secret)

    class FakeNotifier:
        """记录 OnePush 重试 URL 的测试替身。"""

        def notify(self, **kwargs):
            """保存 token 并返回可重试失败响应。"""
            sent_urls.append(kwargs['token'])
            response = mock.MagicMock(status_code=200)
            response.json.return_value = {'errcode': 1, 'errmsg': 'retry'}
            return response

    monkeypatch.setattr(notification, '_build_dingtalk_webhook_url', build_url)
    monkeypatch.setattr(
        notification, 'get_notifier', lambda provider: FakeNotifier())

    result = notification._notify_single_channel(
        channel, '标题', '内容', retry_interval=0, max_count=3,
        log=mock.MagicMock(),
    )

    assert result is False
    assert build_calls == [('short-token', None)]
    assert len(sent_urls) == 3
    assert all(url is sent_urls[0] for url in sent_urls)
    assert sent_urls[0] == (
        'https://oapi.dingtalk.com/robot/send?access_token=short-token'
    )
    assert channel == original


def test_config_error_value_error_reason_is_masked(monkeypatch):
    """钉钉前置配置校验抛出的 ValueError 文本含敏感值时，配置错误日志应脱敏"""
    import modules.notification as notification
    from unittest import mock

    def boom_url(token, secret=None):
        raise ValueError('bad access_token=super-secret')

    monkeypatch.setattr(notification, '_build_dingtalk_webhook_url', boom_url)
    log = mock.MagicMock()
    result = notification._notify_single_channel(
        {'provider': 'dingtalk', 'token': 'tok'},
        '标题',
        '内容',
        retry_interval=0,
        max_count=3,
        log=log,
    )

    assert result is False
    assert log.error.call_count == 1
    assert 'super-secret' not in log.error.call_args[0][0]
    assert 'access_token=***' in log.error.call_args[0][0]
