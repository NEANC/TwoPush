#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""notification 模块单元测试"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.notification import send_notification


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
