"""服务核心生命周期测试。"""

import io
from pathlib import Path

from modules.server_core import run_fastapi_server
from modules.server_options import ServerOptions


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
