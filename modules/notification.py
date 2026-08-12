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
import unicodedata
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

# 钉钉完整 Webhook URL 最大长度
DINGTALK_WEBHOOK_MAX_URL_LENGTH = 8192

# 钉钉 Webhook query 最大参数数量
DINGTALK_WEBHOOK_MAX_QUERY_FIELDS = 100

# 钉钉裸 token 最大长度
DINGTALK_TOKEN_MAX_LENGTH = 4096

# 钉钉 Webhook 请求超时时间（秒）
DINGTALK_REQUEST_TIMEOUT = 10

# HMAC-SHA256 的 32 字节摘要所能产生的最坏规范 Base64 签名
DINGTALK_SIGN_WORST_BASE64 = '/' * 42 + '8' + '='

# 钉钉 timestamp 当前最少预算位数
DINGTALK_TIMESTAMP_MIN_LENGTH = 13


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


def _validate_dingtalk_webhook_url(url):
    """校验钉钉 Webhook 最终 URL 的编码与长度。

    Args:
        url: 经 urlencode 与 urlunsplit 规范化后的 URL

    Returns:
        str: 通过校验的 ASCII URL

    Raises:
        ValueError: URL 无法编码为 ASCII 或长度超过上限时抛出
    """
    try:
        encoded_url = url.encode('ascii')
    except UnicodeEncodeError:
        raise ValueError('钉钉 Webhook 最终 URL 必须为 ASCII 编码') from None
    if len(encoded_url) > DINGTALK_WEBHOOK_MAX_URL_LENGTH:
        raise ValueError(
            '钉钉 Webhook 编码后 URL 长度不得超过 '
            f'{DINGTALK_WEBHOOK_MAX_URL_LENGTH} 个字符'
        )
    return url


def _parse_dingtalk_query(query, error_prefix):
    """严格解析钉钉 Webhook query 并分类转换异常。

    Args:
        query: 待解析的 query 字符串
        error_prefix: 错误消息中的 URL 类型前缀

    Returns:
        list[tuple[str, str]]: 解码后的 query 键值对

    Raises:
        ValueError: query 非合法 UTF-8、参数超过上限或发生其他解析错误
    """
    field_count = query.count('&') + 1 if query else 0
    if field_count > DINGTALK_WEBHOOK_MAX_QUERY_FIELDS:
        raise ValueError(
            f'{error_prefix} query 参数不得超过 '
            f'{DINGTALK_WEBHOOK_MAX_QUERY_FIELDS} 个'
        )
    try:
        return parse_qsl(
            query,
            keep_blank_values=True,
            encoding='utf-8',
            errors='strict',
            max_num_fields=DINGTALK_WEBHOOK_MAX_QUERY_FIELDS,
        )
    except UnicodeDecodeError:
        raise ValueError(
            f'{error_prefix} query 解析失败：必须为合法 UTF-8'
        ) from None
    except ValueError:
        raise ValueError(f'{error_prefix} query 解析失败') from None


