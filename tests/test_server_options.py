#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务选项解析测试。"""

import pytest

from modules.server_options import (
    ServerConfigError,
    ServerOptions,
    build_local_url,
    resolve_server_options,
)


def write_config(path, token='', temp_folder='Temp'):
    """写入最小服务配置。"""
    path.write_text(
        f'[Web]\naccess_token = {token}\n[Paths]\ntemp_folder = {temp_folder}\n',
        encoding='utf-8',
    )


def test_cli_overrides_environment_and_empty_environment_falls_back(tmp_path):
    """CLI 高于非空环境，空环境按未设置处理。"""
    config = tmp_path / 'config.ini'
    write_config(config, 'x' * 32)
    cli = resolve_server_options(
        ServerOptions(True, config, host='127.0.0.2', port='4321'),
        {'TWOPUSH_SERVER_HOST': '0.0.0.0', 'TWOPUSH_SERVER_PORT': '5000'},
    )
    fallback = resolve_server_options(
        ServerOptions(True, config),
        {'TWOPUSH_SERVER_HOST': '', 'TWOPUSH_SERVER_PORT': ' '},
    )
    assert (cli.host, cli.port, cli.port_explicit) == ('127.0.0.2', 4321, True)
    assert (fallback.host, fallback.port, fallback.port_explicit) == ('0.0.0.0', 52233, False)


@pytest.mark.parametrize('value', [' 1', '1 ', '+1', '-1', '１２', '65536', '1.0', ''])
def test_explicit_port_is_ascii_decimal_in_range(tmp_path, value):
    """端口只接受 0..65535 ASCII 十进制文本。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config, port=value), {})
    assert (error.value.code, error.value.exit_code) == ('INVALID_PORT', 2)


@pytest.mark.parametrize('host', ['', 'bad host', '256.1.1.1', '[::1]', 'host\nname'])
def test_invalid_host_has_dedicated_error(tmp_path, host):
    """语法非法或无法解析的监听地址返回 INVALID_HOST。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config, host=host), {})
    assert error.value.code == 'INVALID_HOST'


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '127.255.1.2', '::1'])
def test_loopback_hosts_work_without_token(tmp_path, host):
    """规定的 loopback 地址无需长期 token。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    assert resolve_server_options(ServerOptions(True, config, host=host), {}).host == host


@pytest.mark.parametrize('host', ['0.0.0.0', '::', '192.0.2.10'])
@pytest.mark.parametrize('token', ['', '   ', 'x' * 15])
def test_remote_host_without_valid_token_is_rejected(tmp_path, host, token):
    """空、空白或短于 16 字符的长期 token 均不能授权远程监听。"""
    config = tmp_path / 'config.ini'
    write_config(config, token)
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config, host=host), {})
    assert (error.value.code, error.value.exit_code) == ('AUTH_REQUIRED', 2)


@pytest.mark.parametrize('host', ['0.0.0.0', '::', '192.0.2.10'])
def test_remote_host_with_valid_long_lived_token_is_allowed(tmp_path, host):
    """长度至少 16 字符的有效长期 token 允许显式远程监听。"""
    config = tmp_path / 'config.ini'
    token = 'x' * 16
    write_config(config, token)
    resolved = resolve_server_options(ServerOptions(True, config, host=host), {})
    assert (resolved.host, resolved.access_token) == (host, token)


@pytest.mark.parametrize('url', [
    'ftp://example.test/', 'http://u:p@example.test/',
    'http://example.test/?x=1', 'http://example.test/#x',
    'http://example.test/a b', 'http://example.test/\n',
    'http://example.test:99999/', 'http:///missing-host',
    'https://' + 'a' * 2048,
])
def test_public_url_rejects_unsafe_forms(tmp_path, url):
    """公开 URL 拒绝不安全形式。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config, public_url=url), {})
    assert error.value.code == 'INVALID_PUBLIC_URL'


def test_public_url_normalizes_prefix_and_ipv6_defaults(tmp_path):
    """prefix、root/base path 和 IPv6 本机 URL 规则固定。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    resolved = resolve_server_options(
        ServerOptions(True, config, public_url='https://example.test/a/b'), {})
    assert (resolved.public_url, resolved.root_path, resolved.base_path) == (
        'https://example.test/a/b/', '/a/b', '/a/b/')
    assert build_local_url('::1', 42) == 'http://[::1]:42/'
    assert build_local_url('0.0.0.0', 42) == 'http://127.0.0.1:42/'


def test_directory_defaults_config_environment_and_empty_environment(tmp_path, monkeypatch):
    """覆盖 cwd 默认、配置目录、环境目录及空环境回退。"""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / 'cfg'
    config_dir.mkdir()
    config = config_dir / 'config.ini'
    write_config(config, temp_folder='configured')
    defaults = resolve_server_options(ServerOptions(True, config), {})
    empty = resolve_server_options(ServerOptions(True, config), {
        'TWOPUSH_LOG_DIR': ' ', 'TWOPUSH_TEMP_DIR': ''})
    overridden = resolve_server_options(ServerOptions(True, config), {
        'TWOPUSH_LOG_DIR': '../logs', 'TWOPUSH_TEMP_DIR': 'environment'})
    assert defaults.log_root == (tmp_path / 'logs').resolve()
    assert defaults.temp_dir == (config_dir / 'configured').resolve()
    assert empty.temp_dir == defaults.temp_dir
    assert overridden.log_root == (config_dir / '../logs').resolve()
    assert overridden.temp_dir == (config_dir / 'environment').resolve()
    assert not (overridden.temp_dir / 'Temp').exists()
