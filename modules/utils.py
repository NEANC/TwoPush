#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import math
import re
import urllib.parse

from onepush import all_providers, get_notifier

LOGGER = logging.getLogger(__name__)

# 由程序在发送时自动填充的参数，定位位置参数时需要跳过，避免占用用户参数槽位
PROGRAM_FILLED_PARAMS = {'title', 'content'}

# OnePush 已知推送渠道名单（小写），用于在无参数头写法中定位 provider 名称
KNOWN_PROVIDERS = {str(name).strip().lower() for name in all_providers()}

# 支持剥离的成对包裹引号（直引号与中文弯引号）
QUOTE_PAIRS = {
    "'": "'",
    '"': '"',
    '\u2018': '\u2019',
    '\u201c': '\u201d',
}

# OnePush 各推送渠道密钥参数的别名映射
# 通用写法常用 key，而部分渠道要求特定参数名，此处将其纠正为 OnePush 要求的参数名
CHANNEL_KEY_ALIASES = {
    'serverchan': {'key': 'sckey'},
    'serverchanturbo': {'key': 'sctkey'},
    'pushdeer': {'key': 'pushkey'},
}

# 受限解码的输入长度上限：超过该长度不再做解码检测，直接按原文脱敏
_MAX_DECODE_LENGTH = 4096

# 正则扫描窗口与重叠长度，重叠区用于覆盖窗口边界上的键名和值
_MAX_SCAN_WINDOW = 4096
_SCAN_OVERLAP = 256

# 合法十六进制字符集合，用于识别严格的 %HH 编码序列
_HEX_DIGITS = '0123456789abcdefABCDEF'

# 解码副本中控制字符的占位符（DEL），仅存在于检测副本中，不会写回原文
_CONTROL_PLACEHOLDER = '\x7f'

# 由 %HH 解码出的结构性终止字符集合
# URL query 语义中真正的结构分隔符不会被百分号编码，输入里出现 %2C/%22/%27/
# %7D/%5D 即表示该字符属于值本身；若解码副本中还原为 , " ' } ]，会让
# _SENSITIVE_KEY_VALUE_RE 的 bare_value 组在此提前结束，值的后半段以原文残留，
# 故在检测副本中一并以占位符替代，占位符只存在于检测副本，不会写回原文
# 注意：& 不在本集合内，需保留在解码副本中交由 _QUERY_PARAM_BOUNDARY_RE
# 判定其是否为 query 参数分隔符，以保留 & 之后的非敏感 query 参数
_DECODED_LITERAL_CHARS = '"\'' + ",}]"


def _percent_encoded_variant(ch):
    """单字符的可选百分号编码变体，返回正则片段

    敏感键名中的任意单个字符允许以明文或大写十六进制百分号编码
    （如 %74）形式出现，下划线另支持 %5F；配合 re.IGNORECASE，
    hex 字母大小写（如 %6B 与 %6b）均可匹配

    Args:
        ch (str): 单个字符

    Returns:
        str: 匹配该字符明文或百分号编码形式的正则片段
    """
    if ch == '_':
        return r'(?:_|%5F|%255F)'
    return rf'(?:{re.escape(ch)}|%{ord(ch):02X}|%25{ord(ch):02X})'


def _sensitive_key_regex(name):
    """将敏感键名字符串编译为支持任意单字符百分号编码的正则

    Args:
        name (str): 敏感键名，如 access_token

    Returns:
        str: 键名各字符明文或百分号编码变体拼接而成的正则片段
    """
    return ''.join(_percent_encoded_variant(ch) for ch in name)


# 敏感键名（access_token/token、sign、secret、password、api_key、webhook
# 等常见凭据键，以及 OnePush 各渠道声明的专有凭据参数名）中任意单字符均允许
# 以百分号编码形式出现，access_ 前缀可选，键名字母大小写由 IGNORECASE 折叠
# 注意：device_key 等以 key 结尾的键名必须单独列出，因为正则前置的
# (?<![A-Za-z0-9_]) 断言会让 key 分支在 device_key 中间位置失配；
# lark 的 keyword 是安全校验关键词而非凭据，不在此名单内
_SENSITIVE_KEY_PATTERN = (
    f'(?:{_sensitive_key_regex("access_")})?{_sensitive_key_regex("token")}'
    f'|{_sensitive_key_regex("sign")}'
    f'|{_sensitive_key_regex("secret")}'
    f'|{_sensitive_key_regex("corpsecret")}'
    f'|{_sensitive_key_regex("password")}'
    f'|{_sensitive_key_regex("passwd")}'
    f'|{_sensitive_key_regex("api_key")}'
    f'|{_sensitive_key_regex("apikey")}'
    f'|{_sensitive_key_regex("webhook")}'
    f'|{_sensitive_key_regex("secret_key")}'
    f'|{_sensitive_key_regex("token_key")}'
    f'|{_sensitive_key_regex("auth")}'
    f'|{_sensitive_key_regex("credential")}'
    f'|{_sensitive_key_regex("accesskey")}'
    f'|{_sensitive_key_regex("access_key")}'
    f'|{_sensitive_key_regex("sckey")}'
    f'|{_sensitive_key_regex("sctkey")}'
    f'|{_sensitive_key_regex("pushkey")}'
    f'|{_sensitive_key_regex("cipherkey")}'
    f'|{_sensitive_key_regex("device_keys")}'
    f'|{_sensitive_key_regex("device_key")}'
    f'|{_sensitive_key_regex("key")}'
)

