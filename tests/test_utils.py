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
