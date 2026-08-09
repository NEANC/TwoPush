#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""基于 onepush 的通知推送模块，支持多通道并发发送和自动重试"""

import base64
import datetime
import hashlib
import hmac
import logging
import socket
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from onepush import get_notifier

LOGGER = logging.getLogger(__name__)

# 触发 TwoPush 钉钉直发增强路径的参数键（任一存在即走增强路径）
DINGTALK_ENHANCED_KEYS = {
    'msgtype',
    'at',
    'at_mobiles',
    'atMobiles',
    'is_at_all',
    'isAtAll',
}

# 钉钉自定义机器人 Webhook 基础地址
DINGTALK_WEBHOOK_BASE_URL = 'https://oapi.dingtalk.com/robot/send'


def _is_full_url(value):
    """判断字符串是否为完整 URL。"""
    parsed = urlsplit(str(value))
    return bool(parsed.scheme and parsed.netloc)


def _make_dingtalk_sign(secret):
    """生成钉钉加签所需的 timestamp 与 sign。"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def _build_dingtalk_webhook_url(token, secret=None):
    """根据 token 或完整 Webhook URL 构造钉钉请求 URL。"""
    if _is_full_url(token):
        url = str(token)
    else:
        query = urlencode({'access_token': str(token)})
        url = f'{DINGTALK_WEBHOOK_BASE_URL}?{query}'

    if not secret:
        return url

    timestamp, sign = _make_dingtalk_sign(str(secret))
    parsed = urlsplit(url)
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in ('timestamp', 'sign')
    ]
    query_pairs.extend([('timestamp', timestamp), ('sign', sign)])
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urlencode(query_pairs),
        parsed.fragment,
    ))


def _is_enhanced_dingtalk_channel(provider, params):
    """判断钉钉通道是否需要使用 TwoPush 直发增强路径

    Args:
        provider: 推送渠道名称
        params: 通道参数字典

    Returns:
        bool: 需要走增强路径时返回 True
    """
    if str(provider).strip().lower() != 'dingtalk':
        return False
    return any(key in params for key in DINGTALK_ENHANCED_KEYS)


def _send_dingtalk_webhook(channel, title, content):
    """发送 TwoPush 钉钉增强 Webhook 请求

    Args:
        channel: 钉钉通道参数字典
        title: 通知标题
        content: 通知内容

    Returns:
        requests.Response: 钉钉接口响应对象

    Raises:
        NotImplementedError: 增强直发功能尚未实现
    """
    raise NotImplementedError("钉钉增强直发尚未实现")


def _parse_response_body(response):
    """尝试将响应体解析为 JSON 字典

    Args:
        response: requests.Response 对象

    Returns:
        dict | None: 解析成功返回字典；无法解析或非字典返回 None
    """
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return body


def _is_push_successful(response):
    """判定 onepush 返回的响应是否代表推送成功

    Args:
        response: onepush notify() 的返回值

    Returns:
        tuple[bool, str]: (是否成功, 失败原因描述)
    """
    if response is None:
        return False, "未收到响应，请求可能已失败"

    status_code = getattr(response, 'status_code', None)
    if status_code is not None and not 200 <= status_code < 300:
        text = (getattr(response, 'text', '') or '').strip()
        return False, f"HTTP {status_code}: {text}"

    body = _parse_response_body(response)
    if body is None:
        return True, ""

    errcode = body.get('errcode')
    if errcode is not None and errcode != 0:
        return False, f"errcode={errcode}: {body.get('errmsg', '')}"

    code = body.get('code')
    if code is not None and code not in (0, 200):
        reason = body.get('message') or body.get('reason') or body.get('info') or ''
        return False, f"code={code}: {reason}"

    if body.get('success') is False:
        reason = body.get('reason') or body.get('message') or ''
        return False, f"success=false: {reason}"

    return True, ""


def _handle_attempt_failure(provider, attempt, max_count, reason, retry_interval, log):
    """记录单次发送失败并决定是否继续重试

    Args:
        provider: 推送渠道名称
        attempt: 当前尝试序号（从 1 开始）
        max_count: 最大重试次数
        reason: 失败原因描述
        retry_interval: 重试间隔（秒）
        log: 日志记录器

    Returns:
        bool: True 表示应继续重试
    """
    log.error(
        f"通道 [{provider}] 通知发送失败 (尝试 {attempt}/{max_count}): {reason}"
    )
    if attempt < max_count:
        time.sleep(retry_interval)
        return True
    log.warning(
        f"通道 [{provider}] 通知发送失败，已超过最大重试次数"
    )
    return False


def _notify_single_channel(channel, title, content, retry_interval, max_count, log):
    """向单个推送通道发送通知，失败时按配置重试

    Args:
        channel: 标准通道字典，含 provider 及该渠道所需参数
        title: 通知标题
        content: 通知内容
        retry_interval: 重试间隔（秒）
        max_count: 最大重试次数
        log: 日志记录器

    Returns:
        bool: 是否发送成功
    """
    params = dict(channel)
    provider = params.pop('provider', '')

    if not provider:
        log.error("推送通道缺少 provider 键，已跳过该通道")
        return False

    for attempt in range(1, max_count + 1):
        try:
            if _is_enhanced_dingtalk_channel(provider, params):
                response = _send_dingtalk_webhook(params, title, content)
            else:
                notifier = get_notifier(provider)
                response = notifier.notify(title=title, content=content, **params)
        except Exception as e:
            if not _handle_attempt_failure(
                    provider, attempt, max_count, str(e), retry_interval, log):
                return False
            continue

        success, reason = _is_push_successful(response)
        if success:
            log.info(f"通知发送成功 [{provider}]: {title}")
            return True

        if not _handle_attempt_failure(
                provider, attempt, max_count, reason, retry_interval, log):
            return False
    return False


def render_template_vars():
    """获取模板渲染变量

    Returns:
        dict: host_name、current_time、short_current_time
    """
    now = datetime.datetime.now()
    return {
        'host_name': socket.gethostname(),
        'current_time': now.strftime('%Y/%m/%d %H:%M:%S'),
        'short_current_time': now.strftime('%H:%M:%S'),
    }


def send_notification(title, content, channels, retry_settings=None, logger=None):
    """向指定通道发送通知

    Args:
        title: 通知标题（已渲染）
        content: 通知内容（已渲染）
        channels: 标准通道字典列表（已由 parse_push_channels 解析）
        retry_settings: 可选，{interval: int秒, max_count: int}，默认 3s/3次
        logger: 可选，日志记录器

    Returns:
        list[tuple[str, bool]]: 各通道推送结果，元素为 (provider, 是否成功)
    """
    log = logger or LOGGER
    retry = retry_settings or {}
    retry_interval = int(retry.get('interval', 3))
    max_count = max(int(retry.get('max_count', 3)), 1)

    if not channels:
        log.error("推送通道为空，无法发送通知")
        return []

    channel_names = ', '.join(c.get('provider', '?') for c in channels)
    log.info(f"共 {len(channels)} 个推送通道: {channel_names}")
    log.info(f"通知标题: {title}")
    log.debug(f"通知内容长度: {len(content)}")

    max_workers = min(len(channels), 8)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            (
                channel.get('provider', '?'),
                executor.submit(
                    _notify_single_channel,
                    channel, title, content,
                    retry_interval, max_count, log,
                ),
            )
            for channel in channels
        ]
        results = [(provider, future.result()) for provider, future in futures]
    return results