_SENSITIVE_KEY_VALUE_RE = re.compile(
    rf'''
    (?<![A-Za-z0-9_])
    (?P<leading_quote>["'\x7f]?)
    (?P<key>{_SENSITIVE_KEY_PATTERN})
    (?P<trailing_quote>["'\x7f]?)
    (?P<separator>[\s\x7f]*(?:=|:|%3[Dd]|%3[Aa]|%253[Dd]|%253[Aa])[\s\x7f]*)
    (?:
        (?P<double_open>")
        (?P<double_value>(?:[^"\\]|\\[\s\S])*\\?)
        (?P<double_close>"|$)
        |
        (?P<single_open>')
        (?P<single_value>(?:[^'\\]|\\[\s\S])*\\?)
        (?P<single_close>'|$)
        |
        (?P<bare_value>[^"'\s,&}}\]]+)
    )
    ''',
    flags=re.IGNORECASE | re.VERBOSE,
)


# query 串中的 & 参数分隔符：& 之后紧跟“键名 + 分隔符”形态（键名字符为
# 字母数字、下划线、百分号、点、连字符，分隔符为 = / : 或其百分号编码）
# _SENSITIVE_KEY_VALUE_RE 的 bare_value 组已排除 &，正则本身只能匹配到 & 之前；
# 而结尾扫描会继续向后吞字符以覆盖“值内自带 &”的情形（如 secret=abc&def），
# 故需靠本正则区分两类 &：命中即视为参数分隔符，敏感值到 & 之前结束，
# & 及其后的非敏感参数（如 &timestamp=...）原样保留；未命中则 & 属于值本身
_QUERY_PARAM_BOUNDARY_RE = re.compile(
    r'&[A-Za-z0-9_%.\-]+(?:=|:|%3[Dd]|%3[Aa]|%253[Dd]|%253[Aa])',
)


# scheme://user:pass@host 形式的 URL userinfo，脱敏为 ***:***@
# scheme 须以 :// 结尾，故 mailto:、纯文本邮箱（无 scheme 前缀）与
# 路径中的 @ 均不会被误伤
_URL_USERINFO_RE = re.compile(
    r'(?i)(?P<scheme>[a-z][a-z0-9+.-]*://)'
    r'(?P<userinfo>[^/\s:@]+(?::[^/\s@]*)?)(?P<userinfo_end>@|$)',
)


def _replace_url_userinfo(match):
    """将 URL userinfo 替换为 ***:***@，保留 scheme 与 @ 之后的 host 等部分。"""
    return f'{match.group("scheme")}***:***@'


def _replace_sensitive_key_value(match):
    """将匹配到的敏感键值对替换为脱敏形式，引号形式保留原有结构。"""
    groups = match.groupdict()
    prefix = (
        f'{groups["leading_quote"]}{groups["key"]}'
        f'{groups["trailing_quote"]}'
    )
    if groups['double_open']:
        return f'{prefix}{groups["separator"]}"***{groups["double_close"]}'
    if groups['single_open']:
        return f"{prefix}{groups['separator']}'***{groups['single_close']}"
    separator = groups['separator']
    if separator.lstrip().startswith('%'):
        return f'{prefix}{separator}***'
    return f'{prefix}=***'


def _resolve_url_userinfo_match(value, window_start, window_end, match):
    """确认 URL userinfo 的真实结尾，并返回跨窗口替换计划。"""
    absolute_end = window_start + match.end()
    if match.group('userinfo_end') == '@':
        return absolute_end, _replace_url_userinfo(match)

    index = absolute_end
    while index < len(value) and value[index] not in '/\r\n\t ':
        if value[index] == '@':
            return index + 1, _replace_url_userinfo(match)
        index += 1
    return None


def _resolve_sensitive_key_value_match(value, window_start, window_end, match):
    """确认敏感值的真实结尾，并生成覆盖完整跨窗口值的替换文本。"""
    absolute_end = window_start + match.end()
    groups = match.groupdict()
    quote = None
    open_group = None
    if groups['double_open']:
        quote = '"'
        open_group = 'double_open'
    elif groups['single_open']:
        quote = "'"
        open_group = 'single_open'

    if quote is not None:
        if groups[f'{"double" if quote == chr(34) else "single"}_close']:
            return absolute_end, _replace_sensitive_key_value(match)
        index = window_start + match.start(open_group) + 1
        while index < len(value):
            if value[index] == '\\':
                index += 2
                continue
            if value[index] == quote:
                return index + 1, f'{_replace_sensitive_key_value(match)}{quote}'
            index += 1
        return len(value), _replace_sensitive_key_value(match)

    index = absolute_end
    while index < len(value) and value[index] not in '"\'\r\n\t ,}]':
        # & 后紧跟“键名 + 分隔符”时视为 query 参数分隔符，敏感值到此结束，
        # 保留 & 及其后的非敏感参数；否则 & 属于值本身，继续向后扫描
        if value[index] == '&' and _QUERY_PARAM_BOUNDARY_RE.match(value, index):
            break
        index += 1
    return index, _replace_sensitive_key_value(match)