def _build_dingtalk_webhook_url(token, secret=None):
    """根据 token 或完整 Webhook URL 构造钉钉请求 URL。

    构造前仅去除 token 首尾普通空格，并拒绝隐藏控制、格式、代理字符及首尾
    其他 Unicode 空白。完整 URL 会规范化 scheme、authority 与 query。

    Args:
        token: 裸 access token 或含 access_token 的完整 Webhook URL
        secret: 可选的加签密钥

    Returns:
        str: 钉钉 Webhook 请求 URL

    Raises:
        ValueError: token 规范化后为空、包含 Unicode 控制、格式或代理字符、首尾
            包含非普通空格的空白时抛出；token 疑似完整 Webhook URL（含协议
            :// 或 query = 特征）但非合法 http(s) URL 时抛出；或完整 Webhook
            URL 未明确以 HTTPS 开头、authority 非法、包含 fragment、缺少
            access_token 参数、域名或路径不符合钉钉规范时抛出
    """
    raw_token = str(token)
    if any(
        unicodedata.category(character) in ('Cc', 'Cf', 'Cs')
        for character in raw_token
    ):
        raise ValueError(
            '钉钉 token 不得包含 Unicode 控制字符、格式字符或代理字符'
        )
    if raw_token and any(
        character != ' ' and character.isspace()
        for character in (raw_token[0], raw_token[-1])
    ):
        raise ValueError('钉钉 token 首尾仅允许普通空格，不得包含其他空白字符')
    token_str = raw_token.strip(' ')
    if not token_str:
        raise ValueError('钉钉通道缺少 token')

    if _is_full_url(token_str):
        if len(token_str) > DINGTALK_WEBHOOK_MAX_URL_LENGTH:
            raise ValueError(
                '完整 Webhook URL 长度不得超过 '
                f'{DINGTALK_WEBHOOK_MAX_URL_LENGTH} 个字符'
            )
        parsed = urlsplit(token_str)
        if not token_str.lower().startswith('https://'):
            raise ValueError('完整 Webhook URL 必须使用 HTTPS')
        if parsed.hostname != DINGTALK_WEBHOOK_HOST:
            raise ValueError(
                f'完整 Webhook URL 必须使用钉钉官方域名 {DINGTALK_WEBHOOK_HOST}'
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError('完整 Webhook URL 不得包含用户信息')
        raw_port = parsed.netloc[len(DINGTALK_WEBHOOK_HOST):]
        if raw_port not in ('', ':443'):
            raise ValueError('完整 Webhook URL 端口仅允许 443')
        if parsed.path != DINGTALK_WEBHOOK_PATH:
            raise ValueError(
                f'完整 Webhook URL 必须使用钉钉标准路径 {DINGTALK_WEBHOOK_PATH}'
            )
        if '#' in token_str:
            raise ValueError('完整 Webhook URL 不得包含 fragment 片段')
        if re.search(r'%(?![0-9A-Fa-f]{2})', parsed.query):
            raise ValueError('完整 Webhook URL query 包含非法百分号转义，必须使用 %HH')
        query_pairs = _parse_dingtalk_query(parsed.query, '完整 Webhook URL')
        if any(
            unicodedata.category(character) in ('Cc', 'Cf', 'Cs')
            for key, value in query_pairs
            for character in key + value
        ):
            raise ValueError(
                '完整 Webhook URL query 解码后不得包含 '
                'Unicode 控制字符、格式字符或代理字符'
            )
        access_tokens = [
            value
            for key, value in query_pairs
            if key == 'access_token'
        ]
        if not any(value.strip() for value in access_tokens):
            raise ValueError('完整 Webhook URL 必须包含 access_token 参数')
        url = urlunsplit((
            'https',
            DINGTALK_WEBHOOK_HOST,
            parsed.path,
            urlencode(query_pairs),
            '',
        ))
    else:
        # 裸 token 分支仅接受纯 access token；含协议（://）或 query（=）特征
        # 的字符串疑似完整 Webhook URL，提前报错避免生成错误 URL
        if '://' in token_str or '=' in token_str:
            raise ValueError(
                '疑似完整 Webhook URL，请使用裸 access token 或完整 HTTPS URL'
            )
        if len(token_str) > DINGTALK_TOKEN_MAX_LENGTH:
            raise ValueError(
                f'钉钉 token 长度不得超过 {DINGTALK_TOKEN_MAX_LENGTH} 个字符'
            )
        query = urlencode({'access_token': token_str})
        url = f'{DINGTALK_WEBHOOK_BASE_URL}?{query}'

    if not secret:
        return _validate_dingtalk_webhook_url(url)

    timestamp, sign = _make_dingtalk_sign(str(secret))
    parsed = urlsplit(url)
    parsed_query_pairs = _parse_dingtalk_query(parsed.query, '钉钉 Webhook URL')
    query_pairs = [
        (key, value)
        for key, value in parsed_query_pairs
        if key not in ('timestamp', 'sign')
    ]
    query_pairs.extend([('timestamp', timestamp), ('sign', sign)])
    url = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urlencode(query_pairs),
        parsed.fragment,
    ))
    return _validate_dingtalk_webhook_url(url)


def _validate_dingtalk_signed_url_capacity(base_url):
    """按钉钉签名最坏编码长度校验基础 URL 容量。

    Args:
        base_url: 不带本次动态签名的规范 Webhook URL

    Returns:
        str: 通过最坏情况长度校验的基础 URL

    Raises:
        ValueError: 替换旧签名并追加最坏编码签名后超过 URL 上限时抛出
    """
    parsed = urlsplit(base_url)
    parsed_query_pairs = _parse_dingtalk_query(
        parsed.query, '钉钉 Webhook URL')
    query_pairs = [
        (key, value)
        for key, value in parsed_query_pairs
        if key not in ('timestamp', 'sign')
    ]
    timestamp_length = max(
        DINGTALK_TIMESTAMP_MIN_LENGTH,
        len(str(round(time.time() * 1000))),
    )
    query_pairs.extend([
        ('timestamp', '9' * timestamp_length),
        ('sign', DINGTALK_SIGN_WORST_BASE64),
    ])
    worst_url = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urlencode(query_pairs),
        parsed.fragment,
    ))
    _validate_dingtalk_webhook_url(worst_url)
    return base_url


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


