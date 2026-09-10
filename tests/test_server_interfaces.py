#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务模式固定接口测试。"""

import inspect
from pathlib import Path

from modules.server_auth import ServerAuthStore
from modules.server_core import finish_context, run_fastapi_server
from modules.server_options import (
    ResolvedServerOptions,
    ServerConfigError,
    ServerOptions,
    resolve_server_options,
)
from modules.server_protocol import JsonLineProtocol, ServerProtocolWriter
from modules.windows_job import WindowsJob


def test_fixed_service_interfaces_are_importable():
    """锁定跨任务使用的公开名称和核心签名。"""
    assert inspect.signature(run_fastapi_server).parameters.keys() == {'options'}
    assert inspect.signature(finish_context).parameters.keys() == {'context'}
    assert ServerOptions.__dataclass_fields__['server_mode'].type is bool
    assert ResolvedServerOptions.__dataclass_fields__['port'].type is int
    assert all(value is not None for value in (
        JsonLineProtocol,
        ServerProtocolWriter,
        ServerAuthStore,
        WindowsJob,
        ServerConfigError,
        resolve_server_options,
    ))


def test_resolve_server_options_returns_basic_result(tmp_path):
    """解析接口返回基本结果而不是旧的未实现异常。"""
    config_path = tmp_path / Path(__file__).name
    config_path.write_text('[Web]\naccess_token = xxxxxxxxxxxxxxxx\n', encoding='utf-8')
    resolved = resolve_server_options(ServerOptions(True, config_path), {})
    assert isinstance(resolved, ResolvedServerOptions)
    assert (resolved.server_mode, resolved.config_path, resolved.port) == (True, config_path, 52233)


def test_windows_job_is_safe_to_import_on_current_platform():
    """Windows Job 包装在非 Windows 平台也可以安全导入。"""
    assert WindowsJob is not None
