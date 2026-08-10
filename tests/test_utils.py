#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""utils 模块单元测试"""

import pytest

from modules.utils import mask_sensitive_fields


def test_mask_sensitive_fields_masks_only_declared_fields():
    """仅对调用方声明的字段执行敏感片段脱敏"""
    fields = {
        'reason': '手机号 13800138000 access_token=abc sign=xyz secret=SECa token=xyz',
        'provider': 'dingtalk(builtin)',
        'content': 'design=keep assign=keep xaccess_token=keep',
    }
    masked = mask_sensitive_fields(fields, sensitive_fields={'reason'})

    assert '13800138000' not in masked['reason']
    assert '138****8000' in masked['reason']
    assert 'access_token=***' in masked['reason']
    assert ' token=***' in masked['reason']
    assert 'token=xyz' not in masked['reason']
    assert 'sign=***' in masked['reason']
    assert 'secret=***' in masked['reason']
    assert 'xaccess_token=keep' in masked['content']
    assert masked['provider'] == 'dingtalk(builtin)'
    assert masked['content'] == fields['content']


def test_mask_sensitive_fields_validates_input_and_returns_copy():
    """校验入参类型并返回新字典，不修改原字段"""
    fields = {'reason': '13800138000', 'count': 13800138000, 'empty': None}
    result = mask_sensitive_fields(fields, sensitive_fields={'reason', 'count', 'empty'})

    assert result is not fields
    assert fields['reason'] == '13800138000'
    assert result['reason'] == '138****8000'
    assert result['count'] == '138****8000'
    assert result['empty'] is None
    assert mask_sensitive_fields(fields, sensitive_fields=set()) == fields
    assert mask_sensitive_fields(fields, sensitive_fields=set()) is not fields

    with pytest.raises(TypeError):
        mask_sensitive_fields([], sensitive_fields={'reason'})
    with pytest.raises(TypeError):
        mask_sensitive_fields(fields, sensitive_fields='reason')


def test_mask_sensitive_fields_keeps_non_mobile_segments_untouched():
    """1 开头但第二位非 3-9 的 11 位数字（如订单号）不应被脱敏"""
    text = mask_sensitive_fields({'text': '订单号 12000000000'}, {'text'})['text']

    assert '12000000000' in text


def test_mask_sensitive_fields_still_masks_valid_mobile_segments():
    """11 位且第二位为 3-9 的手机号仍应被脱敏为 138****8000 形式"""
    text = mask_sensitive_fields({'text': '手机号 13800138000'}, {'text'})['text']

    assert '13800138000' not in text
    assert '138****8000' in text


def test_mask_sensitive_fields_masks_country_code_prefixed_mobiles():
    """带 +86/86 前缀的手机号应脱敏为纯号段掩码，不泄露完整号码"""
    text = mask_sensitive_fields(
        {'t': '手机号 +8613800138000 备用 8613800138000'}, {'t'}
    )['t']

    assert '8613800138000' not in text
    assert text.count('138****8000') == 2


def test_ampersand_in_sensitive_value_is_fully_masked():
    """敏感值内含 & 时应完整脱敏，不残留 & 后缀片段"""
    result = mask_sensitive_fields({'reason': 'secret=abc&def'}, {'reason'})['reason']

    assert result == 'secret=***'
    assert '&def' not in result


