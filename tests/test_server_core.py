"""服务核心生命周期测试。"""

import inspect
import io
from pathlib import Path

import pytest

from modules.server_core import _create_config, _create_manager, _server_app, run_fastapi_server
from modules.server_options import ServerConfigError, ServerOptions


def test_server_app_uses_loaded_app_for_factory_config():
    """工厂模式应使用 Uvicorn 配置加载后的 ASGI 应用。"""
    loaded_app = object()
    config = type('Config', (), {'app': 'factory:app', 'loaded_app': loaded_app})()
    server = type('Server', (), {'config': config})()

    assert _server_app(server) is loaded_app


def test_server_app_uses_config_app_without_server_app_attribute():
    """健康检查应从配置取得应用而不读取已移除的 Server.app。"""
    app = object()
    config = type('Config', (), {'app': app})()
    server = type('Server', (), {'config': config})()

    assert _server_app(server) is app


def test_supported_kwargs_handles_inspection_errors_without_dropping_business_parameters(monkeypatch):
    """签名检查失败时应保留完整参数。"""
    kwargs = {'logger': 'logger', 'temp_dir': 'Temp'}
    monkeypatch.setattr('modules.server_core.inspect.signature',
                        lambda _: (_ for _ in ()).throw(TypeError('不可检查')))

    from modules.server_core import _supported_kwargs
    assert _supported_kwargs(object(), kwargs) == kwargs


