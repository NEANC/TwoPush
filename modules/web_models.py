#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Web 请求模型与公共校验。"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ALLOWED_ACTIONS = frozenset({'save', 'direct', 'save_and_push'})
_WILDCARD_CHARS = frozenset({'*', '?', '['})
_WINDOWS_ILLEGAL_CHARS = frozenset({':', '<', '>', '"', '|'})
_DRIVE_PATH = re.compile(r'^[A-Za-z]:')
_RESERVED_DEVICE_NAMES = frozenset({'CON', 'PRN', 'AUX', 'NUL', *(f'COM{index}' for index in range(1, 10)), *(f'LPT{index}' for index in range(1, 10))})


def _validate_windows_path_parts(parts: list[str]) -> None:
    """校验 Windows 文件名组成部分。"""
    for part in parts:
        if any(char in _WINDOWS_ILLEGAL_CHARS for char in part) or any(ord(char) < 32 for char in part):
            raise ValueError('路径包含 Windows 不允许的字符')
        if part.endswith((' ', '.')):
            raise ValueError('路径段不能以空格或句点结尾')
        if part.split('.', 1)[0].rstrip(' ').rstrip('.').upper() in _RESERVED_DEVICE_NAMES:
            raise ValueError('路径不能使用 Windows 保留设备名')


def _validate_relative_path(value: str) -> str:
    """校验工作区内使用的相对文件路径。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('路径必须是非空字符串')
    if value.endswith((' ', '.')):
        raise ValueError('路径不能以空格或句点结尾')
    path = value.strip()
    if path.startswith(('/', '\\')) or _DRIVE_PATH.match(path):
        raise ValueError('路径必须是相对路径')
    if any(char in path for char in _WILDCARD_CHARS):
        raise ValueError('路径不允许包含通配符')
    parts = re.split(r'[/\\]', path)
    _validate_windows_path_parts(parts)
    if any(part in ('', '.') for part in parts):
        raise ValueError('路径不允许包含空路径段或当前目录段')
    if '..' in parts:
        raise ValueError('路径不允许目录遍历')
    return path


def _validate_filename(value: str) -> str:
    """校验只包含文件名的临时文件标识。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('名称必须是非空字符串')
    if value.endswith((' ', '.')):
        raise ValueError('名称不能以空格或句点结尾')
    name = value.strip()
    if name in ('.', '..') or '/' in name or '\\' in name:
        raise ValueError('名称必须是纯文件名')
    if _DRIVE_PATH.match(name) or any(char in name for char in _WILDCARD_CHARS):
        raise ValueError('名称不是安全的文件名')
    _validate_windows_path_parts([name])
    return name


class LaunchAuthPayload(BaseModel):
    """启动令牌兑换载荷。"""

    model_config = ConfigDict(extra='forbid')

    launch_token: str


class JsonTemplatePayload(BaseModel):
    """JSON 推送模板载荷。"""

    model_config = ConfigDict(extra='allow')

    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    channels: list[dict[str, Any]] = Field(min_length=1)

    @field_validator('title', 'content')
    @classmethod
    def validate_text(cls, value: str) -> str:
        """校验标题和内容为非空字符串。"""
        if not value.strip():
            raise ValueError('字段必须是非空字符串')
        return value

    @field_validator('channels')
    @classmethod
    def validate_channels(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """校验通道列表非空且每项为对象。"""
        if not value:
            raise ValueError('channels 不能为空')
        return value


class IniConfigPayload(BaseModel):
    """INI 配置更新载荷，保留各节的动态字段。"""

    model_config = ConfigDict(extra='allow')

    path: str | None = None
    network: dict[str, str | bool | int] = Field(default_factory=dict)
    push: dict[str, str | bool | int] = Field(default_factory=dict)
    update: dict[str, str | bool | int] = Field(default_factory=dict)
    logs: dict[str, str | bool | int] = Field(default_factory=dict)


class PushRequest(BaseModel):
    """推送操作请求。"""

    action: str
    path: str
    payload: dict[str, Any] | None = None

    @field_validator('action')
    @classmethod
    def validate_action(cls, value: str) -> str:
        """校验推送动作。"""
        if value not in _ALLOWED_ACTIONS:
            raise ValueError('不支持的推送动作')
        return value

    @field_validator('path')
    @classmethod
    def validate_path(cls, value: str) -> str:
        """校验推送文件路径。"""
        return _validate_relative_path(value)


class FileOperationRequest(BaseModel):
    """文件操作请求。"""

    path: str

    @field_validator('path')
    @classmethod
    def validate_path(cls, value: str) -> str:
        """校验文件操作路径。"""
        return _validate_relative_path(value)


class TempDeleteRequest(BaseModel):
    """临时文件删除请求。"""

    names: list[str] = Field(min_length=1)
    confirmed: bool

    @field_validator('names')
    @classmethod
    def validate_names(cls, value: list[str]) -> list[str]:
        """校验临时文件名数组。"""
        return [_validate_filename(name) for name in value]

    @field_validator('confirmed', mode='before')
    @classmethod
    def validate_confirmed(cls, value: Any) -> bool:
        """仅接受布尔值作为删除确认。"""
        if type(value) is not bool:
            raise ValueError('confirmed 必须为布尔值')
        return value


def resolve_web_host(access_token: str) -> str:
    """根据访问令牌长度决定 Web 服务监听地址。"""
    return '0.0.0.0' if isinstance(access_token, str) and len(access_token) >= 16 else '127.0.0.1'
