#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""基于 onepush 的通知推送模块，支持多通道并发发送和自动重试"""

import base64
import datetime
import hashlib
import hmac
import logging
import re
import requests
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from onepush import get_notifier
from onepush.exceptions import NoSuchNotifierError

from modules.utils import mask_sensitive_fields

# 绑定 requests.request 便于测试时 monkeypatch 替换
request = requests.request

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

# 钉钉 Webhook 官方域名（从 base URL 提取，保持单一数据源）
DINGTALK_WEBHOOK_HOST = urlsplit(DINGTALK_WEBHOOK_BASE_URL).hostname

# 钉钉 Webhook 标准路径（从 base URL 提取，保持单一数据源）
DINGTALK_WEBHOOK_PATH = urlsplit(DINGTALK_WEBHOOK_BASE_URL).path

# 钉钉 Webhook 请求超时时间（秒）
DINGTALK_REQUEST_TIMEOUT = 10


def _is_full_url(value):
    """判断字符串是否为完整的 http(s) Webhook URL。

    Args:
        value: 待判断的字符串

    Returns:
        bool: scheme 为 http/https（大小写不敏感）且 netloc 存在时返回 True
    """
    parsed = urlsplit(str(value))
    return bool(
        parsed.netloc and parsed.scheme.lower() in ('http', 'https')
    )


def _make_dingtalk_sign(secret):
    """生成钉钉加签所需的 timestamp 与 sign。

    返回的 sign 为未编码的原始 base64 字符串，由调用方在拼入 URL query
    时统一 URL 编码，避免双重编码导致服务端校验失败。

    Args:
        secret: 钉钉加签密钥

    Returns:
        tuple[str, str]: (timestamp, sign)，sign 为未 URL 编码的 base64
    """
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).digest()
    sign = base64.b64encode(hmac_code).decode('utf-8')
    return timestamp, sign


def _build_dingtalk_webhook_url(token, secret=None):
    """根据 token 或完整 Webhook URL 构造钉钉请求 URL。

    构造前会对 token 统一 strip 首尾空白，避免空白被 urlencode 编码为
    加号（+）或保留在完整 URL 中导致请求无效。

    Args:
        token: 裸 access token 或含 access_token 的完整 Webhook URL
        secret: 可选的加签密钥

    Returns:
        str: 钉钉 Webhook 请求 URL

    Raises:
        ValueError: token strip 后为空时抛出；token 疑似完整 Webhook URL
            （含协议 :// 或 query = 特征）但非合法 http(s) URL 时抛出；或
            token 为完整 Webhook URL 但缺少 access_token 参数时抛出；或完整
            Webhook URL 的域名不是钉钉官方域名 DINGTALK_WEBHOOK_HOST 时抛出；
            或完整 Webhook URL 的路径不是钉钉标准路径 /robot/send 时抛出
    """
    token_str = str(token).strip()
    if not token_str:
        raise ValueError('钉钉通道缺少 token')

    if _is_full_url(token_str):
        parsed = urlsplit(token_str)
        if parsed.hostname != DINGTALK_WEBHOOK_HOST:
            raise ValueError(
                f'完整 Webhook URL 必须使用钉钉官方域名 {DINGTALK_WEBHOOK_HOST}'
            )
        if parsed.path != DINGTALK_WEBHOOK_PATH:
            raise ValueError(
                f'完整 Webhook URL 必须使用钉钉标准路径 {DINGTALK_WEBHOOK_PATH}'
            )
        access_tokens = [
            value
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key == 'access_token'
        ]
        if not any(value.strip() for value in access_tokens):
            raise ValueError('完整 Webhook URL 必须包含 access_token 参数')
        url = token_str
    else:
        # 裸 token 分支仅接受纯 access token；含协议（://）或 query（=）特征
        # 的字符串疑似完整 Webhook URL，提前报错避免生成错误 URL
        if '://' in token_str or '=' in token_str:
            raise ValueError(
                '疑似完整 Webhook URL，请使用裸 access token 或完整 http(s) URL'
            )
        query = urlencode({'access_token': token_str})
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
    return any(params.get(key) is not None for key in DINGTALK_ENHANCED_KEYS)