def _bounded_sub(pattern, replacement, value, resolver=None):
    """在受控窗口内执行正则替换，并合并窗口重叠区的重复匹配。"""
    if len(value) <= _MAX_SCAN_WINDOW and resolver is None:
        return pattern.sub(replacement, value)

    matches = {}
    step = _MAX_SCAN_WINDOW - _SCAN_OVERLAP
    for window_start in range(0, len(value), step):
        window_end = min(window_start + _MAX_SCAN_WINDOW, len(value))
        window = value[window_start:window_end]
        core_end = window_end
        if window_end < len(value):
            core_end -= _SCAN_OVERLAP
        for match in pattern.finditer(window):
            start, end = match.span()
            absolute_start = window_start + start
            absolute_end = window_start + end
            if window_start <= absolute_start < core_end:
                replacement_text = replacement(match)
                if resolver is not None:
                    resolved = resolver(value, window_start, window_end, match)
                    if resolved is None:
                        continue
                    absolute_end, replacement_text = resolved
                previous = matches.get(absolute_start)
                if previous is None or absolute_end > previous[0]:
                    matches[absolute_start] = (absolute_end, replacement_text)

    # 结尾扫描可能把后一个匹配的起点纳入前一个匹配的区间（如敏感值内含 &
    # 时继续吞掉后续敏感键值对），仅按起点去重无法发现这类区间重叠，倒序
    # 替换时会重复写入导致畸形输出；故先按起点升序做重叠剔除，保留起点在前
    # 且覆盖范围更大的匹配，使剩余区间互不相交后再倒序替换
    planned = []
    covered_end = 0
    for start in sorted(matches):
        end, replacement_text = matches[start]
        if planned and start < covered_end:
            continue
        planned.append((start, end, replacement_text))
        covered_end = max(covered_end, end)

    result = value
    for start, end, replacement_text in reversed(planned):
        result = result[:start] + replacement_text + result[end:]
    return result


def _percent_decode_once(value):
    """对字符串执行最多一层的受限百分号解码

    仅解码严格合法的 %HH 序列（HH 为大写或小写 hex），非法 %（如
    %ZZ、孤立 %）保持原样；解码结果仅用于正则检测，不替换回原文，
    也不会把解码字节解释为控制字符；输入长度超过 _MAX_DECODE_LENGTH
    或解码无法构成合法 UTF-8 时返回原文，不做解码检测

    Args:
        value (str): 待解码的字符串

    Returns:
        str: 单层解码结果；无法安全解码时返回原字符串
    """
    if len(value) > _MAX_DECODE_LENGTH:
        return value
    try:
        return urllib.parse.unquote(value, errors='strict')
    except (ValueError, KeyError):
        return value


def _decode_once_with_spans(value):
    """单层受限解码，并记录检测副本各字符对应的原文区间

    解码副本与原文长度可能不一致（%HH 三字符压缩为一字符），记录每个
    解码字符对应的原文区间，用于将解码副本上命中的敏感片段映射回原文；
    由 %HH 解码产生的控制字符（如 %0A 换行）以占位符替代，避免裸值
    匹配因换行等提前终止，占位符仅存在于检测副本中，不会写回原文

    Args:
        value (str): 待解码的字符串

    Returns:
        tuple: (decoded, spans)；decoded 为单层解码检测副本，spans 为
            长度等于 len(decoded) 的区间列表，spans[i] = (start, end)
            表示 decoded[i] 对应原文 value[start:end]；解码无变化
            或无法解码时返回 (value, None)
    """
    decoded = _percent_decode_once(value)
    if decoded == value:
        return value, None

    spans = []
    decoded_chars = []
    index = 0
    length = len(value)
    for ch in decoded:
        if (
            index + 2 < length
            and value[index] == '%'
            and value[index + 1] in _HEX_DIGITS
            and value[index + 2] in _HEX_DIGITS
        ):
            # 合法 %HH 序列：可能是单字节 ASCII，也可能是多字节 UTF-8 字符
            start = index
            raw = bytearray()
            target = ch.encode('utf-8')
            while (
                index + 2 < length
                and value[index] == '%'
                and value[index + 1] in _HEX_DIGITS
                and value[index + 2] in _HEX_DIGITS
            ):
                raw.append(int(value[index + 1:index + 3], 16))
                index += 3
                if bytes(raw) == target:
                    break
            # 单字节空白、控制字符或结构性终止字符以占位符替代，避免干扰裸值匹配边界
            if len(target) == 1 and (
                ch.isspace()
                or ord(ch) == 0x7F
                or ch in _DECODED_LITERAL_CHARS
            ):
                decoded_chars.append(_CONTROL_PLACEHOLDER)
            else:
                decoded_chars.append(ch)
            spans.append((start, index))
        else:
            # 普通字符或非法 %：解码前后 1:1 对应
            decoded_chars.append(ch)
            spans.append((index, index + 1))
            index += 1
    return ''.join(decoded_chars), spans


