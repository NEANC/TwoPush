#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""源码模式 Web 静态资源读取测试。"""

from pathlib import Path

import pytest

from modules.web_resources import get_web_resource, get_web_resource_dir


_ALLOWED_RESOURCES = {
    'index.html': '<!doctype html>',
    'app.js': 'window.fetch',
    'style.css': ':root',
}


def test_reads_allowed_resources_from_project_web_directory(tmp_path):
    """应从项目目录下的 web 文件夹读取允许的资源。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    for name, marker in _ALLOWED_RESOURCES.items():
        (resource_dir / name).write_text(marker, encoding='utf-8')

    assert get_web_resource_dir(tmp_path) == resource_dir.resolve()
    for name, marker in _ALLOWED_RESOURCES.items():
        assert get_web_resource(name, tmp_path) == marker.encode('utf-8')


def test_missing_resource_raises_clear_file_not_found_error(tmp_path):
    """缺失资源应返回明确的文件不存在错误。"""
    (tmp_path / 'web').mkdir()

    with pytest.raises(FileNotFoundError, match='Web 资源不存在'):
        get_web_resource('index.html', tmp_path)


@pytest.mark.parametrize('name', [
    'other.txt', 'sub/app.js', '../app.js', '..\\app.js',
    '/app.js', '\\app.js', 'C:/app.js', 'web/app.js', '',
])
def test_rejects_unsupported_or_unsafe_resource_names(tmp_path, name):
    """应拒绝非白名单资源名、目录分隔符和绝对路径。"""
    (tmp_path / 'web').mkdir()

    with pytest.raises(ValueError, match='不允许访问 Web 资源'):
        get_web_resource(name, tmp_path)


def test_rejects_resource_symlink_outside_web_directory(tmp_path):
    """资源符号链接越出 web 目录时应拒绝访问。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    outside = tmp_path / 'secret.txt'
    outside.write_text('secret', encoding='utf-8')
    try:
        (resource_dir / 'app.js').symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('当前环境不支持创建符号链接')

    with pytest.raises(ValueError, match='不允许访问 Web 资源'):
        get_web_resource('app.js', tmp_path)
