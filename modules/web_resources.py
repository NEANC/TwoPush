#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""源码模式 Web 静态资源读取。"""

from pathlib import Path


_ALLOWED_RESOURCES = frozenset({'index.html', 'app.js', 'style.css'})


def get_web_resource_dir(project_dir):
    """获取源码项目目录下的 Web 资源目录。"""
    return Path(project_dir).resolve() / 'web'


def get_web_resource(name, project_dir):
    """安全读取白名单中的 Web 资源并返回 UTF-8 字节。"""
    if name not in _ALLOWED_RESOURCES or Path(name).name != name:
        raise ValueError('不允许访问 Web 资源')

    resource_dir = get_web_resource_dir(project_dir)
    resource_path = resource_dir / name
    try:
        resolved_path = resource_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError(f'Web 资源不存在: {name}') from error

    try:
        resolved_path.relative_to(resource_dir)
    except ValueError as error:
        raise ValueError('不允许访问 Web 资源') from error
    if not resolved_path.is_file():
        raise FileNotFoundError(f'Web 资源不存在: {name}')
    return resolved_path.read_bytes()