def _decode_limited_with_spans(value, max_depth=2):
    """执行有限层级百分号解码，并保留解码副本到原文的区间映射。"""
    decoded = value
    spans = [(index, index + 1) for index in range(len(value))]
    changed = False

    for _ in range(max_depth):
        next_decoded, next_spans = _decode_once_with_spans(decoded)
        if next_spans is None:
            break
        spans = [
            (spans[start][0], spans[end - 1][1])
            for start, end in next_spans
        ]
        decoded = next_decoded
        changed = True

    return decoded, spans if changed else None


def _original_span_text(original, spans, match, group_name):
    """取解码副本正则匹配分组对应的原文文本片段

    Args:
        original (str): 原文
        spans (list): 解码副本字符到原文区间的映射
        match: 解码副本上的正则匹配对象
        group_name (str): 分组名

    Returns:
        str: 分组对应原文的文本片段；分组未参与匹配时返回空字符串
    """
    start, end = match.span(group_name)
    if start < 0 or end <= start:
        return ''
    return original[spans[start][0]:spans[end - 1][1]]


def _rebuild_sensitive_replacement(original, spans, match):
    """重建解码副本匹配对应的原文形态脱敏文本

    保留键名、引号与分隔符在原文中的编码形态，仅将敏感值替换为
    ***，与 _replace_sensitive_key_value 的替换语义保持一致

    Args:
        original (str): 原文
        spans (list): 解码副本字符到原文区间的映射
        match: 解码副本上 _SENSITIVE_KEY_VALUE_RE 的匹配对象

    Returns:
        str: 原文形态的脱敏文本
    """
    groups = match.groupdict()
    prefix = (
        f'{_original_span_text(original, spans, match, "leading_quote")}'
        f'{_original_span_text(original, spans, match, "key")}'
        f'{_original_span_text(original, spans, match, "trailing_quote")}'
    )
    separator = _original_span_text(original, spans, match, 'separator')
    if groups['double_open'] or groups['single_open']:
        # 编码引号在检测副本中已被替换为占位符，故这两个分组只会匹配到原文中
        # 的字面引号，直接按字面引号形态重建
        quote = '"' if groups['double_open'] else "'"
        close_group = 'double_close' if groups['double_open'] else 'single_close'
        close = _original_span_text(original, spans, match, close_group)
        return f'{prefix}{separator}{quote}***{close}'
    if separator.lstrip().startswith('%'):
        return f'{prefix}{separator}***'
    return f'{prefix}=***'


def _decoded_bare_value_end(decoded, start):
    """在解码副本上确认裸敏感值的真实结尾位置

    _SENSITIVE_KEY_VALUE_RE 的 bare_value 组排除了 &，正则本身只能匹配到
    & 之前，需与原文路径的 _resolve_sensitive_key_value_match 一样从匹配
    结尾继续向后扫描，否则解码副本中 & 之后的值会残留

    Args:
        decoded (str): 解码检测副本
        start (int): 扫描起点，即解码副本上正则匹配的结尾位置

    Returns:
        int: 敏感值在解码副本上的真实结尾位置
    """
    index = start
    while index < len(decoded) and decoded[index] not in '"\'\r\n\t ,}]':
        # & 后紧跟“键名 + 分隔符”时视为 query 参数分隔符，敏感值到此结束；
        # 否则 & 属于值本身，继续向后扫描
        if decoded[index] == '&' and _QUERY_PARAM_BOUNDARY_RE.match(decoded, index):
            break
        index += 1
    return index


