#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""FastAPI 服务启动选项的固定接口。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ServerOptions:
    """保存命令行或宿主传入的服务选项。"""

    server_mode: bool
    config_path: Path
    host: str | None = None
    port: str | None = None
    public_url: str | None = None
    emit_launch_token: bool = False
    silent: bool = False


@dataclass(frozen=True)
class ResolvedServerOptions:
    """保存完成解析后的服务选项。"""

    server_mode: bool
    config_path: Path
    host: str
    port: int
    port_explicit: bool
    public_url: str | None
    root_path: str
    base_path: str
    temp_dir: Path
    log_root: Path
    access_token: str
    emit_launch_token: bool
    silent: bool


class ServerConfigError(Exception):
    """表示服务配置无效的稳定错误。"""

    def __init__(self, code: str, exit_code: int, safe_message: str):
        """创建包含稳定错误码和安全消息的配置错误。"""
        super().__init__(safe_message)
        self.code = code
        self.exit_code = exit_code
        self.safe_message = safe_message


def resolve_server_options(
        options: ServerOptions,
        environ: Mapping[str, str],
) -> ResolvedServerOptions:
    """解析服务选项；具体来源和校验行为由后续任务实现。"""
    raise NotImplementedError
