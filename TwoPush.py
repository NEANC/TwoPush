#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""TwoPush - 基于 onepush 的通知推送包装程序

通过 INI 管理全局配置，JSON 管理每次推送的内容和通道，
支持命令行调用和自我更新。
"""

import argparse
import asyncio
import errno
import inspect
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from contextlib import contextmanager
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from modules.config_manager import ConfigManager
from modules.logger_manager import (
    add_file_logger,
    cleanup_gui_logs,
    cleanup_old_logs,
    close_gui_logger,
    raw_read_save_enabled,
    sanitize_log_message,
    set_max_files,
    setup_gui_logger,
    setup_logger,
)
from modules.notification import (
    describe_channel_routes,
    render_template_vars,
    send_notification,
)
from modules.utils import mask_sensitive_fields, parse_push_channels, parse_time_string
from modules.json_manager import (
    DEFAULT_TEMPLATE_FILE,
    ensure_default_template_on_first_run,
    handle_template_command,
    load_json_template,
)
from modules.server_options import ServerOptions, resolve_server_options
from modules.server_core import run_fastapi_server, run_server_with_protocol
from modules.version import VERSION

DEFAULT_CONFIG_FILE = "config.ini"
WEB_DEFAULT_PORT = 52233


def _supported_kwargs(callable_object, kwargs):
    """按构造签名过滤明确不支持的参数。"""
    try:
        signature = inspect.signature(callable_object)
    except (TypeError, ValueError):
        return dict(kwargs)
    parameters = signature.parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return dict(kwargs)
    supported = {
        parameter.name for parameter in parameters
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in kwargs.items() if key in supported}


def should_start_web():
    """判断是否应在无参数模式启动 Web 服务。"""
    return len(sys.argv) == 1


def _select_dynamic_web_port():
    """在确认默认端口占用后选择系统动态端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _is_port_in_use_error(error):
    """判断 Uvicorn 启动失败是否由端口占用引起。"""
    return isinstance(error, OSError) and (
        getattr(error, 'winerror', None) == 10048 or error.errno == errno.EADDRINUSE
    )


def _cleanup_web_server(server, preserve_exception=False):
    """请求 Uvicorn 服务退出，并兼容旧测试替身。"""
    if server is None:
        return
    try:
        server.should_exit = True
        shutdown = getattr(server, 'shutdown', None)
        if shutdown is not None:
            result = shutdown()
            if inspect.isawaitable(result):
                asyncio.run(result)
            return
        close = getattr(server, 'close', None)
        if close is not None:
            close()
    except Exception:
        if not preserve_exception:
            raise