def _mask_decoded_variants(original, decoded, spans):
    """在解码副本上检测敏感片段，并按原文形态替换回原文

    解码副本与原文长度不一致，无法直接在原文上执行正则替换，故先在
    解码副本上定位敏感片段（URL userinfo 与敏感键值对），映射回原文
    区间后从后往前替换；与 userinfo 重叠的键值对由 userinfo 规则优先

    Args:
        original (str): 原文
        decoded (str): 单层解码副本
        spans (list): 解码副本字符到原文区间的映射

    Returns:
        str: 脱敏后的字符串
    """
    plan = []
    for match in _URL_USERINFO_RE.finditer(decoded):
        if match.group('userinfo_end') != '@':
            continue
        start, end = match.span()
        scheme = _original_span_text(original, spans, match, 'scheme')
        plan.append((start, end, f'{scheme}***:***@'))

    for match in _SENSITIVE_KEY_VALUE_RE.finditer(decoded):
        start, end = match.span()
        # 裸值分支需向后扫描补齐真实结尾，引号分支由正则的引号闭合规则决定
        if match.group('bare_value'):
            end = _decoded_bare_value_end(decoded, end)
        plan.append(
            (start, end, _rebuild_sensitive_replacement(original, spans, match))
        )

    # 结尾扫描可能把后一个匹配的起点纳入前一个匹配的区间，仅按起点排序无法
    # 发现这类区间重叠，倒序替换时会重复写入导致畸形输出；故先按起点升序做
    # 重叠剔除，保留起点在前且覆盖范围更大的匹配，使剩余区间互不相交。
    # userinfo 与敏感键值对重叠时同样由此决定优先级：起点在前者胜出，
    # 被剔除的一方所覆盖的凭据必然落在胜出者的替换区间内，不会明文残留
    planned = []
    covered_end = 0
    for start, end, text in sorted(plan):
        if planned and start < covered_end:
            continue
        planned.append((start, end, text))
        covered_end = max(covered_end, end)

    result = original
    for start, end, text in reversed(planned):
        orig_start = spans[start][0]
        orig_end = spans[end - 1][1]
        result = result[:orig_start] + text + result[orig_end:]
    return result


def mask_sensitive_fields(fields, sensitive_fields):
    """仅对调用方声明的字段执行敏感片段脱敏

    在字段字典的副本上进行处理：11 位手机号（1[3-9] 开头号段，允许带
    可选 +86/86 国家码前缀）保留前 3 位与后 4 位；scheme://user:pass@
    形式的 URL userinfo 脱敏为 ***:***@（保留 scheme/host/port 等）；
    access_token/token、sign、secret、password、api_key、webhook 等
    常见凭据键值对的值替换为 ***
    （支持 key=value、key: value、单双引号键值形式；值侧优先按
    带转义处理的引号字符串匹配到明确闭合引号或字符串末尾，否则取到
    空白、引号、逗号或右括号为止，故值内含转义引号、逗号或 & 等连接符
    时均能完整脱敏，未闭合的引号值不会凭空补充闭合引号；
    其中 & 后紧跟“键名 + = / : 或其百分号编码”形态时按 query 参数分隔符
    处理，敏感值到该 & 之前结束，& 及其后的非敏感参数（如
    &timestamp=1700000000000）原样保留，后续敏感参数各自单独脱敏；
    引号形式脱敏后保留原有结构，如 'access_token': 'abc123' 输出为
    'access_token': '***'，裸值形式输出为 access_token=***）；
    同时支持百分号编码的键名与分隔符（如 access%5Ftoken、%3D、%3A），
    键名中任意单个字符均允许以百分号编码形式出现（如 access_%74oken、
    %61ccess_token，hex 字母大小写均可匹配），脱敏时保留其编码形式
    只替换值；值内以百分号编码出现的结构性字符（如 %2C、%22、%27、%7D、
    %5D 及 %20 等空白）视为值本身的内容而非结构分隔符，敏感值会
    整段脱敏，不会在此截断并残留后半段明文（如 access_token=abc%2Cdef
    输出为 access_token=***，access_token=%22abc%22 输出为
    access_token=***）；%26 例外，与未编码的 & 适用同一条 query 参数
    分隔符规则：其后紧跟“键名 + = / : 或其百分号编码”时敏感值到该 %26
    之前结束，%26 及其后内容原样保留（如 access_token=abc%26plain%3Dv
    输出为 access_token=***%26plain%3Dv），不构成该形态时（如
    access_token=abc%26def）才整段脱敏；双层编码的键名与分隔符由固定
    深度正则直接识别，不受解码长度上限影响；其余检测会对不超过 4096
    字符的值执行最多一层受限百分号解码，以限制解码副本资源，三层及以上
    编码仍为已知边界；
    敏感键名前使用 ASCII 字母数字下划线边界断言，键名前缀为中文等
    非 ASCII 字符时同样脱敏，而 xaccess_token 等 ASCII 前缀拼接不脱敏；
    未声明字段与 None 值原样保留

    Args:
        fields (dict): 原始字段字典，不会被修改
        sensitive_fields (set | list | tuple): 需要脱敏的字段名集合

    Returns:
        dict: 脱敏后的新字典

    Raises:
        TypeError: fields 不是 dict，或 sensitive_fields 不是
            set/list/tuple 时抛出
    """
    if not isinstance(fields, dict):
        raise TypeError('fields 必须是 dict')
    if not isinstance(sensitive_fields, (set, list, tuple)):
        raise TypeError('sensitive_fields 必须是 set、list 或 tuple')

    result = dict(fields)
    for field in sensitive_fields:
        if field not in result or result[field] is None:
            continue
        value = str(result[field])
        value = _bounded_sub(
            re.compile(r'(?<!\d)(?:\+?86)?(1[3-9]\d)\d{4}(\d{4})(?!\d)'),
            lambda match: f'{match.group(1)}****{match.group(2)}',
            value,
        )
        decoded, spans = _decode_limited_with_spans(value)
        if spans is None:
            value = _bounded_sub(
                _URL_USERINFO_RE,
                _replace_url_userinfo,
                value,
                _resolve_url_userinfo_match,
            )
            value = _bounded_sub(
                _SENSITIVE_KEY_VALUE_RE,
                _replace_sensitive_key_value,
                value,
                _resolve_sensitive_key_value_match,
            )
        else:
            value = _mask_decoded_variants(value, decoded, spans)
        result[field] = value
    return result


