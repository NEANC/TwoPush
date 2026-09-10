#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""FastAPI 服务生命周期核心的固定接口。"""

from dataclasses import dataclass
from typing import Any

from modules.server_options import ServerOptions


@dataclass
class ServiceResult:
    """表示服务生命周期结束结果。"""

    code: str = 'OK'
    exit_code: int = 0


@dataclass
class ServiceContext:
    """保存服务生命周期上下文的最小容器。"""

    value: Any = None


def run_fastapi_server(options: ServerOptions) -> int:
    """验证服务入口契约并返回，完整生命周期留给后续实现。"""
    if not options.server_mode:
        raise ValueError('服务入口需要 server_mode')
    return 0


def finish_context(context: ServiceContext) -> ServiceResult:
    """完成服务清理；完整清理顺序由后续任务实现。"""
    raise NotImplementedError
