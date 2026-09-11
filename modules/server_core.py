#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""FastAPI 服务生命周期核心。"""

import asyncio
import errno
import inspect
import logging
import os
import signal
import threading
import time
import webbrowser
from pathlib import Path

from modules.config_manager import ConfigManager
from modules.logger_manager import close_gui_logger, setup_gui_logger
from modules.push_process import PushProcessManager
from modules.server_auth import ServerAuthStore
from modules.server_options import ServerConfigError, resolve_server_options
from modules.server_protocol import ServerProtocolWriter
from modules.web_server import WebServerControl, create_app


DEFAULT_THREAD_JOIN_TIMEOUT = 1.0


class ServiceResult:
    """表示服务生命周期结束结果。"""

    def __init__(self, code='OK', exit_code=0):
        self.code = code
        self.exit_code = exit_code


class ServiceContext:
    """保存服务生命周期上下文。"""

    def __init__(self, value=None):
        self.value = value


def _prepare_fallback_thread(server, previous_thread, logger):
    """停止首服务线程并以有界时间等待其退出。"""
    server.should_exit = True
    previous_thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
    if previous_thread.is_alive():
        logger.error('首个服务线程未在回退前退出，线程残留诊断已记录')
        return False
    return True


def _is_bind_error(error):
    """判断异常是否为端口占用。"""
    return isinstance(error, OSError) and (
        error.errno == errno.EADDRINUSE or getattr(error, 'winerror', None) == 10048
    )


def _serve_uvicorn(app, host, port, control):
    """在当前线程运行 Uvicorn，并返回实际端口。"""
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, access_log=False, log_config=None)
    server = uvicorn.Server(config)
    control.exit_callback = lambda: setattr(server, 'should_exit', True)
    server.run()
    sockets = getattr(server, 'servers', None) or []
    if sockets and sockets[0].sockets:
        return sockets[0].sockets[0].getsockname()[1]
    return port


def _new_server(app, host, port, control):
    """创建可在后台线程运行的 Uvicorn 服务。"""
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, access_log=False, log_config=None)
    server = uvicorn.Server(config)
    control.exit_callback = lambda: setattr(server, 'should_exit', True)
    return server


def _supported_kwargs(callable_object, kwargs):
    """按构造签名过滤明确不支持的关键参数。"""
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


def _create_config(resolved, logger):
    """创建配置管理器，并兼容不支持临时目录参数的旧替身。"""
    config_kwargs = {
        'config_file': str(resolved.config_path),
        'logger': logger,
        'app_name': 'TwoPush',
        'non_interactive': True,
        'temp_dir': resolved.temp_dir,
    }
    return ConfigManager(**_supported_kwargs(ConfigManager, config_kwargs))


def _create_manager(resolved, logger):
    """创建进程管理器，并兼容旧替身构造签名。"""
    manager_kwargs = {
        'temp_dir': resolved.temp_dir,
        'gui_mode': True,
        'logger': logger,
        'terminal_streams': (os.sys.stderr, os.sys.stderr),
    }
    return PushProcessManager(**_supported_kwargs(PushProcessManager, manager_kwargs))


def _cleanup_server(server):
    """请求服务退出并兼容不同的服务替身接口。"""
    if server is None:
        return
    server.should_exit = True
    shutdown = getattr(server, 'shutdown', None)
    if shutdown is not None:
        result = shutdown()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
        return
    close = getattr(server, 'close', None)
    if close is not None:
        close()