def _is_dingtalk_mention_token_continuation(character):
    """判断字符是否会延续相邻钉钉提醒 token。

    Args:
        character: 待判断的单个字符

    Returns:
        bool: Unicode 单词字符、组合附加符或格式字符返回 True
    """
    category = unicodedata.category(character)
    return bool(re.match(r'\w', character)) or category.startswith(
        'M') or category == 'Cf'


def _has_independent_dingtalk_mention(text, mention):
    """通过保守文本扫描判断正文是否包含独立钉钉提醒 token。

    本函数不解析 msgtype 或 Markdown 语法。候选 @ 前紧邻任意数量的
    反斜杠时均不视为独立提醒。

    Args:
        text: 消息正文
        mention: 包含 @ 前缀的提醒文本

    Returns:
        bool: 正文包含独立提醒 token 时返回 True
    """
    for match in re.finditer(re.escape(mention), text):
        start = match.start()
        end = match.end()
        if start:
            prefix = text[start - 1]
            if prefix in ('@', '\\') or _is_dingtalk_mention_token_continuation(
                    prefix):
                continue
        if end < len(text) and _is_dingtalk_mention_token_continuation(text[end]):
            continue
        return True
    return False


def _append_missing_dingtalk_mentions(text, mobiles, is_at_all=False):
    """将缺失的 @手机号 与 @所有人 追加到钉钉消息正文。

    钉钉要求 @ 生效时正文必须包含对应的 @文本，故在请求体 at 字段设置
    atMobiles/isAtAll 的同时，需将缺失的 @手机号 与 @所有人 补入正文末尾。

    Args:
        text: 消息正文
        mobiles: 需要 @ 的手机号列表
        is_at_all: 是否启用全员 @；为 True 且正文不含 @所有人 时自动补齐

    Returns:
        str: 补齐缺失 @ 文本后的正文
    """
    missing = [
        mobile for mobile in mobiles
        if not _has_independent_dingtalk_mention(text, f'@{mobile}')
    ]
    if is_at_all and not _has_independent_dingtalk_mention(text, '@所有人'):
        missing.append('所有人')
    if not missing:
        return text
    suffix = ' '.join(f'@{item}' for item in missing)
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
    is_at_all = at.get('isAtAll', False)

    if msgtype == 'text':
        message = '\n\n'.join(part for part in (title, content) if part)
        message = _append_missing_dingtalk_mentions(
            message, mobiles, is_at_all=is_at_all)
        payload = {'msgtype': 'text', 'text': {'content': message}}
    else:
        text = _append_missing_dingtalk_mentions(
            content or '', mobiles, is_at_all=is_at_all)
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


def _send_dingtalk_webhook(channel, title, content, validated_url=None):
    """发送 TwoPush 钉钉增强 Webhook 请求。

    Args:
        channel: 钉钉通道参数字典，需包含 token，可包含 secret 与 msgtype/at
        title: 通知标题
        content: 通知内容
        validated_url: 可选的预构造最终 Webhook URL

    Returns:
        requests.Response: 钉钉 Webhook 响应对象

    Raises:
        ValueError: 通道缺少 token 或最终 URL 未通过校验时抛出
    """
    token = channel.get('token')
    if not token or not str(token).strip():
        raise ValueError("钉钉通道缺少 token")
    url = validated_url
    if url is None:
        url = _build_dingtalk_webhook_url(token, channel.get('secret'))
    else:
        url = _build_dingtalk_webhook_url(url)
    payload = _build_dingtalk_payload(channel, title, content)
    headers = {'Content-Type': 'application/json'}
    return request(
        'post',
        url,
        json=payload,
        headers=headers,
        timeout=DINGTALK_REQUEST_TIMEOUT,
        allow_redirects=False,
    )


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


