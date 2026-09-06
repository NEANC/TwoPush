#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Web 请求模型测试。"""

import pytest
from pydantic import ValidationError

from modules.web_models import (
    FileOperationRequest,
    IniConfigPayload,
    JsonTemplatePayload,
    PushRequest,
    TempDeleteRequest,
    resolve_web_host,
)


def test_json_template_accepts_required_fields_and_unknown_channel_fields():
    """JSON 模板应保留通道中的未知字段。"""
    payload = JsonTemplatePayload(
        title="标题",
        content="内容",
        channels=[{"provider": "custom", "unknown": "keep"}],
    )

    assert payload.channels[0]["unknown"] == "keep"


@pytest.mark.parametrize("value", [None, "", [], {}])
def test_json_template_rejects_invalid_required_fields(value):
    """JSON 模板必填字段不得为空或类型错误。"""
    with pytest.raises(ValidationError):
        JsonTemplatePayload(title=value, content="内容", channels=[{}])
    with pytest.raises(ValidationError):
        JsonTemplatePayload(title="标题", content=value, channels=[{}])
    with pytest.raises(ValidationError):
        JsonTemplatePayload(title="标题", content="内容", channels=value)


def test_ini_payload_validates_string_boolean_and_integer_values():
    """INI 模型应支持字符串、布尔和整数配置值。"""
    payload = IniConfigPayload(
        network={"proxy": "http://127.0.0.1"},
        push={"enable_proxy_for_push": True, "retry_count": 2},
    )

    assert payload.network["proxy"] == "http://127.0.0.1"
    assert payload.push["enable_proxy_for_push"] is True
    assert payload.push["retry_count"] == 2


def test_push_request_accepts_only_supported_actions():
    """推送动作只能使用三种受支持的值。"""
    for action in ("save", "direct", "save_and_push"):
        assert PushRequest(action=action, path="payload.json").action == action

    with pytest.raises(ValidationError):
        PushRequest(action="delete", path="payload.json")


@pytest.mark.parametrize("path", ["", "/tmp/a.json", "C:\\a.json", "\\\\server\\share\\a.json", "../a.json", "a/../b.json", "a*.json", "a//b.json", "a/./b.json", "a/", "a\\"])
def test_file_paths_must_be_safe_relative_paths(path):
    """文件路径必须是非空、不越界且不含特殊模式的相对路径。"""
    with pytest.raises(ValidationError):
        FileOperationRequest(path=path)

    with pytest.raises(ValidationError):
        PushRequest(action="save", path=path)


def test_file_paths_allow_nested_relative_paths():
    """文件路径允许使用嵌套的相对路径。"""
    assert FileOperationRequest(path="nested\\a.json").path == "nested\\a.json"


def test_temp_delete_requires_plain_filename_and_confirmation():
    """临时文件删除只接受纯文件名和布尔确认。"""
    assert TempDeleteRequest(name="Temp_push.json", confirmed=True).confirmed is True
    for name in ("", "a/b.json", "../a.json", "*.json", "C:\\a.json"):
        with pytest.raises(ValidationError):
            TempDeleteRequest(name=name, confirmed=True)
    with pytest.raises(ValidationError):
        TempDeleteRequest(name="Temp_push.json", confirmed=False)
    with pytest.raises(ValidationError):
        TempDeleteRequest(name="Temp_push.json", confirmed="true")


def test_resolve_web_host_uses_token_length_threshold():
    """短令牌使用本机监听，足够长令牌使用全部网卡。"""
    assert resolve_web_host("") == "127.0.0.1"
    assert resolve_web_host("short-token") == "127.0.0.1"
    assert resolve_web_host("1234567890abcdef") == "0.0.0.0"