def _run_fastapi_server(options, protocol_stream=None, open_browser=False):
    """解析选项、启动服务并在真实 socket 就绪后前台阻塞。"""

    if not options.server_mode:
        raise ValueError('服务入口需要 server_mode')
    protocol = ServerProtocolWriter(protocol_stream or os.sys.stdout)
    logger = setup_gui_logger()
    manager = None
    server = None
    control = None
    thread = None
    first_error = None
    old_handlers = {}
    try:
        resolved = resolve_server_options(options, os.environ)
        from modules.server_options import probe_directory
        probe_directory(resolved.temp_dir, 'TEMP_DIR_UNAVAILABLE')
        probe_directory(resolved.log_root, 'LOG_DIR_UNAVAILABLE')
        config = _create_config(resolved, logger)
        config.load()
        if not config.validate():
            return 2
        manager = _create_manager(resolved, logger)
        control = WebServerControl()
        auth_store = ServerAuthStore(resolved.access_token)
        resource_dir = Path(__file__).resolve().parent.parent / 'web'
        app = create_app(resolved.config_path.parent, resolved.config_path, resource_dir,
                         manager, server_control=control, logger=logger,
                         root_path=resolved.root_path, base_path=resolved.base_path,
                         public_url_is_https=bool(resolved.public_url and resolved.public_url.startswith('https://')),
                         temp_dir=resolved.temp_dir, auth_store=auth_store)

        def emit_stop(server_instance):
            """输出唯一停止事件并请求指定 Uvicorn 实例退出。"""
            try:
                protocol.stopping(reason='signal' if control.first_error == 'signal' else 'api')
            except RuntimeError:
                pass
            if server_instance is not None:
                server_instance.should_exit = True

        def stop_from_signal(signum, _frame):
            """记录首个系统信号并请求服务停止。"""
            if not control.stop_requested:
                control.first_error = 'signal'
            control.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                old_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, stop_from_signal)
            except (ValueError, OSError):
                pass

        def make_exit_callback(server_instance):
            """创建只绑定当前服务实例的退出回调。"""
            return lambda: emit_stop(server_instance)

        def serve(server_instance, errors):
            """运行指定 Uvicorn 实例并保存启动异常。"""
            try:
                server_instance.run()
            except BaseException as error:
                errors.append(error)

        server = _new_server(app, resolved.host, resolved.port, control)
        control.exit_callback = make_exit_callback(server)
        errors = []
        thread = threading.Thread(target=serve, args=(server, errors), daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not getattr(server, 'started', False) and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if errors and _is_bind_error(errors[0]) and not resolved.port_explicit:
            previous_server = server
            previous_thread = thread
            if not _prepare_fallback_thread(previous_server, previous_thread, logger):
                raise RuntimeError('服务回退清理失败')
            server = _new_server(app, resolved.host, 0, control)
            control.exit_callback = make_exit_callback(server)
            errors = []
            thread = threading.Thread(target=serve, args=(server, errors), daemon=True)
            thread.start()
            while not getattr(server, 'started', False) and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
        if errors or not getattr(server, 'started', False):
            raise errors[0] if errors else RuntimeError('服务启动失败')
        sockets = getattr(server, 'servers', None) or []
        actual_port = sockets[0].sockets[0].getsockname()[1] if sockets else resolved.port
        launch_token = auth_store.issue_launch_token() if resolved.emit_launch_token else None
        url = resolved.public_url or f'http://127.0.0.1:{actual_port}/'
        if launch_token:
            separator = '&' if '?' in url else '?'
            url = f'{url}{separator}launch_token={launch_token}'
        protocol.ready(pid=os.getpid(), bind_host=resolved.host, bind_port=actual_port,
                       url=url, auth_required=bool(resolved.access_token),
                       launch_token_included=launch_token is not None)
        if open_browser and launch_token:
            webbrowser.open(url)
        thread.join()
        return 0
    except ServerConfigError as error:
        first_error = error
        protocol.error(code=error.code, message=error.safe_message)
        return error.exit_code
    except BaseException as error:
        first_error = error
        try:
            protocol.error(code='SERVER_ERROR', message='服务启动或运行失败')
        except Exception:
            pass
        return 1
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
            if thread.is_alive():
                logger.error('服务线程未在收尾期限内退出，线程残留诊断已记录')
        for sig, handler in old_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        for action in (
            lambda: control.stop() if control else None,
            lambda: manager.stop() if manager else None,
            lambda: manager.shutdown() if manager else None,
            lambda: close_gui_logger(logger),
        ):
            try:
                action()
            except Exception:
                if first_error is None:
                    first_error = RuntimeError('服务清理失败')


def run_fastapi_server(options):
    """运行默认服务协议入口。"""
    return _run_fastapi_server(options)


def run_server_with_protocol(options, protocol_stream=None, open_browser=False):
    """运行可注入协议流和浏览器策略的服务。"""
    return _run_fastapi_server(options, protocol_stream, open_browser)


def finish_context(context):
    """完成上下文清理并返回结果。"""
    return ServiceResult()