def run_web_server(config_path=DEFAULT_CONFIG_FILE):
    """初始化并运行 Web 服务，保留浏览器模式异常与清理契约。"""
    from modules.push_process import PushProcessManager
    from modules.web_server import WebServerControl, create_app

    logger = setup_gui_logger()
    process_manager = None
    control = None
    server = None
    first_error = None
    try:
        resolved = resolve_server_options(
            ServerOptions(False, Path(config_path), emit_launch_token=True),
            os.environ,
        )
        config_kwargs = {
            'config_file': config_path,
            'logger': logger,
            'app_name': 'TwoPush',
            'non_interactive': True,
            'temp_dir': resolved.temp_dir,
        }
        config = ConfigManager(**_supported_kwargs(ConfigManager, config_kwargs))
        config.load()
        if not config.validate():
            return 2

        process_kwargs = {
            'gui_mode': True,
            'logger': logger,
            'temp_dir': resolved.temp_dir,
        }
        process_manager = PushProcessManager(**_supported_kwargs(PushProcessManager, process_kwargs))
        control = WebServerControl()
        from modules.server_auth import ServerAuthStore
        auth_store = ServerAuthStore(resolved.access_token)
        app = create_app(
            os.path.dirname(os.path.abspath(config_path)), config_path,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web'),
            process_manager, server_control=control, logger=logger,
            root_path=resolved.root_path, base_path=resolved.base_path,
            public_url_is_https=bool(resolved.public_url and resolved.public_url.startswith('https://')),
            temp_dir=resolved.temp_dir, auth_store=auth_store,
        )
        port = resolved.port
        host = resolved.host
        import uvicorn

        def build_server(server_port):
            """按指定端口创建 Uvicorn 服务。"""
            return uvicorn.Server(uvicorn.Config(app, host=host, port=server_port,
                                                  access_log=False, log_config=None))

        def run_server(server_instance, errors):
            """运行指定服务实例并记录启动异常。"""
            try:
                server_instance.run()
            except BaseException as error:
                errors.append(error)

        server = build_server(port)
        control.exit_callback = lambda: setattr(server, 'should_exit', True)
        server_error = []
        server_thread = threading.Thread(target=run_server, args=(server, server_error), daemon=True)
        server_thread.start()
        while not getattr(server, 'started', False):
            if server_error or server.should_exit or not server_thread.is_alive():
                break
            time.sleep(0.05)
        if server_error and _is_port_in_use_error(server_error[0]):
            previous_server = server
            port = _select_dynamic_web_port()
            server = build_server(port)
            control.exit_callback = lambda: setattr(server, 'should_exit', True)
            server_error = []
            _cleanup_web_server(previous_server, preserve_exception=True)
            server_thread = threading.Thread(target=run_server, args=(server, server_error), daemon=True)
            server_thread.start()
            while not getattr(server, 'started', False):
                if server_error or server.should_exit or not server_thread.is_alive():
                    break
                time.sleep(0.05)
        actual_port = port
        server_sockets = getattr(server, 'servers', None) or []
        if server_sockets:
            actual_port = server_sockets[0].sockets[0].getsockname()[1]
        launch_token = auth_store.issue_launch_token() if resolved.emit_launch_token else None
        if launch_token:
            from urllib.parse import parse_qsl
            parsed_url = urlsplit(resolved.public_url or f'http://127.0.0.1:{actual_port}/')
            query = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
            query['launch_token'] = launch_token
            url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path or '/', urlencode(query), ''))
        else:
            default_public_url = f'http://127.0.0.1:{resolved.port}/'
            url = (resolved.public_url if resolved.public_url != default_public_url
                   else f'http://127.0.0.1:{actual_port}{resolved.base_path}')
        if not getattr(server, 'started', False):
            if server_thread.is_alive():
                server_thread.join(timeout=1)
            return 1
        webbrowser.open(url)
        server_thread.join()
        return 0
    except BaseException as error:
        first_error = error
        raise
    finally:
        for cleanup in (
            lambda: _cleanup_web_server(server, preserve_exception=first_error is not None),
            lambda: control.stop() if control is not None else None,
            lambda: process_manager.stop() if process_manager is not None else None,
            lambda: process_manager.shutdown() if process_manager is not None else None,
        ):
            try:
                cleanup()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        try:
            close_gui_logger(logger)
        except BaseException as error:
            if first_error is None:
                first_error = error
        if first_error is not None and sys.exc_info()[0] is None:
            raise first_error


_PROXY_SENSITIVE_QUERY_KEYS = frozenset({
    'password', 'passwd', 'token', 'access_token', 'accesskey',
    'access_key', 'sign', 'secret', 'key', 'api_key', 'apikey',
    'auth', 'credential', 'token_key', 'secret_key',
})


