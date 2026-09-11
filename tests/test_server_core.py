"""服务核心生命周期测试。"""

import inspect
import io
from pathlib import Path

import pytest

from modules.server_core import _create_config, _create_manager, run_fastapi_server
from modules.server_options import ServerOptions


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

    class Server:
        started = True
        should_exit = False
        servers = [Listener()]
        app = type('App', (), {'state': type('State', (), {'health_status': 'ready'})()})()
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