def test_query_chain_is_fully_masked():
    """含 & 的连续敏感键值对（如 query 链）应整体被脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=abc&sign=xyz'}, {'reason'}
    )['reason']

    assert 'abc' not in result
    assert 'xyz' not in result
    assert 'access_token=***' in result


def test_whitespace_separated_sensitive_values_are_masked():
    """空白分隔的敏感键值对各自被脱敏，空白作为分隔符保留"""
    result = mask_sensitive_fields(
        {'reason': 'token=xyz secret=SECa'}, {'reason'}
    )['reason']

    assert 'token=***' in result
    assert 'secret=***' in result
    assert 'xyz' not in result
    assert 'SECa' not in result


def test_cjk_prefix_before_sensitive_key_is_masked():
    """中文（非 ASCII）直接粘连敏感键名时仍应脱敏"""
    result = mask_sensitive_fields(
        {'reason': '参数access_token=abc 密钥secret=SECa 签名sign=xyz'}, {'reason'}
    )['reason']

    assert 'abc' not in result
    assert 'SECa' not in result
    assert 'xyz' not in result
    assert 'access_token=***' in result
    assert 'secret=***' in result
    assert 'sign=***' in result


def test_ascii_prefix_before_sensitive_key_not_masked():
    """ASCII 字母数字下划线前缀（如 xaccess_token=）不应被脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'xaccess_token=keep mytoken=keep'}, {'reason'}
    )['reason']

    assert 'xaccess_token=keep' in result
    assert 'mytoken=keep' in result


def test_json_key_value_sensitive_fields_are_masked():
    """JSON 键值形式（"token":"value"）的敏感值应整体脱敏"""
    result = mask_sensitive_fields(
        {'reason': '{"access_token":"abc123","secret":"SECxyz","sign":"sigxyz"}'},
        {'reason'},
    )['reason']

    assert result == '{"access_token":"***","secret":"***","sign":"***"}'
    assert 'abc123' not in result
    assert 'SECxyz' not in result
    assert 'sigxyz' not in result


def test_colon_form_sensitive_values_are_masked():
    """冒号形式（token: value）的敏感值应脱敏，冒号两侧空格可有可无"""
    result = mask_sensitive_fields(
        {'reason': 'token: xyz sign : SECa'}, {'reason'}
    )['reason']

    assert 'token=***' in result
    assert 'sign=***' in result
    assert 'xyz' not in result
    assert 'SECa' not in result


def test_quoted_value_with_ampersand_is_fully_masked():
    """JSON 引号形式的值内含 & 时应完整脱敏，不残留 & 后缀片段"""
    result = mask_sensitive_fields(
        {'reason': '"secret":"abc&def"'}, {'reason'}
    )['reason']

    assert result == '"secret":"***"'
    assert '&def' not in result


def test_json_keys_with_sensitive_name_substrings_not_masked():
    """design/assign/mytoken 等含敏感键名子串的键不应被误伤"""
    result = mask_sensitive_fields(
        {'reason': '{"design":"keep","assign":"keep","mytoken":"keep"}'},
        {'reason'},
    )['reason']

    assert result == '{"design":"keep","assign":"keep","mytoken":"keep"}'


def test_json_value_with_escaped_quote_is_fully_masked():
    """JSON 引号形式的值内含转义引号时应完整脱敏，不残留引号后片段"""
    result = mask_sensitive_fields(
        {'reason': '{"secret":"abc\\"def"}'}, {'reason'}
    )['reason']

    assert result == '{"secret":"***"}'
    assert 'abc"def' not in result


def test_json_value_with_comma_is_fully_masked():
    """JSON 引号形式的值内含逗号时应完整脱敏，不残留逗号后片段"""
    result = mask_sensitive_fields(
        {'reason': '{"secret":"a,b"}'}, {'reason'}
    )['reason']

    assert result == '{"secret":"***"}'
    assert 'a' not in result
    assert 'b' not in result


def test_mixed_bare_and_json_forms_are_masked_respectively():
    """裸值形式与 JSON 引号形式同时存在时，各自按对应规则脱敏"""
    result = mask_sensitive_fields(
        {'reason': 'access_token=abc {"secret":"SECa","sign":"xyz"}'},
        {'reason'},
    )['reason']

    assert 'access_token=***' in result
    assert '"secret":"***"' in result
    assert '"sign":"***"' in result
    assert 'abc' not in result
    assert 'SECa' not in result
    assert 'xyz' not in result