def strip_wrapping_quotes(value):
    """剥离字符串值首尾成对的包裹引号

    支持英文直引号 ' 与 "，以及中文弯引号 '' 与 ""
    仅当首尾为同一组成对引号时才剥离，非字符串值原样返回

    Args:
        value: 待处理的值，可能为任意类型

    Returns:
        剥离包裹引号后的字符串；若入参非字符串则原样返回
    """
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if len(stripped) < 2:
        return stripped

    head, tail = stripped[0], stripped[-1]
    if QUOTE_PAIRS.get(head) == tail:
        return stripped[1:-1].strip()
    return stripped


def correct_channel_aliases(provider, params):
    """就地纠正推送渠道参数中的别名键名为 OnePush 要求的参数名

    根据 provider 名称查找别名映射表，将用户使用的通用键名（如 key）
    纠正为对应渠道要求的参数名（如 serverchan 的 sckey）直接在传入的
    params 上修改

    Args:
        provider (str): 推送通道名称，大小写不敏感，允许带包裹引号
        params (dict): 推送通道参数字典（不含 provider 键），将被就地修改

    Returns:
        dict: 已纠正的键名映射，格式为 {旧键名: 新键名}；无纠正时为空字典
    """
    provider = strip_wrapping_quotes(provider)
    if not isinstance(provider, str):
        return {}

    aliases = CHANNEL_KEY_ALIASES.get(provider.strip().lower(), {})
    if not aliases:
        return {}

    corrections = {}
    for old_key, new_key in aliases.items():
        # 守卫：别名键不存在，或目标键已存在时跳过，避免覆盖用户已正确填写的值
        if old_key not in params or new_key in params:
            continue
        params[new_key] = params.pop(old_key)
        corrections[old_key] = new_key
    return corrections


def get_provider_param_order(provider):
    """获取指定推送渠道用于位置参数推断的参数名顺序

    依据 OnePush 各渠道声明的 _params（required 在前、optional 在后），
    并剔除由程序自动填充的参数（title、content），得到用户位置参数可占用的
    参数名顺序，用于将无键名的位置值映射到正确的参数名

    Args:
        provider (str): 推送通道名称，大小写不敏感

    Returns:
        list[str]: 位置参数对应的参数名顺序；渠道不存在时返回空列表
    """
    if not isinstance(provider, str):
        return []

    try:
        notifier = get_notifier(provider.strip().lower())
    except Exception:
        # 未知渠道：无法推断参数名，返回空列表交由调用方处理
        return []

    params = getattr(notifier, '_params', None) or {}
    ordered = list(params.get('required', [])) + list(params.get('optional', []))
    return [name for name in ordered if name not in PROGRAM_FILLED_PARAMS]


def _assemble_channel(provider, named, positional):
    """将拆解出的 provider、命名参数与位置参数组装为标准通道字典

    位置参数按渠道声明的参数顺序映射到参数名，并跳过已被命名参数占用的槽位；
    随后纠正密钥别名（如 serverchan 的 key -> sckey）

    Args:
        provider (str): 推送通道名称
        named (dict): 已带键名的参数（键名可能仍是别名）
        positional (list): 无键名的位置参数值列表

    Returns:
        dict: 标准通道字典，provider 键排在最前；解析失败时返回空字典
    """
    if not provider or not isinstance(provider, str):
        return {}

    provider = provider.strip().lower()
    params = {str(key): value for key, value in named.items()}

    if positional:
        order = get_provider_param_order(provider)
        slot = 0
        for value in positional:
            # 跳过已被命名参数占用的参数名槽位
            while slot < len(order) and order[slot] in params:
                slot += 1
            if slot >= len(order):
                LOGGER.warning(
                    f"推送通道 '{provider}' 的位置参数过多，已忽略多余值: {value}"
                )
                break
            params[order[slot]] = value
            slot += 1

    correct_channel_aliases(provider, params)

    channel = {'provider': provider}
    channel.update(params)
    return channel


def _is_known_provider(name):
    """判断给定名称是否为 OnePush 已知推送渠道

    剥离包裹引号并转为小写后，与已知渠道名单比对

    Args:
        name: 待判断的名称，可能为任意类型

    Returns:
        bool: 命中已知渠道名单时为 True，否则为 False
    """
    if not isinstance(name, str):
        return False
    return strip_wrapping_quotes(name).strip().lower() in KNOWN_PROVIDERS