def test_supported_kwargs_filters_positional_only_and_varargs(monkeypatch):
    """签名过滤不应把仅位置参数或可变位置参数当作关键字参数。"""
    signature = inspect.Signature([
        inspect.Parameter('positional_only', inspect.Parameter.POSITIONAL_ONLY),
        inspect.Parameter('named', inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter('args', inspect.Parameter.VAR_POSITIONAL),
        inspect.Parameter('keyword_only', inspect.Parameter.KEYWORD_ONLY),
    ])
    monkeypatch.setattr('modules.server_core.inspect.signature', lambda _: signature)

    from modules.server_core import _supported_kwargs
    assert _supported_kwargs(object(), {'positional_only': 1, 'named': 2,
                                        'keyword_only': 3, 'args': 4}) == {
        'named': 2, 'keyword_only': 3,
    }


def test_constructor_compatibility_filters_unsupported_signature_parameters(monkeypatch, tmp_path):
    """旧构造签名不支持关键参数时只应兼容重试一次。"""
    resolved = type('Resolved', (), {'config_path': tmp_path / 'config.ini', 'temp_dir': tmp_path / 'Temp'})()
    calls = []

    class Config:
        def __init__(self, config_file, logger, app_name, non_interactive):
            calls.append(('config', config_file, logger, app_name, non_interactive))

    class Manager:
        def __init__(self, gui_mode, logger, terminal_streams):
            calls.append(('manager', gui_mode, logger, terminal_streams))

    monkeypatch.setattr('modules.server_core.ConfigManager', Config)
    monkeypatch.setattr('modules.server_core.PushProcessManager', Manager)
    assert isinstance(_create_config(resolved, 'logger'), Config)
    assert isinstance(_create_manager(resolved, 'logger'), Manager)
    assert len(calls) == 2


def test_constructor_type_error_is_not_retried(monkeypatch, tmp_path):
    """构造函数内部 TypeError 即使包含参数字样也只能调用一次。"""
    resolved = type('Resolved', (), {'config_path': tmp_path / 'config.ini', 'temp_dir': tmp_path / 'Temp'})()
    calls = []

    class Config:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            raise TypeError('内部错误: unexpected keyword argument')

    monkeypatch.setattr('modules.server_core.ConfigManager', Config)
    with pytest.raises(TypeError, match='内部错误'):
        _create_config(resolved, 'logger')
    assert len(calls) == 1


def test_web_server_control_stop_requests_uvicorn_exit(monkeypatch):
    """浏览器模式停止控制应桥接到 Uvicorn 的退出标志。"""
    from modules.web_server import WebServerControl
    server = type('Server', (), {'should_exit': False})()
    control = WebServerControl()
    control.exit_callback = lambda: setattr(server, 'should_exit', True)
    control.stop()
    assert server.should_exit is True

def test_server_core_injects_canonical_temp_dir_and_emits_ready(monkeypatch, tmp_path):
    """核心应把解析后的临时目录注入配置管理器并输出就绪事件。"""
    config_path = tmp_path / 'temp.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    captured = {}

    class Config:
        def __init__(self, **kwargs):
            captured['config'] = kwargs
        def load(self):
            return None
        def validate(self):
            return True

    class Manager:
        def __init__(self, **kwargs):
            captured['manager'] = kwargs
        def stop(self):
            return None
        def shutdown(self):
            return None

    monkeypatch.setattr('modules.server_core.ConfigManager', Config)
    monkeypatch.setattr('modules.server_core.PushProcessManager', Manager)
    class Socket:
        def getsockname(self):
            return ('127.0.0.1', 4567)

    class Listener:
        sockets = [Socket()]

    class App:
        state = type('State', (), {'health_status': 'ready'})()

    class Server:
        started = True
        should_exit = False
        servers = [Listener()]
        config = type('Config', (), {'app': App()})()
        def run(self):
            return None
    monkeypatch.setattr('modules.server_core._new_server', lambda *args: Server())
    output = io.StringIO()
    monkeypatch.setattr('sys.stdout', output)
    options = ServerOptions(True, Path(config_path), port='0')

    assert run_fastapi_server(options) == 0
    assert captured['config']['temp_dir'] == captured['manager']['temp_dir']
    assert '"event":"server_ready"' in output.getvalue()
    assert '"bind_port":4567' in output.getvalue()
    assert 'http://127.0.0.1:4567/' in output.getvalue()


def test_server_core_uses_resolved_log_root(monkeypatch, tmp_path):
    """核心应将解析后的日志目录传给服务日志器。"""
    captured = {}
    resolved = type('Resolved', (), {'log_root': tmp_path / 'logs'})()

    monkeypatch.setattr('modules.server_core.setup_gui_logger',
                        lambda **kwargs: captured.update(kwargs) or 'logger')

    from modules.server_core import _create_logger
    assert _create_logger(resolved) == 'logger'
    assert captured['log_dir'] == tmp_path / 'logs'


def test_signal_exit_code_maps_sigint_and_sigterm():
    """服务信号应映射为标准命令行退出码。"""
    from modules.server_core import _signal_exit_code

    assert _signal_exit_code(2) == 130
    assert _signal_exit_code(15) == 0

def test_server_core_fallback_join_timeout_is_stable_failure_with_residual_diagnostic():
    """核心回退线程未退出时应返回稳定失败并记录残留诊断。"""
    from modules.server_core import _prepare_fallback_thread

    calls = []

    class Thread:
        def join(self, timeout=None):
            calls.append(('join', timeout))

        def is_alive(self):
            return True

    server = type('Server', (), {'should_exit': False})()
    logger = type('Logger', (), {'error': lambda self, *args: calls.append(('error', args))})()

    result = _prepare_fallback_thread(server, Thread(), logger)

    assert result is False
    assert server.should_exit is True
    assert calls[0][0] == 'join'
    assert calls[0][1] is not None
    assert any('残留' in str(item) for item in calls if item[0] == 'error')


@pytest.mark.parametrize(('signum', 'exit_code'), [
    (2, 130),
    (15, 0),
])
def test_startup_signal_returns_signal_code_without_ready_or_stopping(
        monkeypatch, tmp_path, signum, exit_code):
    """启动等待期间信号应返回对应退出码且不输出就绪或停止事件。"""
    config_path = tmp_path / 'temp.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    handlers = {}

    class Config:
        def __init__(self, **kwargs):
            pass
        def load(self):
            return None
        def validate(self):
            return True

    class Manager:
        def __init__(self, **kwargs):
            pass
        def stop(self):
            return None
        def shutdown(self):
            return None

    class Server:
        started = False
        should_exit = False
        app = type('App', (), {'state': type('State', (), {})()})()

        def run(self):
            handlers[signum](signum, None)

    def fake_signal(signal_number, handler):
        if callable(handler):
            handlers[signal_number] = handler
        return None

    monkeypatch.setattr('modules.server_core.ConfigManager', Config)
    monkeypatch.setattr('modules.server_core.PushProcessManager', Manager)
    monkeypatch.setattr('modules.server_core._create_logger', lambda resolved: 'logger')
    monkeypatch.setattr('modules.server_core.close_gui_logger', lambda logger: None)
    monkeypatch.setattr('modules.server_core._new_server', lambda *args: Server())
    monkeypatch.setattr('modules.server_core.signal.getsignal', lambda signal_number: None)
    monkeypatch.setattr('modules.server_core.signal.signal', fake_signal)
    output = io.StringIO()
    monkeypatch.setattr('sys.stdout', output)
    options = ServerOptions(True, Path(config_path), port='0')

    assert run_fastapi_server(options) == exit_code
    assert 'server_ready' not in output.getvalue()
    assert 'server_stopping' not in output.getvalue()


@pytest.mark.parametrize('signum', [2, 15])
def test_startup_signal_cleanup_failure_emits_cleanup_error(
        monkeypatch, tmp_path, signum):
    """启动等待期间清理失败应输出 CLEANUP_FAILED 并返回清理失败码。"""
    config_path = tmp_path / 'temp.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    handlers = {}

    class Config:
        def __init__(self, **kwargs):
            pass
        def load(self):
            return None
        def validate(self):
            return True

    class Manager:
        def __init__(self, **kwargs):
            pass
        def stop(self):
            raise RuntimeError('cleanup failed')
        def shutdown(self):
            return None

    class Server:
        started = False
        should_exit = False
        app = type('App', (), {'state': type('State', (), {})()})()

        def run(self):
            handlers[signum](signum, None)

    def fake_signal(signal_number, handler):
        if callable(handler):
            handlers[signal_number] = handler
        return None

    monkeypatch.setattr('modules.server_core.ConfigManager', Config)
    monkeypatch.setattr('modules.server_core.PushProcessManager', Manager)
    monkeypatch.setattr('modules.server_core._create_logger', lambda resolved: 'logger')
    monkeypatch.setattr('modules.server_core.close_gui_logger', lambda logger: None)
    monkeypatch.setattr('modules.server_core._new_server', lambda *args: Server())
    monkeypatch.setattr('modules.server_core.signal.getsignal', lambda signal_number: None)
    monkeypatch.setattr('modules.server_core.signal.signal', fake_signal)
    output = io.StringIO()
    monkeypatch.setattr('sys.stdout', output)
    options = ServerOptions(True, Path(config_path), port='0')

    assert run_fastapi_server(options) == 4
    assert 'server_ready' not in output.getvalue()
    assert 'server_stopping' not in output.getvalue()
    assert '"event":"server_error"' in output.getvalue()
    assert '"code":"CLEANUP_FAILED"' in output.getvalue()
def test_protocol_stream_none_uses_stdout(monkeypatch):
    """显式传入 None 时协议输出应使用标准输出。"""
    import modules.server_core as server_core

    writer = type('Writer', (), {})
    captured = {}
    class Protocol:
        def error(self, **kwargs):
            return None
    protocol = Protocol()
    monkeypatch.setattr(server_core, 'ServerProtocolWriter',
                        lambda stream: (captured.setdefault('stream', stream), protocol)[1])
    monkeypatch.setattr(server_core, 'resolve_server_options',
                        lambda *args: (_ for _ in ()).throw(ServerConfigError(
                            'CONFIG_INVALID', 2, '无效配置')))
    options = ServerOptions(True, Path('config.ini'))

    assert server_core.run_server_with_protocol(options, None) == 2
    assert captured['stream'] is __import__('sys').stdout


def test_startup_signal_handler_is_installed_before_configuration(monkeypatch, tmp_path):
    """最小信号处理器必须先于配置读取和日志创建安装。"""
    import modules.server_core as server_core
    order = []
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    monkeypatch.setattr(server_core.signal, 'signal',
                        lambda signum, handler: order.append(('signal', signum)) or None)
    monkeypatch.setattr(server_core.signal, 'getsignal', lambda signum: None)
    monkeypatch.setattr(server_core, 'resolve_server_options',
                        lambda *args: order.append(('config',)) or (_ for _ in ()).throw(
                            ServerConfigError('CONFIG_INVALID', 2, '无效配置')))
    monkeypatch.setattr(server_core, '_create_logger', lambda resolved: order.append(('logger',)) or None)

    server_core.run_server_with_protocol(ServerOptions(True, config_path), None)

    assert order[0][0] == 'signal'


def test_explicit_bind_failure_emits_bind_failed(monkeypatch, tmp_path):
    """显式端口绑定失败应返回 3 并输出稳定错误码。"""
    import modules.server_core as server_core
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    class Server:
        started = False
        should_exit = False
        def run(self):
            raise OSError(10048, 'address in use')

    monkeypatch.setattr(server_core, '_new_server', lambda *args: Server())
    monkeypatch.setattr(server_core, '_create_logger', lambda resolved: type(
        'L', (), {'error': lambda *a: None, 'info': lambda *a: None,
                  'warning': lambda *a: None, 'debug': lambda *a: None})())
    output = io.StringIO()
    monkeypatch.setattr('sys.stdout', output)

    assert server_core._is_bind_error(OSError(10048, 'address in use'))
    result = server_core.run_server_with_protocol(
        ServerOptions(True, config_path, port='52233'), output)

    assert result == 3
    assert '"code":"BIND_FAILED"' in output.getvalue()