def parse_args():
    """解析命令行参数

    Returns:
        argparse.Namespace: 命令行参数命名空间
    """
    parser = argparse.ArgumentParser(
        description='TwoPush - 基于 onepush 的通知推送工具',
        add_help=False,
    )
    parser.add_argument(
        '-h', '-H', '--help', '--Help',
        action='help',
        default=argparse.SUPPRESS,
        help='显示帮助信息',
    )
    parser.add_argument(
        '-v', '--version', action='store_true',
        help='显示版本号',
    )
    parser.add_argument(
        '-c', '-C', '--config', '--Config',
        default=DEFAULT_CONFIG_FILE,
        help='指定配置文件路径，示例 -c C:\\path\\config.ini',
    )
    parser.add_argument(
        '-p', '-P', '--push', '--Push',
        default=None,
        help='指定推送 JSON 文件路径，示例 -p C:\\path\\report.json',
    )
    parser.add_argument('--server', action='store_true', help='启动 FastAPI 服务')
    parser.add_argument('--host', default=None, help='服务监听地址')
    parser.add_argument('--port', default=None, help='服务监听端口')
    parser.add_argument('--public-url', default=None, help='服务公开访问 URL')
    parser.add_argument('--emit-launch-token', action='store_true', help='生成一次性启动令牌')
    parser.add_argument(
        '--update', '--Update', action='store_true', dest='update',
        help='检查并执行自我更新',
    )
    parser.add_argument(
        '--update-force', '--UpdateForce', action='store_true', dest='update_force',
        help='强制更新到最新版本',
    )
    parser.add_argument(
        '-S', '--silent', '--Silent',
        action='store_true',
        dest='silent',
        help='静默模式，不输出控制台日志',
    )
    parser.add_argument(
        '-T', '--template', '--Template',
        nargs='?',
        const=DEFAULT_TEMPLATE_FILE,
        default=None,
        dest='template',
        help='生成 JSON 模板文件，未指定路径时生成 TwoPush.templates.json，示例 -T C:\\path\\template.json',
    )
    parser.add_argument(
        '--template-force', '--Template-Force',
        nargs='?',
        const=DEFAULT_TEMPLATE_FILE,
        default=None,
        dest='template_force',
        help='生成 JSON 模板文件并允许覆盖已有文件，示例 --Template-Force C:\\path\\template.json',
    )
    # 自更新相关参数
    parser.add_argument('--self-update-verify', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--expected-sha256', type=str, default='', help=argparse.SUPPRESS)
    parser.add_argument('--expected-version', type=str, default='', help=argparse.SUPPRESS)
    parser.add_argument('--retry-update', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--update-failed', action='store_true', help=argparse.SUPPRESS)
    # 用于文件拖放
    parser.add_argument('jsonfile',nargs='?',default=None,help=argparse.SUPPRESS)

    args = parser.parse_args()
    server_values = (args.host, args.port, args.public_url, args.emit_launch_token)
    ordinary_values = (args.push, args.jsonfile, args.update, args.update_force,
                       args.template, args.template_force, args.version)
    if args.server and any(value for value in ordinary_values):
        parser.error('--server 不能与普通 CLI 参数混用')
    if not args.server and any(value is not None for value in server_values[:3]):
        parser.error('--host、--port、--public-url 只能与 --server 一起使用')
    if not args.server and args.emit_launch_token:
        parser.error('--emit-launch-token 只能与 --server 一起使用')
    return args


def is_config_path_explicit(argv):
    """判断命令行是否显式指定了配置文件路径

    Args:
        argv: 命令行参数列表，通常为 sys.argv

    Returns:
        bool: 显式传入配置文件参数时返回 True
    """
    config_flags = {'-c', '-C', '--config', '--Config'}
    short_config_flags = {'-c', '-C'}
    for arg in argv[1:]:
        if arg in config_flags:
            return True
        if any(arg.startswith(f'{flag}=') for flag in config_flags):
            return True
        if any(arg.startswith(flag) and arg != flag for flag in short_config_flags):
            return True
    return False


def resolve_proxy(json_template, config):
    """解析推送代理设置

    优先级：JSON proxy > INI enable_proxy_for_push + proxy > 不使用

    Args:
        json_template: JSON 模板字典
        config: ConfigManager 实例

    Returns:
        str | None: 代理地址，不使用代理时返回 None

    Raises:
        ValueError: JSON 模板中的 proxy 不是字符串时抛出。
    """
    json_proxy = json_template.get('proxy')
    if json_proxy:
        if not isinstance(json_proxy, str):
            # 非字符串代理值会在 urlsplit 处抛 AttributeError/TypeError，
            # 在此提前拦下并转为可被调用方友好处理的 ValueError
            raise ValueError('JSON 模板中的 proxy 必须是字符串')
        return json_proxy
    if config.get_attr_bool('enable_proxy_for_push', False):
        return config.get_attr('proxy', '') or None
    return None


@contextmanager
def push_proxy_environment(proxy, logger):
    """临时设置推送代理环境变量，仅适合同进程串行推送

    支持 HTTP/HTTPS/SOCKS5 代理协议。同时设置 HTTP_PROXY、HTTPS_PROXY
    和 ALL_PROXY，确保 DNS 解析也走代理隧道，避免 DNS 泄漏。

    Args:
        proxy: HTTP/HTTPS/SOCKS5 代理服务器地址，为空时不修改环境变量
        logger: 日志记录器
    """
    if not proxy:
        yield
        return

    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_all_proxy = os.environ.get('ALL_PROXY')
    os.environ['HTTP_PROXY'] = proxy
    os.environ['HTTPS_PROXY'] = proxy
    os.environ['ALL_PROXY'] = proxy
    logger.info("已启用推送代理")
    try:
        yield
    finally:
        if old_http_proxy is None:
            os.environ.pop('HTTP_PROXY', None)
        else:
            os.environ['HTTP_PROXY'] = old_http_proxy
        if old_https_proxy is None:
            os.environ.pop('HTTPS_PROXY', None)
        else:
            os.environ['HTTPS_PROXY'] = old_https_proxy
        if old_all_proxy is None:
            os.environ.pop('ALL_PROXY', None)
        else:
            os.environ['ALL_PROXY'] = old_all_proxy


def _mask_proxy_query(query):
    """脱敏 query 中敏感键的值；无敏感键时原样返回，不规范化。

    Args:
        query: URL 的 query 字符串。

    Returns:
        str: 脱敏后的 query 字符串。
    """
    if not query:
        return query
    masked_parts = []
    has_sensitive_key = False
    for part in query.split('&'):
        key, separator, value = part.partition('=')
        decoded_key = key
        decode_count = 0
        for _ in range(2):
            next_key = unquote_plus(decoded_key)
            if next_key == decoded_key:
                break
            decoded_key = next_key
            decode_count += 1
        if decoded_key.strip().lower() in _PROXY_SENSITIVE_QUERY_KEYS:
            has_sensitive_key = True
            output_key = decoded_key if decode_count == 1 else key
            masked_parts.append(
                f'{output_key}{separator}***' if separator else f'{output_key}=***'
            )
        else:
            masked_parts.append(part)
    if not has_sensitive_key:
        return query
    return '&'.join(masked_parts)


def mask_proxy_authentication(proxy):
    """脱敏代理 URL 中的认证信息、敏感 query 参数并移除 fragment。

    Args:
        proxy: 代理地址。

    Returns:
        str | None: 脱敏后的代理地址。
    """
    if not proxy:
        return proxy
    if not isinstance(proxy, str):
        # 脱敏是安全边界，非字符串输入宁可返回占位符也不能让
        # urlsplit 抛 AttributeError/TypeError 使原值随异常暴露
        return '***'

    try:
        parsed = urlsplit(proxy)
    except ValueError:
        return '***'
    has_auth = bool(parsed.username) or parsed.password is not None
    masked_query = _mask_proxy_query(parsed.query)
    if not has_auth and masked_query == parsed.query and not parsed.fragment:
        return proxy

    if has_auth:
        try:
            port = parsed.port
        except ValueError:
            # 畸形端口（非数字）无法安全重建 URL，返回固定脱敏占位符
            return '***'
        host = parsed.hostname or ''
        if ':' in host and not host.startswith('['):
            host = f'[{host}]'
        if port is not None:
            host = f'{host}:{port}'
        netloc = f'***:***@{host}'
    else:
        netloc = parsed.netloc
    return urlunsplit((
        parsed.scheme,
        netloc,
        parsed.path,
        masked_query,
        '',
    ))


def format_push_preview(title, content, proxy, retry_settings, channels):
    """格式化推送前预览内容。

    Args:
        title: 渲染后的通知标题。
        content: 渲染后的通知内容。
        proxy: 最终生效的代理值。
        retry_settings: 最终重试配置。
        channels: 标准通道字典列表。

    Returns:
        str: 无最外层大括号的 5 行 JSON 风格预览。
    """
    channel_names = describe_channel_routes(channels)
    retry_preview = {
        'interval': retry_settings.get('interval'),
        'max_count': retry_settings.get('max_count'),
    }
    proxy_preview = mask_proxy_authentication(proxy)
    # 仅对字符串类型的 title/content 脱敏，避免预览日志泄露敏感信息；
    # 非字符串值原样保留，不被 str() 转换
    masked_title = (
        mask_sensitive_fields({'title': title}, {'title'})['title']
        if isinstance(title, str)
        else title
    )
    masked_content = (
        mask_sensitive_fields({'content': content}, {'content'})['content']
        if isinstance(content, str)
        else content
    )
    return '\n'.join([
        f'"title": {json.dumps(masked_title, ensure_ascii=False)},',
        f'"content": {json.dumps(masked_content, ensure_ascii=False)},',
        f'"proxy": {json.dumps(proxy_preview, ensure_ascii=False)},',
        f'"retry": {json.dumps(retry_preview, ensure_ascii=False)},',
        f'"channels": {json.dumps(channel_names, ensure_ascii=False)}',
    ])


def init_self_updater(config, logger):
    """初始化 SelfUpdater 实例

    Args:
        config: ConfigManager 实例
        logger: 日志记录器

    Returns:
        SelfUpdater | None: 非打包环境返回 None
    """
    from modules.self_updater import SelfUpdater
    from modules.self_utils import detect_package_type

    is_bundled, package_type = detect_package_type()
    if not is_bundled:
        logger.debug("源码运行，跳过自我更新")
        return None

    return SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-(Nuitka|PyInstaller)-v[\d.]+.*\.exe$',
        app_name="TwoPush",
        current_version=VERSION,
        proxy=config.get_attr('proxy', ''),
        temp_folder=config.get_attr('temp_folder', ''),
        logger=logger,
        self_update_channel=config.get_attr('channel', 'stable'),
        is_bundled=is_bundled,
        package_type=package_type,
    )


def handle_self_update_verify(args):
    """处理 --self-update-verify（PS1 脚本调用）

    Args:
        args: 命令行参数
    """
    from modules.self_updater import SelfUpdater
    exit_code = SelfUpdater.self_update_verify(
        expected_sha256=args.expected_sha256,
        expected_version=args.expected_version,
    )
    sys.exit(exit_code)


def handle_update_failed(logger):
    """处理 --update-failed（PS1 脚本调用）

    Args:
        logger: 日志记录器
    """
    from modules.self_config import UpdateState
    state = UpdateState.load()
    if state:
        failed_ver = state["new_version"]
        logger.critical(f"自更新失败：版本 {failed_ver} 多次验证不通过")
    else:
        logger.critical("自更新失败，但无法读取状态信息")
    sys.exit(1)


def handle_retry_update(config, logger):
    """处理 --retry-update（PS1 脚本回滚后重试）

    Args:
        config: ConfigManager 实例
        logger: 日志记录器
    """
    logger.info("正在重试自更新...")
    updater = init_self_updater(config, logger)
    if updater and updater.check_self_update():
        sys.exit(0)
    logger.error("重试更新失败")
    sys.exit(1)


def handle_update_command(config, logger, force=False):
    """处理 --update / --update-force

    Args:
        config: ConfigManager 实例
        logger: 日志记录器
        force: 是否强制更新
    """
    updater = init_self_updater(config, logger)
    if updater is None:
        logger.warning("当前为源码运行模式，无法执行自我更新")
        sys.exit(0)
    if updater.check_self_update(force=force):
        logger.info("已将新版本下载到临时文件夹，即将退出以完成更新...")
        sys.exit(0)
    logger.info("当前已是最新版本")
    sys.exit(0)


def auto_update_check(config, logger):
    """自动更新检查（若 INI 中 auto_check=true）

    Args:
        config: ConfigManager 实例
        logger: 日志记录器
    """
    if not config.get_attr_bool('auto_check', True):
        return
    updater = init_self_updater(config, logger)
    if updater is None:
        return
    if updater.check_self_update():
        logger.info("检测到新版本，即将退出以完成更新...")
        sys.exit(0)


def execute_push(json_path, config, logger):
    """执行推送操作

    Args:
        json_path: JSON 模板文件路径
        config: ConfigManager 实例
        logger: 日志记录器

    Returns:
        int: 退出码
    """
    template = load_json_template(json_path, logger)
    if template is None:
        return 2

    channels = parse_push_channels(template.get('channels'))
    if not channels:
        logger.error("推送通道解析结果为空，无法发送通知")
        return 2

    retry_settings = {}
    json_retry = template.get('retry')
    if json_retry and not isinstance(json_retry, dict):
        # JSON 模板不校验 retry 类型，字符串/列表/数字等真值没有 get 方法，
        # 直接取值会抛 AttributeError 穿透调用栈
        logger.error("模板字段 retry 必须是对象")
        return 2
    if json_retry:
        interval_str = json_retry.get('interval', '3s')
        try:
            retry_settings['interval'] = int(parse_time_string(interval_str))
        except (TypeError, ValueError):
            retry_settings['interval'] = 3
        try:
            retry_settings['max_count'] = max(int(json_retry.get('max_count', 3)), 1)
        except (TypeError, ValueError):
            retry_settings['max_count'] = 3
    else:
        interval_str = config.get_attr('retry_interval', '3s')
        try:
            retry_settings['interval'] = int(parse_time_string(interval_str))
        except (TypeError, ValueError):
            retry_settings['interval'] = 3
        retry_settings['max_count'] = max(config.get_attr_int('retry_max_count', 3), 1)

    vars_ = render_template_vars()
    for field in ('title', 'content'):
        if not isinstance(template.get(field), str):
            # JSON 模板只做真值校验，数字/列表/字典等非字符串值能通过校验，
            # 但没有 str.format 方法，直接调用会抛 AttributeError 穿透调用栈
            logger.error(f"模板字段 {field} 必须是字符串")
            return 2

    try:
        title = template['title'].format(**vars_)
        content = template['content'].format(**vars_)
    except KeyError as e:
        logger.error(f"模板变量缺失: {e}")
        return 2
    except (ValueError, IndexError) as e:
        # 占位符语法非法（如括号未闭合、使用位置参数、未知转换符）时
        # format 抛出 ValueError/IndexError，须与变量缺失一样给出友好提示
        logger.error(f"模板占位符语法错误: {e}")
        return 2

    try:
        proxy = resolve_proxy(template, config)
    except ValueError as e:
        logger.error(f"代理配置无效: {e}")
        return 2

    preview = format_push_preview(
        title=title,
        content=content,
        proxy=proxy,
        retry_settings=retry_settings,
        channels=channels,
    )
    logger.info("推送预览：\n" + preview)
    with push_proxy_environment(proxy, logger):
        results = send_notification(
            title=title,
            content=content,
            channels=channels,
            retry_settings=retry_settings,
            logger=logger,
        )

    success_count = sum(1 for _, ok in results if ok)
    fail_count = len(results) - success_count
    logger.info(f"推送完成: {success_count} 成功, {fail_count} 失败")
    return 0 if fail_count == 0 else 1


def main():
    """主入口"""
    if should_start_web():
        return run_web_server()
    args = parse_args()
    if args.server:
        return run_fastapi_server(ServerOptions(
            True, Path(args.config), host=args.host, port=args.port,
            public_url=args.public_url, emit_launch_token=args.emit_launch_token,
            silent=args.silent,
        ))

    # 自更新内部参数
    if args.self_update_verify:
        handle_self_update_verify(args)
    if args.update_failed:
        handle_update_failed(setup_logger(console_enabled=not args.silent))

    if args.version:
        print(f"TwoPush {VERSION}")
        sys.exit(0)

    if not args.silent:
        print("TwoPush - 基于 onepush 的通知推送工具")
        print(f"版本: {VERSION}")

    save_enabled = raw_read_save_enabled(args.config)
    logger = setup_logger(console_enabled=not args.silent)
    if os.environ.get('TWOPUSH_SAVE_LOGS') != '0' and save_enabled:
        add_file_logger(logger, version=VERSION, log_dir='logs', log_prefix='TwoPush')

    handle_template_command(args, logger)

    if is_config_path_explicit(sys.argv) and not os.path.exists(args.config):
        logger.critical(f"指定的配置文件不存在: {args.config}")
        sys.exit(1)

    config = ConfigManager(
        config_file=args.config,
        logger=logger,
        app_name="TwoPush",
        first_run_callback=lambda: ensure_default_template_on_first_run(logger),
    )
    config.load()
    if not config.validate():
        sys.exit(2)

    if save_enabled:
        max_files = config.get_attr_int('max_files', 15)
        if max_files > 0:
            cleanup_old_logs(logger, max_files, log_dir='logs', log_prefix='TwoPush')

    push_file = args.push or args.jsonfile
    is_drag_drop = bool(args.jsonfile and not args.push)
    push_exit_code = None

    if push_file:
        if not os.path.exists(push_file):
            logger.critical(f"指定的 JSON 推送文件不存在: {push_file}")
            if is_drag_drop:
                input("按任意键退出...")
            sys.exit(1)
        push_exit_code = execute_push(push_file, config, logger)
        if is_drag_drop:
            print(f"\n推送完成，退出码: {push_exit_code}")
            input("按任意键退出...")

    # 检查并清理更新残留
    from modules.self_updater import SelfUpdater
    SelfUpdater._cleanup_update_residue(
        logger,
        temp_folder=config.get_attr('temp_folder', ''),
        clean_cache=not args.retry_update,
    )

    # push 失败时提前退出，避免退出码被更新命令覆盖
    if push_exit_code is not None and push_exit_code != 0:
        sys.exit(push_exit_code)

    if args.retry_update:
        handle_retry_update(config, logger)

    if args.update or args.update_force:
        handle_update_command(config, logger, force=args.update_force)

    # 自动更新检查仅在非推送模式下执行（--update/--update-force 始终生效）
    if push_file is None:
        auto_update_check(config, logger)

    if push_exit_code is not None:
        sys.exit(push_exit_code)
    sys.exit(0)


if __name__ == '__main__':
    main()