def _locate_provider(items):
    """在无参数头的键值项列表中定位 provider，并归类其余参数

    依次按以下优先级定位 provider：
    1. 某项的键命中已知渠道名单（如 {dingtalk, secret: x} 或 {secret: x, dingtalk}）；
    2. 某项的值命中已知渠道名单（如 {SCTxxxx: serverchan}），该项键转为位置参数；
    3. 均未命中时回退为「首项即 provider」，以兼容自定义/未知渠道

    Args:
        items (list): (key, value) 二元组列表，已完成引号剥离

    Returns:
        tuple: (provider, named, positional)，分别为通道名、命名参数字典、
            位置参数列表；items 为空时 provider 为 None
    """
    if not items:
        return None, {}, []

    provider_index = None
    provider = None
    provider_from_value = False

    # 优先级 1：键命中已知渠道名单
    for index, (key, _) in enumerate(items):
        if _is_known_provider(key):
            provider_index = index
            provider = key
            break

    # 优先级 2：值命中已知渠道名单，对应键降级为位置参数
    if provider_index is None:
        for index, (_, value) in enumerate(items):
            if _is_known_provider(value):
                provider_index = index
                provider = value
                provider_from_value = True
                break

    # 优先级 3：回退为首项即 provider
    if provider_index is None:
        return _locate_provider_fallback(items)

    named = {}
    positional = []
    for index, (key, value) in enumerate(items):
        if index == provider_index:
            # provider 由值命中时，其键作为位置参数（如 {SCTxxxx: serverchan} 的 SCTxxxx）
            if provider_from_value and key is not None:
                positional.append(key)
            # provider 由键命中且带值时，其值作为位置参数（如 {serverchan: SCTxxxx} 的 SCTxxxx）
            elif not provider_from_value and value is not None:
                positional.append(value)
            continue
        if value is None:
            positional.append(key)
        else:
            named[key] = value
    return provider, named, positional


def _locate_provider_fallback(items):
    """无任何项命中已知渠道名单时，按「首项即 provider」归类参数

    Args:
        items (list): (key, value) 二元组列表，已完成引号剥离

    Returns:
        tuple: (provider, named, positional)
    """
    first_key, first_value = items[0]
    provider = first_key
    named = {}
    positional = []
    if first_value is not None:
        positional.append(first_value)
    for key, value in items[1:]:
        if value is None:
            positional.append(key)
        else:
            named[key] = value
    return provider, named, positional


def _dict_fragment_to_channel(fragment):
    """将字典形式的通道片段解析为标准通道字典

    兼容标准写法（含 provider 键，允许键乱序）与各类无参数头写法：
    通道名可位于任意位置（首/中/末），亦可与密钥参数颠倒书写，
    程序通过 OnePush 已知渠道名单自动定位 provider

    Args:
        fragment (dict): 字典形式的通道片段

    Returns:
        dict: 标准通道字典；无法解析时返回空字典
    """
    items = [
        (strip_wrapping_quotes(key), strip_wrapping_quotes(value))
        for key, value in fragment.items()
    ]
    if not items:
        return {}

    has_provider = any(str(key).lower() == 'provider' for key, _ in items)

    if has_provider:
        provider = None
        named = {}
        positional = []
        for key, value in items:
            if str(key).lower() == 'provider':
                provider = value
            elif value is None:
                positional.append(key)
            else:
                named[key] = value
        return _assemble_channel(provider, named, positional)

    # 无参数头：通过已知渠道名单定位 provider，兼容乱序、颠倒、通道名居中等写法
    provider, named, positional = _locate_provider(items)
    return _assemble_channel(provider, named, positional)


def _list_fragment_to_channel(fragment):
    """将列表形式的通道片段解析为标准通道字典

    先将各元素归一为 (key, value) 项（裸标量 -> (值, None)，单键字典 ->
    (键, 值)），再通过 OnePush 已知渠道名单定位 provider，从而兼容
    [serverchan, SCTxxxx]、[SCTxxxx, serverchan]、[SCTxxxx: serverchan] 等写法

    Args:
        fragment (list): 列表形式的通道片段

    Returns:
        dict: 标准通道字典；无法解析时返回空字典
    """
    elements = list(fragment)
    if not elements:
        return {}

    items = []
    for element in elements:
        if isinstance(element, dict):
            for key, value in element.items():
                items.append(
                    (strip_wrapping_quotes(key), strip_wrapping_quotes(value))
                )
            continue
        items.append((strip_wrapping_quotes(element), None))

    # 显式 provider 键：保持其作为通道名，其余按键值归类
    if any(str(key).lower() == 'provider' for key, _ in items):
        provider = None
        named = {}
        positional = []
        for key, value in items:
            if str(key).lower() == 'provider':
                provider = value
            elif value is None:
                positional.append(key)
            else:
                named[key] = value
        return _assemble_channel(provider, named, positional)

    provider, named, positional = _locate_provider(items)
    return _assemble_channel(provider, named, positional)


