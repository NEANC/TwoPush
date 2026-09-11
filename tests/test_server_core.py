"""服务核心生命周期测试。"""

import io
from pathlib import Path

import pytest

from modules.server_core import _create_config, _create_manager, run_fastapi_server
from modules.server_options import ServerOptions


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
    class Server:
        started = True
        should_exit = False
        servers = []
        def run(self):
            return None
    monkeypatch.setattr('modules.server_core._new_server', lambda *args: Server())
    output = io.StringIO()
    monkeypatch.setattr('sys.stdout', output)
    options = ServerOptions(True, Path(config_path), port='0')

    assert run_fastapi_server(options) == 0
    assert captured['config']['temp_dir'] == captured['manager']['temp_dir']
    assert '"event":"server_ready"' in output.getvalue()