def _as_string_list(value):
    """将字符串、数字标量或列表值归一为字符串列表。

    Args:
        value: 字符串、数字（int/float，排除 bool）或字符串列表/元组

    Returns:
        list: 归一化后的字符串列表；None 或无法归一时返回空列表
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [str(value)]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_mobile_number(value):
    """判断字符串是否为带可选 +86/86 前缀的中国大陆手机号（1[3-9] 开头）。

    Args:
        value: 待判断的字符串

    Returns:
        bool: 匹配纯号段或带 +86/86 前缀的 11 位手机号时返回 True
    """
    return bool(re.fullmatch(r'(?:\+?86)?1[3-9]\d{9}', value))


def _normalize_mobile_number(value):
    """将带可选 +86/86 国家码前缀的手机号归一化为 11 位纯号段。

    钉钉 atMobiles 需要纯手机号，不能带国家码前缀，故匹配到前缀时剥除。

    Args:
        value: 待归一化的字符串

    Returns:
        str: 匹配手机号时返回 11 位纯号段；否则原样返回
    """
    match = re.fullmatch(r'(?:\+?86)?(1[3-9]\d{9})', value)
    return match.group(1) if match else value


def _parse_boolean(value):
    """将布尔或字符串形式解析为布尔值。

    Args:
        value: 布尔值或字符串（true/false/1/0/yes/no/on）

    Returns:
        bool: 解析后的布尔值
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ('true', '1', 'yes', 'on')
    return bool(value)


def _normalize_dingtalk_at(params):
    """归一化钉钉 @ 配置，默认仅支持手机号。"""
    mobiles = []
    raw_at = params.get('at')

    if isinstance(raw_at, dict):
        mobiles.extend(_as_string_list(raw_at.get('atMobiles')))
    else:
        mobiles.extend(_as_string_list(raw_at))

    mobiles.extend(_as_string_list(params.get('at_mobiles')))
    mobiles.extend(_as_string_list(params.get('atMobiles')))
    mobiles = [m for m in mobiles if _is_mobile_number(m)]
    mobiles = [_normalize_mobile_number(m) for m in mobiles]

    is_at_all = _parse_boolean(params.get('is_at_all')) or _parse_boolean(params.get('isAtAll'))
    if isinstance(raw_at, dict):
        is_at_all = is_at_all or _parse_boolean(raw_at.get('isAtAll'))
    at = {'isAtAll': is_at_all}
    if mobiles:
        at['atMobiles'] = list(dict.fromkeys(mobiles))
    return at


def _append_missing_dingtalk_mentions(text, mobiles):
    """将缺失的 @手机号 追加到钉钉消息正文。"""
    missing = [mobile for mobile in mobiles if f'@{mobile}' not in text]
    if not missing:
        return text
    suffix = ' '.join(f'@{mobile}' for mobile in missing)
    return f'{text}\n\n{suffix}' if text else suffix


def _build_dingtalk_payload(params, title, content):
    """构造钉钉 text 或 markdown Webhook 请求体

    Args:
        params: 通道参数字典
        title: 通知标题
        content: 通知内容

    Returns:
        dict: 钉钉 Webhook 请求体
    """
    msgtype = str(params.get('msgtype') or 'markdown').strip().lower()
    if msgtype not in ('text', 'markdown'):
        msgtype = 'markdown'

    at = _normalize_dingtalk_at(params)
    mobiles = at.get('atMobiles', [])

    if msgtype == 'text':
        message = '\n\n'.join(part for part in (title, content) if part)
        message = _append_missing_dingtalk_mentions(message, mobiles)
        payload = {'msgtype': 'text', 'text': {'content': message}}
    else:
        text = _append_missing_dingtalk_mentions(content or '', mobiles)
        payload = {
            'msgtype': 'markdown',
            'markdown': {
                'title': title or '',
                'text': text,
            },
        }

    if at.get('isAtAll') or at.get('atMobiles'):
        payload['at'] = at
    return payload