def _fragment_to_channel(parsed):
    """将单个已解析的通道片段（结构化对象）转换为标准通道字典

    Args:
        parsed: 已解析的结构化对象，可能为 dict、list 或裸标量

    Returns:
        dict: 标准通道字典；无法解析时返回空字典
    """
    if isinstance(parsed, dict):
        return _dict_fragment_to_channel(parsed)
    if isinstance(parsed, (list, tuple)):
        return _list_fragment_to_channel(parsed)
    if isinstance(parsed, str):
        provider = strip_wrapping_quotes(parsed).strip().lower()
        return {'provider': provider} if provider else {}
    return {}



def parse_push_channels(raw_value):
    """将用户填写的 push_channel 配置解析为标准通道字典列表

    兼容以下输入形式：
    - 标准字典 {provider: serverchan, sckey: SCTxxxx}（允许键乱序）；
    - 无参数头写法 [serverchan, SCTxxxx] / [serverchan: SCTxxxx] /
      {serverchan, SCTxxxx} / {serverchan: SCTxxxx}；
    - 多通道 block list，每个元素均为映射

    Args:
        raw_value: push_channel 的原始值（dict / list）

    Returns:
        list[dict]: 标准通道字典列表，每项 provider 键排在最前
    """
    if raw_value is None:
        return []

    # 列表且所有元素均为映射：视为多通道 block list
    if isinstance(raw_value, (list, tuple)) and _is_multi_channel_list(raw_value):
        channels = []
        for element in raw_value:
            channel = _fragment_to_channel(element)
            if channel:
                channels.append(channel)
        return channels

    # 其余结构化对象：视为单通道（含 [serverchan, SCTxxxx] 等无参数头列表写法）
    channel = _fragment_to_channel(raw_value)
    return [channel] if channel else []


def _is_multi_channel_list(raw_value):
    """判断列表形式的 push_channel 是否为「多通道 block list」

    仅当列表非空且所有元素均为映射（dict）时，视为多通道列表；
    含裸标量的列表（如 [serverchan, SCTxxxx]）属于单通道无参数头写法

    Args:
        raw_value (list | tuple): 待判断的列表

    Returns:
        bool: 为多通道 block list 时返回 True
    """
    if len(raw_value) == 0:
        return False
    return all(isinstance(element, dict) for element in raw_value)


def parse_time_string(time_str):
    """解析时间字符串为秒

    支持负数时间配置（例如 `-5s`、`-1m`）：负号仅用于表达负值
    ，实际以绝对值使用，并记录修正日志。

    Args:
        time_str (str): 时间字符串，格式如 "1h", "15m", "30s"

    Returns:
        float: 转换后的秒数

    Raises:
        ValueError: 如果时间字符串格式无效、类型不是字符串或数值，或解析出的
            数值为非有限值（inf / -inf / nan）
    """
    LOGGER.info(f"解析时间字符串: {time_str}")
    # 兼容负值输入：自动去除前缀减号，保持时间语义为正数
    if isinstance(time_str, (int, float)):
        time_str = str(time_str)
    if not isinstance(time_str, str):
        # None/列表/字典等类型没有 strip 方法，直接调用会抛 AttributeError
        # 穿透调用方的 except (TypeError, ValueError)，故在此收敛为 ValueError
        LOGGER.error(f"时间配置必须是字符串或数值: {type(time_str).__name__}")
        raise ValueError(
            f"时间配置必须是字符串或数值: {type(time_str).__name__}"
        )
    time_str = time_str.strip().lower()
    if not time_str:
        LOGGER.error("时间字符串不能为空")
        raise ValueError("时间字符串不能为空")

    if time_str.startswith('-'):
        original = time_str
        time_str = time_str.lstrip('-').strip()
        LOGGER.warning(f"检测到负数时间配置，已自动去除负号: {original} -> {time_str}")
        if not time_str:
            LOGGER.error("时间字符串去除负号后为空")
            raise ValueError("时间字符串不能为空")

    units = {
        'h': 3600,   # 1小时 = 3600秒
        'm': 60,     # 1分钟 = 60秒
        's': 1       # 1秒 = 1秒
    }
    
    if time_str[-1] in units:
        value = float(time_str[:-1])
        unit = time_str[-1]
        seconds = value * units[unit]
        # float() 接受 inf/1e400/nan 等字面量，且有限值乘以单位系数后仍可能
        # 静默溢出为 inf（如 1e308h），非有限值会让调用方的 int() 抛
        # OverflowError 穿透，故在乘法之后统一收敛为 ValueError
        if not math.isfinite(seconds):
            LOGGER.error(
                f"无效的时间数值: {time_str}，请使用 '1h', '15m', '30s'"
            )
            raise ValueError(
                f"无效的时间数值: {time_str}，请使用 '1h', '15m', '30s'"
            )
        LOGGER.info(f"解析结果: {seconds} 秒")
        return seconds
    else:
        # 尝试直接解析为整数（秒）
        try:
            seconds = int(time_str)
            LOGGER.info(f"直接解析为秒: {seconds}")
            return seconds
        except ValueError:
            LOGGER.error(
                f"无效的时间格式: {time_str}，请使用 '1h', '15m', '30s'"
            )
            raise ValueError(f"无效的时间格式: {time_str}，请使用 '1h', '15m', '30s'")
