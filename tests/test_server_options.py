#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""服务选项解析测试。"""

import secrets
import socket
from pathlib import Path

import pytest

from modules.server_options import (
    ServerConfigError,
    ServerOptions,
    build_local_url,
    probe_directory,
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


def test_public_url_environment_overrides_local_default(tmp_path):
    """非空环境公开 URL 覆盖本机默认 URL。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    resolved = resolve_server_options(ServerOptions(True, config), {
        'TWOPUSH_PUBLIC_URL': 'https://public.example/base',
    })
    assert resolved.public_url == 'https://public.example/base/'
    assert (resolved.root_path, resolved.base_path) == ('/base', '/base/')


@pytest.mark.parametrize('value', ['not-a-url', 'ftp://example.test/'])
def test_nonempty_public_url_environment_does_not_fall_back(tmp_path, value):
    """非法非空公开 URL 返回错误而不是回退本机 URL。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config), {
            'TWOPUSH_PUBLIC_URL': value,
        })
    assert error.value.code == 'INVALID_PUBLIC_URL'


def test_resolve_does_not_create_runtime_directories(tmp_path, monkeypatch):
    """解析阶段保持纯解析，不创建运行目录。"""
    monkeypatch.chdir(tmp_path)
    config = tmp_path / 'config.ini'
    write_config(config, temp_folder='runtime')
    resolved = resolve_server_options(ServerOptions(True, config), {})
    assert not resolved.temp_dir.exists()
    assert not resolved.log_root.exists()


def test_probe_directory_success_leaves_no_probe_file(tmp_path):
    """探针成功后不残留探针文件。"""
    probe_directory(tmp_path / 'runtime', 'TEMP_UNAVAILABLE')
    assert list((tmp_path / 'runtime').glob('.twopush-probe-*')) == []


@pytest.mark.parametrize('operation', ['mkdir', 'write', 'delete'])
def test_probe_directory_failures_have_stable_error(operation, tmp_path, monkeypatch):
    """探针创建、写入、删除失败均返回稳定错误。"""
    path = tmp_path / 'runtime'
    probe = path / '.twopush-probe-fixed'
    if operation == 'mkdir':
        def fail_mkdir(*args, **kwargs):
            raise OSError('secret mkdir detail')
        monkeypatch.setattr(Path, 'mkdir', fail_mkdir)
    elif operation == 'write':
        path.mkdir()
        monkeypatch.setattr(Path, 'open', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('secret write detail')))
    else:
        path.mkdir()
        monkeypatch.setattr(secrets, 'token_hex', lambda size: 'fixed')
        monkeypatch.setattr(Path, 'unlink', lambda self: (_ for _ in ()).throw(OSError('secret delete detail')))
    with pytest.raises(ServerConfigError) as error:
        probe_directory(path, 'TEMP_UNAVAILABLE')
    assert (error.value.code, error.value.exit_code, error.value.safe_message) == (
        'TEMP_UNAVAILABLE', 2, '运行目录不可用')
    assert 'secret' not in str(error.value)


def test_probe_write_failure_cleans_probe_file(tmp_path, monkeypatch):
    """探针写入或 flush 失败后尽力清理且不残留文件。"""
    path = tmp_path / 'runtime'
    path.mkdir()
    original_open = Path.open

    class FailingFlush:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, value):
            return self.stream.write(value)

        def flush(self):
            raise OSError('secret flush detail')

    def open_and_fail_flush(self, *args, **kwargs):
        return FailingFlush(original_open(self, *args, **kwargs))

    monkeypatch.setattr(Path, 'open', open_and_fail_flush)
    with pytest.raises(ServerConfigError) as error:
        probe_directory(path, 'TEMP_UNAVAILABLE')
    assert list(path.glob('.twopush-probe-*')) == []
    assert (error.value.code, error.value.exit_code, error.value.safe_message) == (
        'TEMP_UNAVAILABLE', 2, '运行目录不可用')
    assert error.value.__cause__.args == ('secret flush detail',)


def test_probe_cleanup_failure_preserves_write_failure(tmp_path, monkeypatch):
    """探针清理失败时仍保留首个写入异常语义。"""
    path = tmp_path / 'runtime'
    path.mkdir()
    original_open = Path.open

    class FailingWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, value):
            raise OSError('secret write detail')

        def flush(self):
            self.stream.flush()

    def open_and_fail_write(self, *args, **kwargs):
        return FailingWrite(original_open(self, *args, **kwargs))

    monkeypatch.setattr(Path, 'open', open_and_fail_write)
    monkeypatch.setattr(Path, 'unlink', lambda self: (_ for _ in ()).throw(OSError('secret delete detail')))
    with pytest.raises(ServerConfigError) as error:
        probe_directory(path, 'TEMP_UNAVAILABLE')
    assert (error.value.code, error.value.exit_code, error.value.safe_message) == (
        'TEMP_UNAVAILABLE', 2, '运行目录不可用')
    assert error.value.__cause__.args == ('secret write detail',)
    assert 'secret delete detail' not in str(error.value)


def test_hostname_with_any_non_loopback_address_is_rejected(tmp_path, monkeypatch):
    """多地址主机名包含非 loopback 地址时拒绝远程监听。"""
    config = tmp_path / 'config.ini'
    write_config(config)
    def addresses(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 0)),
        ]
    monkeypatch.setattr(socket, 'getaddrinfo', addresses)
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config, host='multi.example'), {})
    assert error.value.code == 'AUTH_REQUIRED'


def test_config_interpolation_failure_is_safe_configuration_error(tmp_path):
    """配置插值异常统一转换为不泄露详情的 CONFIG_INVALID。"""
    config = tmp_path / 'config.ini'
    config.write_text('[Web]\naccess_token = %\n', encoding='utf-8')
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config), {})
    assert (error.value.code, error.value.exit_code) == ('CONFIG_INVALID', 2)
    assert error.value.safe_message == '服务配置文件无效'
    assert '%' not in error.value.safe_message
    assert str(config) not in error.value.safe_message


    """配置错误消息不包含访问令牌。"""
    token = 'secret-token-value-1234'
    config = tmp_path / 'config.ini'
    write_config(config, token)
    with pytest.raises(ServerConfigError) as error:
        resolve_server_options(ServerOptions(True, config, host='bad host'), {})
    assert token not in str(error.value)
    assert token not in error.value.safe_message