def _send_dingtalk_webhook(channel, title, content):
    """发送 TwoPush 钉钉增强 Webhook 请求。

    Args:
        channel: 钉钉通道参数字典，需包含 token，可包含 secret 与 msgtype/at
        title: 通知标题
        content: 通知内容

    Returns:
        requests.Response: 钉钉 Webhook 响应对象

    Raises:
        ValueError: 通道缺少 token 时抛出
    """
    token = channel.get('token')
    if not token or not str(token).strip():
        raise ValueError("钉钉通道缺少 token")
    url = _build_dingtalk_webhook_url(token, channel.get('secret'))
    payload = _build_dingtalk_payload(channel, title, content)
    headers = {'Content-Type': 'application/json'}
    return request('post', url, json=payload, headers=headers, timeout=DINGTALK_REQUEST_TIMEOUT)


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


def _describe_channel_route(provider, enhanced):
    """描述推送通道实际使用的路由分支

    Args:
        provider: 推送渠道名称
        enhanced: 是否为 TwoPush 钉钉直发增强路径

    Returns:
        str: 路由标识；钉钉分为 dingtalk(builtin) 与 dingtalk(onepush)，
            其他渠道保留原 provider
    """
    if str(provider).strip().lower() == 'dingtalk':
        return 'dingtalk(builtin)' if enhanced else 'dingtalk(onepush)'
    return str(provider)


def _handle_attempt_failure(route_label, attempt, max_count, reason, retry_interval, log):
    """记录单次发送失败并决定是否继续重试

    Args:
        route_label: 通道路由标识（如 dingtalk(builtin)）
        attempt: 当前尝试序号（从 1 开始）
        max_count: 最大重试次数
        reason: 失败原因描述
        retry_interval: 重试间隔（秒）
        log: 日志记录器

    Returns:
        bool: True 表示应继续重试
    """
    reason = mask_sensitive_fields({'reason': reason}, sensitive_fields={'reason'})['reason']
    log.error(
        f"通道 [{route_label}] 通知发送失败 (尝试 {attempt}/{max_count}): {reason}"
    )
    if attempt < max_count:
        time.sleep(retry_interval)
        return True
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

    enhanced = _is_enhanced_dingtalk_channel(provider, params)
    route_label = _describe_channel_route(provider, enhanced)

    # 配置性校验：钉钉通道必须有 token，且完整 Webhook URL 必须含 access_token；
    # 增强直发/onepush 两种路径统一在此拦截，避免对配置错误做无意义重试
    if str(provider).strip().lower() == 'dingtalk':
        token = params.get('token')
        if not token or not str(token).strip():
            log.error(f"通道 [{route_label}] 配置错误: 钉钉通道缺少 token")
            return False
        try:
            _build_dingtalk_webhook_url(str(token))
        except ValueError as e:
            log.error(f"通道 [{route_label}] 配置错误: {e}")
            return False

    for attempt in range(1, max_count + 1):
        try:
            if enhanced:
                response = _send_dingtalk_webhook(params, title, content)
            else:
                notifier = get_notifier(provider)
                response = notifier.notify(title=title, content=content, **params)
        except NoSuchNotifierError as e:
            # 未知推送渠道属于配置性错误，重试无意义，立即返回 False
            log.error(f"通道 [{route_label}] 配置错误: 未知推送渠道 {provider}")
            return False
        except ValueError as e:
            # 兜底：其他配置性 ValueError 重试无意义，立即返回 False
            log.error(f"通道 [{route_label}] 配置错误: {e}")
            return False
        except Exception as e:
            if not _handle_attempt_failure(
                    route_label, attempt, max_count, str(e),
                    retry_interval, log):
                return False
            continue

        success, reason = _is_push_successful(response)
        if success:
            return True

        if not _handle_attempt_failure(
                route_label, attempt, max_count, reason,
                retry_interval, log):
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

    # 汇总日志按通道计算路由标识，与 _notify_single_channel 的增强判定保持一致
    route_labels = [
        _describe_channel_route(
            channel.get('provider', '?'),
            _is_enhanced_dingtalk_channel(
                channel.get('provider', '?'),
                {key: value for key, value in channel.items() if key != 'provider'},
            ),
        )
        for channel in channels
    ]
    channel_names = ', '.join(route_labels)
    log.info(f"共 {len(channels)} 个推送通道: {channel_names}")
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
