#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""FastAPI 服务启动选项的固定接口。"""

import configparser
import ipaddress
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 52233


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

    def __post_init__(self) -> None:
        """确保配置路径契约始终使用 Path。"""
        if not isinstance(self.config_path, Path):
            raise TypeError('config_path 必须是 Path')


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


def _error(code: str, message: str) -> ServerConfigError:
    """创建不包含敏感路径和值的配置错误。"""
    return ServerConfigError(code, 2, message)


def _value(environ: Mapping[str, str], name: str) -> str | None:
    """读取非空环境变量。"""
    value = environ.get(name)
    return value.strip() if value is not None and value.strip() else None


def _parse_port(value: str) -> int:
    """解析 ASCII 十进制端口。"""
    if not value or not value.isascii() or not value.isdecimal():
        raise _error('INVALID_PORT', '服务端口配置无效')
    port = int(value)
    if port > 65535:
        raise _error('INVALID_PORT', '服务端口配置无效')
    return port


def _host_is_loopback(host: str) -> bool:
    """判断监听地址是否仅指向 loopback。"""
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            results = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as error:
            raise _error('INVALID_HOST', '监听地址配置无效') from error
        addresses = {item[4][0] for item in results}
        return bool(addresses) and all(ipaddress.ip_address(address).is_loopback
                                       for address in addresses)


def _validate_host(host: str) -> None:
    """验证 IPv4、IPv6 或可解析主机名。"""
    if not host or host.startswith('[') or host.endswith(']') or any(char.isspace() or ord(char) < 32 for char in host):
        raise _error('INVALID_HOST', '监听地址配置无效')
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    try:
        socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as error:
        raise _error('INVALID_HOST', '监听地址配置无效') from error


def _read_config(path: Path) -> tuple[str, str | None]:
    """读取 token 和临时目录配置。"""
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding='utf-8')
    except (OSError, configparser.Error) as error:
        raise _error('CONFIG_INVALID', '服务配置文件无效') from error
    try:
        token = parser.get('Web', 'access_token', fallback='').strip()
        temp = parser.get('Paths', 'temp_folder', fallback='').strip() or None
    except configparser.Error as error:
        raise _error('CONFIG_INVALID', '服务配置文件无效') from error
    return token if len(token) >= 16 else '', temp


def _normalize_public_url(value: str) -> tuple[str, str, str]:
    """校验并规范化公开 URL，同时提取代理路径前缀。"""
    if len(value) > 2048 or any(not char.isascii() or char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise _error('INVALID_PUBLIC_URL', '公开 URL 配置无效')
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.query or parsed.fragment or parsed.port is None and ':' in parsed.netloc.rsplit(']', 1)[-1]:
            raise ValueError
        _ = parsed.port
        if not parsed.hostname:
            raise ValueError
    except (ValueError, UnicodeError):
        raise _error('INVALID_PUBLIC_URL', '公开 URL 配置无效')
    path = parsed.path or '/'
    if not path.startswith('/'):
        path = '/' + path
    path = path.rstrip('/') + '/'
    root = path.rstrip('/') or ''
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', '')), root, path


def build_local_url(host: str, port: int) -> str:
    """构造不携带认证信息的本机 URL。"""
    if host == '0.0.0.0':
        host = '127.0.0.1'
    elif host == '::':
        host = '::1'
    try:
        address = ipaddress.ip_address(host)
        rendered = f'[{host}]' if address.version == 6 else host
    except ValueError:
        rendered = host
    return f'http://{rendered}:{port}/'


def probe_directory(path: Path, error_code: str) -> None:
    """创建目录并用随机探针验证写入能力。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise _error(error_code, '运行目录不可用') from error
    probe = path / f'.twopush-probe-{secrets.token_hex(8)}'
    try:
        with probe.open('w', encoding='utf-8') as stream:
            stream.write('probe')
            stream.flush()
    except OSError as error:
        try:
            probe.unlink()
        except OSError:
            pass
        raise _error(error_code, '运行目录不可用') from error
    try:
        probe.unlink()
    except OSError as error:
        raise _error(error_code, '运行目录不可用') from error


def resolve_server_options(options: ServerOptions, environ: Mapping[str, str]) -> ResolvedServerOptions:
    """按固定优先级解析并校验服务选项。"""
    config_path = Path(options.config_path)
    token, configured_temp = _read_config(config_path)
    host = options.host if options.host is not None else _value(environ, 'TWOPUSH_SERVER_HOST')
    if host is None:
        host = '0.0.0.0' if token else DEFAULT_HOST
    host = host.strip() if options.host is not None else host
    _validate_host(host)
    if not _host_is_loopback(host) and not token:
        raise _error('AUTH_REQUIRED', '远程监听需要有效访问令牌')

    raw_port = options.port if options.port is not None else _value(environ, 'TWOPUSH_SERVER_PORT')
    port_explicit = raw_port is not None
    port = _parse_port(raw_port) if raw_port is not None else DEFAULT_PORT

    public_value = options.public_url if options.public_url is not None else _value(environ, 'TWOPUSH_PUBLIC_URL')
    if public_value is None:
        public_url = build_local_url(host, port)
        root_path, base_path = '', '/'
    else:
        public_url, root_path, base_path = _normalize_public_url(public_value)

    config_dir = config_path.resolve(strict=False).parent
    temp_raw = _value(environ, 'TWOPUSH_TEMP_DIR') or configured_temp or 'Temp'
    temp_dir = (config_dir / temp_raw).resolve(strict=False)
    log_raw = _value(environ, 'TWOPUSH_LOG_DIR') or 'logs'
    log_root = (config_dir / log_raw).resolve(strict=False) if log_raw != 'logs' else Path.cwd().joinpath('logs').resolve()
    return ResolvedServerOptions(options.server_mode, config_path, host, port, port_explicit,
                                 public_url, root_path, base_path, temp_dir, log_root,
                                 token, options.emit_launch_token, options.silent)