def describe_channel_routes(channels):
    """描述各推送通道实际使用的路由标识

    Args:
        channels: 标准通道字典列表

    Returns:
        list: 每个通道对应的路由标识列表；钉钉分为 dingtalk(builtin)
            与 dingtalk(onepush)，其他渠道保留原 provider，缺失时为 '?'
    """
    return [
        _describe_channel_route(
            channel.get('provider', '?'),
            _is_enhanced_dingtalk_channel(
                channel.get('provider', '?'),
                {key: value for key, value in channel.items() if key != 'provider'},
            ),
        )
        for channel in channels
    ]


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


def _disable_dingtalk_redirects(notifier):
    """为钉钉 OnePush 实例注入强制禁用重定向的请求包装。

    OnePush 的 Provider.request 是 @staticmethod，经实例访问得到不绑定
    self 的底层函数，实例可安全覆盖。包装函数在转发前强制覆盖
    allow_redirects，避免上游调用时重新开启重定向。wrapped 作为实例属性
    赋值后，经 self.request(...) 访问同样不绑定 self，与 @staticmethod 的
    original_request 语义一致，这是正确透传位置参数（method 不会错位）
    的关键依赖。

    Args:
        notifier: OnePush 通知器实例

    Returns:
        bool: 成功注入返回 True；实例缺少可调用 request 时返回 False
    """
    original_request = getattr(notifier, 'request', None)
    if not callable(original_request):
        return False

    def wrapped(method, url, *args, **kwargs):
        """转发请求并强制将 allow_redirects 置为 False。

        使用 *args 跨版本透传位置参数，兼容 OnePush 1.2.0~1.5.0 的两位置
        参数（method、url）签名与 1.6.0+ 的三位置参数（method、url、
        proxies）签名，避免被误简化为具名参数 proxies。
        """
        kwargs['allow_redirects'] = False
        return original_request(method, url, *args, **kwargs)

    notifier.request = wrapped
    return True


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
    base_url = None
    secret = None

    # 配置性校验：钉钉通道必须有 token，且完整 Webhook URL 必须含 access_token；
    # 增强直发/onepush 两种路径统一在此拦截，避免对配置错误做无意义重试
    if str(provider).strip().lower() == 'dingtalk':
        token = params.get('token')
        if not token or not str(token).strip():
            log.error(f"通道 [{route_label}] 配置错误: 钉钉通道缺少 token")
            return False
        try:
            base_url = _build_dingtalk_webhook_url(str(token))
            secret = params.get('secret')
            if secret:
                _validate_dingtalk_signed_url_capacity(base_url)
        except ValueError as e:
            reason = mask_sensitive_fields(
                {'reason': str(e)}, sensitive_fields={'reason'}
            )['reason']
            log.error(f"通道 [{route_label}] 配置错误: {reason}")
            return False

    for attempt in range(1, max_count + 1):
        try:
            final_url = base_url
            if base_url is not None and secret:
                try:
                    final_url = _build_dingtalk_webhook_url(base_url, secret)
                except ValueError as e:
                    reason = mask_sensitive_fields(
                        {'reason': str(e)}, sensitive_fields={'reason'}
                    )['reason']
                    log.error(f"通道 [{route_label}] 配置错误: {reason}")
                    return False
            if enhanced:
                response = _send_dingtalk_webhook(
                    params, title, content, validated_url=final_url)
            else:
                notifier = get_notifier(provider)
                if str(provider).strip().lower() == 'dingtalk':
                    if not _disable_dingtalk_redirects(notifier):
                        reason = mask_sensitive_fields(
                            {'reason': '无法为钉钉 OnePush 实例安全禁用自动重定向'},
                            sensitive_fields={'reason'},
                        )['reason']
                        log.error(f"通道 [{route_label}] 配置错误: {reason}")
                        return False
                send_params = params
                if final_url is not None:
                    send_params = dict(params)
                    send_params['token'] = final_url
                    send_params.pop('secret', None)
                response = notifier.notify(
                    title=title, content=content, **send_params)
        except NoSuchNotifierError as e:
            # 未知推送渠道属于配置性错误，重试无意义，立即返回 False
            reason = mask_sensitive_fields(
                {'reason': f'未知推送渠道 {provider}'}, sensitive_fields={'reason'}
            )['reason']
            log.error(f"通道 [{route_label}] 配置错误: {reason}")
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
    route_labels = describe_channel_routes(channels)
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
