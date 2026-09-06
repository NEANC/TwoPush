#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""TwoPush 主流程与通道解析测试"""

import json
import logging
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import TwoPush
import modules.json_manager as json_manager
from modules.json_manager import build_default_json_template
from modules.utils import parse_push_channels
from tests import make_mobile_number, mask_mobile_number


@pytest.fixture
def mock_cleanup_residue(monkeypatch):
    """mock _cleanup_update_residue，避免测试中触发文件系统副作用"""
    monkeypatch.setattr(
        'modules.self_updater.SelfUpdater._cleanup_update_residue',
        lambda logger, temp_folder=None, clean_cache=True: None,
    )


def test_parse_args_uses_config_ini_by_default(monkeypatch):
    """未指定配置路径时应默认使用 config.ini"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py'])

    args = TwoPush.parse_args()

    assert args.config == 'config.ini'


def test_should_start_web_only_without_arguments(monkeypatch):
    """仅无参数时进入 Web 入口。"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py'])
    assert TwoPush.should_start_web() is True

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--help'])
    assert TwoPush.should_start_web() is False


def test_should_start_web_preserves_any_cli_argument(monkeypatch):
    """任意命令行参数都保留原 CLI 行为。"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', 'push.json'])
    assert TwoPush.should_start_web() is False


def test_select_web_port_falls_back_to_dynamic_port_when_default_is_occupied(monkeypatch):
    """默认端口被占用时应回退到可用动态端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        monkeypatch.setattr(TwoPush, 'WEB_DEFAULT_PORT', occupied.getsockname()[1])
        fallback_port = TwoPush._select_web_port()

    assert fallback_port != TwoPush.WEB_DEFAULT_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', fallback_port))

def test_run_web_server_uses_config_and_opens_actual_port(monkeypatch, tmp_path):
    """Web 服务使用非交互配置、实际端口和带令牌首页 URL。"""
    calls = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            calls['config_kwargs'] = kwargs
        def load(self):
            calls['loaded'] = True
        def validate(self):
            return True
        def get_attr(self, key, default=''):
            return 'secret token/中文?&=' if key == 'access_token' else default

    (tmp_path / 'config.ini').write_text('[Web]\naccess_token = secret token/中文?&=\n', encoding='utf-8')

    class FakeServer:
        def __init__(self, config):
            calls['uvicorn_config'] = config
            self.started = True
            self.should_exit = False
        def run(self):
            calls['ran'] = True
        def shutdown(self):
            calls['shutdown'] = True

    class FakeUvicorn:
        Config = staticmethod(lambda app, **kwargs: kwargs)
        Server = FakeServer

    monkeypatch.setattr(TwoPush, 'ConfigManager', FakeConfig)
    monkeypatch.setitem(sys.modules, 'uvicorn', FakeUvicorn)
    monkeypatch.setattr(TwoPush, 'webbrowser', type('Browser', (), {'open': staticmethod(lambda url: calls.setdefault('url', url))}))
    monkeypatch.setattr(TwoPush, '_select_web_port', lambda: 52233)

    result = TwoPush.run_web_server(str(tmp_path / 'config.ini'))

    assert result == 0
    assert calls['config_kwargs']['non_interactive'] is True
    assert calls['loaded'] is True
    assert calls['ran'] is True
    assert calls['url'] == 'http://0.0.0.0:52233/?token=secret+token%2F%E4%B8%AD%E6%96%87%3F%26%3D'
    assert calls['uvicorn_config']['host'] == '0.0.0.0'
    assert calls['uvicorn_config']['access_log'] is False


def test_run_web_server_uses_actual_socket_port_and_cleans_up(monkeypatch, tmp_path):
    """Web 服务应使用实际监听 socket 端口并完成清理。"""
    calls = []
    uvicorn_config = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            pass
        def load(self):
            pass
        def validate(self):
            return True
        def get_attr(self, key, default=''):
            return default

    class FakeManager:
        def stop(self):
            calls.append('manager.stop')
        def shutdown(self):
            calls.append('manager.shutdown')

    class FakeControl:
        stop_requested = False
        def stop(self):
            calls.append('control.stop')

    class FakeSocket:
        def getsockname(self):
            return ('127.0.0.1', 54321)

    class FakeServer:
        started = True
        should_exit = False
        servers = [type('Server', (), {'sockets': [FakeSocket()]})()]
        def __init__(self, config):
            uvicorn_config.update(config)
        def run(self):
            calls.append('run')
        def close(self):
            calls.append('close')

    class FakeUvicorn:
        Config = staticmethod(lambda app, **kwargs: kwargs)
        Server = FakeServer

    monkeypatch.setattr(TwoPush, 'ConfigManager', FakeConfig)
    monkeypatch.setitem(sys.modules, 'uvicorn', FakeUvicorn)
    monkeypatch.setattr('modules.push_process.PushProcessManager', FakeManager)
    monkeypatch.setattr('modules.web_server.WebServerControl', FakeControl)
    monkeypatch.setattr(TwoPush, 'webbrowser', type('Browser', (), {'open': staticmethod(lambda url: calls.append(url))}))
    monkeypatch.setattr(TwoPush, '_select_web_port', lambda: 52233)

    assert TwoPush.run_web_server(str(tmp_path / 'config.ini')) == 0
    assert uvicorn_config['host'] == '127.0.0.1'
    assert 'http://127.0.0.1:54321/' in calls
    assert calls[-3:] == ['close', 'control.stop', 'manager.stop'] or calls[-4:] == ['close', 'control.stop', 'manager.stop', 'manager.shutdown']
    assert 'manager.shutdown' in calls


def test_run_web_server_startup_failure_does_not_wait_forever_and_cleans_up(monkeypatch, tmp_path):
    """Web 服务线程启动失败时应及时退出并清理资源。"""
    calls = []

    class FakeConfig:
        def __init__(self, **kwargs):
            pass
        def load(self):
            pass
        def validate(self):
            return True
        def get_attr(self, key, default=''):
            return default

    class FakeManager:
        def stop(self):
            calls.append('manager.stop')
        def shutdown(self):
            calls.append('manager.shutdown')

    class FakeControl:
        stop_requested = False
        def stop(self):
            calls.append('control.stop')

    class FakeServer:
        started = False
        should_exit = False
        def __init__(self, config):
            pass
        def run(self):
            calls.append('run')
        def close(self):
            calls.append('close')

    class FakeUvicorn:
        Config = staticmethod(lambda app, **kwargs: kwargs)
        Server = FakeServer

    monkeypatch.setattr(TwoPush, 'ConfigManager', FakeConfig)
    monkeypatch.setitem(sys.modules, 'uvicorn', FakeUvicorn)
    monkeypatch.setattr('modules.push_process.PushProcessManager', FakeManager)
    monkeypatch.setattr('modules.web_server.WebServerControl', FakeControl)
    monkeypatch.setattr(TwoPush, 'webbrowser', type('Browser', (), {'open': staticmethod(lambda url: calls.append(url))}))
    monkeypatch.setattr(TwoPush, '_select_web_port', lambda: 52233)

    assert TwoPush.run_web_server(str(tmp_path / 'config.ini')) == 1
    assert calls == ['run', 'close', 'control.stop', 'manager.stop', 'manager.shutdown']


def test_run_web_server_browser_failure_cleans_up(monkeypatch, tmp_path):
    """浏览器打开失败时应关闭服务和进程管理器。"""
    calls = []

    class FakeConfig:
        def __init__(self, **kwargs):
            pass
        def load(self):
            pass
        def validate(self):
            return True
        def get_attr(self, key, default=''):
            return default

    class FakeManager:
        def stop(self):
            calls.append('manager.stop')
        def shutdown(self):
            calls.append('manager.shutdown')

    class FakeControl:
        stop_requested = False
        def stop(self):
            calls.append('control.stop')

    class FakeServer:
        started = True
        should_exit = False
        def __init__(self, config):
            pass
        def run(self):
            calls.append('run')
        def close(self):
            calls.append('close')

    class FakeUvicorn:
        Config = staticmethod(lambda app, **kwargs: kwargs)
        Server = FakeServer

    def fail_open(url):
        raise RuntimeError('browser unavailable')

    monkeypatch.setattr(TwoPush, 'ConfigManager', FakeConfig)
    monkeypatch.setitem(sys.modules, 'uvicorn', FakeUvicorn)
    monkeypatch.setattr('modules.push_process.PushProcessManager', FakeManager)
    monkeypatch.setattr('modules.web_server.WebServerControl', FakeControl)
    monkeypatch.setattr(TwoPush, 'webbrowser', type('Browser', (), {'open': staticmethod(fail_open)}))
    monkeypatch.setattr(TwoPush, '_select_web_port', lambda: 52233)

    with pytest.raises(RuntimeError, match='browser unavailable'):
        TwoPush.run_web_server(str(tmp_path / 'config.ini'))
    assert calls == ['run', 'close', 'control.stop', 'manager.stop', 'manager.shutdown']

def test_run_web_server_constructor_failure_cleans_up(monkeypatch, tmp_path):
    """Web 服务构造失败时也应清理已创建的资源。"""
    calls = []

    class FakeConfig:
        def __init__(self, **kwargs):
            pass
        def load(self):
            pass
        def validate(self):
            return True
        def get_attr(self, key, default=''):
            return default

    class FakeManager:
        def stop(self):
            calls.append('manager.stop')
        def shutdown(self):
            calls.append('manager.shutdown')

    class FakeControl:
        stop_requested = False
        def stop(self):
            calls.append('control.stop')

    class FakeUvicorn:
        Config = staticmethod(lambda app, **kwargs: kwargs)
        class Server:
            def __init__(self, config):
                raise RuntimeError('server unavailable')

    monkeypatch.setattr(TwoPush, 'ConfigManager', FakeConfig)
    monkeypatch.setitem(sys.modules, 'uvicorn', FakeUvicorn)
    monkeypatch.setattr('modules.push_process.PushProcessManager', FakeManager)
    monkeypatch.setattr('modules.web_server.WebServerControl', FakeControl)
    monkeypatch.setattr(TwoPush, '_select_web_port', lambda: 52233)

    with pytest.raises(RuntimeError, match='server unavailable'):
        TwoPush.run_web_server(str(tmp_path / 'config.ini'))
    assert calls == ['control.stop', 'manager.stop', 'manager.shutdown']


def test_parse_args_single_dash_long_option_abbreviated_by_argparse(monkeypatch):
    """argparse 短选项缩写与位置参数共存时，裸文本会被位置参数截获"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-config', 'value'])

    args = TwoPush.parse_args()

    assert args.config == 'onfig'
    assert args.jsonfile == 'value'


def test_parse_args_accepts_readme_config_options(monkeypatch):
    """README 中列出的配置参数形式应可用"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--config', 'custom.ini'])

    args = TwoPush.parse_args()

    assert args.config == 'custom.ini'


def test_parse_args_accepts_readme_push_options(monkeypatch):
    """README 中列出的推送参数形式应可用"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--push', 'push.json'])

    args = TwoPush.parse_args()

    assert args.push == 'push.json'


def test_parse_args_accepts_readme_version_short_option(monkeypatch):
    """README 中列出的版本短参数形式应可用"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-v'])

    args = TwoPush.parse_args()

    assert args.version is True


def test_parse_args_accepts_readme_update_pascal_options(monkeypatch):
    """README 中列出的更新参数大小写形式应可用"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--UpdateForce'])

    args = TwoPush.parse_args()

    assert args.update_force is True


def test_main_exits_when_explicit_config_file_missing(monkeypatch, tmp_path, caplog):
    """显式指定的配置文件不存在时应报错退出且不自动生成"""
    config_file = tmp_path / 'missing.ini'
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-c', str(config_file)])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)

    with caplog.at_level(logging.CRITICAL, logger='TwoPush'):
        with pytest.raises(SystemExit) as exc_info:
            TwoPush.main()

    assert exc_info.value.code == 1
    assert not config_file.exists()
    assert f'指定的配置文件不存在: {config_file}' in caplog.text


def test_main_rejects_equals_style_explicit_config(monkeypatch, tmp_path):
    """等号形式显式配置路径不存在时应退出且不自动生成"""
    config_file = tmp_path / 'missing.ini'
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', f'--config={config_file}'])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 1
    assert not config_file.exists()


def test_main_exits_for_missing_attached_short_config(monkeypatch, tmp_path):
    """短配置参数贴合路径形式不存在时应退出且不自动生成"""
    config_file = tmp_path / 'missing.ini'
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', f'-c{config_file}'])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 1
    assert not config_file.exists()


def test_main_uses_push_argument_without_args_p(monkeypatch, tmp_path, mock_cleanup_residue):
    """主流程应使用 README 参数表对应的 push 属性"""
    config_file = tmp_path / 'config.ini'
    push_file = tmp_path / 'push.json'
    config_file.write_text('[SelfUpdate]\nenabled = false\n', encoding='utf-8')
    push_file.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(
        sys,
        'argv',
        ['TwoPush.py', '--config', str(config_file), '--push', str(push_file)],
    )
    monkeypatch.setattr(TwoPush, 'auto_update_check', lambda config, logger: None)
    monkeypatch.setattr(TwoPush, 'execute_push', lambda json_path, config, logger: 3)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 3


def test_self_update_verify_runs_before_explicit_config_check(monkeypatch, tmp_path):
    """自更新验证不依赖配置，应先于显式配置缺失检查执行"""
    config_file = tmp_path / 'missing.ini'
    called = {}
    monkeypatch.setattr(
        sys,
        'argv',
        ['TwoPush.py', '--self-update-verify', '-c', str(config_file)],
    )
    def fake_handle_self_update_verify(args):
        """模拟自更新验证命令会自行退出"""
        called['verify'] = True
        raise SystemExit(7)

    monkeypatch.setattr(TwoPush, 'handle_self_update_verify', fake_handle_self_update_verify)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 7
    assert called == {'verify': True}
    assert not config_file.exists()


@pytest.mark.parametrize(
    ('argv_extra', 'expected_console'),
    [([], True), (['-S'], False)],
    ids=['normal', 'silent'],
)
def test_main_update_failed_passes_silent_to_setup_logger(
    monkeypatch, argv_extra, expected_console
):
    """--update-failed 分支的日志器应遵循静默标志"""
    calls = []

    def fake_setup_logger(name='TwoPush', console_enabled=True):
        """记录日志器创建时的控制台开关"""
        calls.append(console_enabled)
        return logging.getLogger('test_main_update_failed_passes_silent')

    def fake_handle_update_failed(logger):
        """模拟自更新失败处理会自行退出"""
        raise SystemExit(1)

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--update-failed'] + argv_extra)
    monkeypatch.setattr(TwoPush, 'setup_logger', fake_setup_logger)
    monkeypatch.setattr(TwoPush, 'handle_update_failed', fake_handle_update_failed)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 1
    assert calls == [expected_console]


class FakeConfig:
    """用于测试代理解析的配置对象"""

    def __init__(self, enable_proxy=False, proxy=''):
        self.enable_proxy = enable_proxy
        self.proxy = proxy

    def get_attr_bool(self, key, default=False):
        """返回布尔配置值"""
        if key == 'enable_proxy_for_push':
            return self.enable_proxy
        return default

    def get_attr(self, key, default=''):
        """返回字符串配置值"""
        if key == 'proxy':
            return self.proxy
        if key == 'retry_interval':
            return '3s'
        return default

    def get_attr_int(self, key, default=0):
        """返回整数配置值"""
        if key == 'retry_max_count':
            return 3
        return default

    def load(self):
        """模拟配置加载，无操作"""
        pass

    def validate(self):
        """模拟配置校验，始终返回 True"""
        return True


def test_parse_push_channels_aliases_serverchan_key():
    """serverchan 的 key 别名应自动转换为 sckey"""
    channels = parse_push_channels([
        {'provider': 'serverchan', 'key': 'SCTxxxx'},
    ])
    assert channels == [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}]


def test_resolve_proxy_json_overrides_ini():
    """JSON proxy 应优先于 INI 代理控制"""
    config = FakeConfig(enable_proxy=False, proxy='http://ini.proxy')
    assert TwoPush.resolve_proxy({'proxy': 'http://json.proxy'}, config) == 'http://json.proxy'


def test_resolve_proxy_ini_requires_enable_flag():
    """INI 代理只有 enable_proxy_for_push=true 时才用于推送"""
    disabled = FakeConfig(enable_proxy=False, proxy='http://ini.proxy')
    enabled = FakeConfig(enable_proxy=True, proxy='http://ini.proxy')
    assert TwoPush.resolve_proxy({}, disabled) is None
    assert TwoPush.resolve_proxy({}, enabled) == 'http://ini.proxy'


def test_execute_push_restores_proxy_environment(monkeypatch):
    """执行推送后应恢复原 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 环境变量"""
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_all_proxy = os.environ.get('ALL_PROXY')
    os.environ['HTTP_PROXY'] = 'http://old-http.proxy'
    os.environ['HTTPS_PROXY'] = 'http://old-https.proxy'
    os.environ['ALL_PROXY'] = 'http://old-all.proxy'

    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'proxy': 'http://secret:token@new.proxy',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })
    monkeypatch.setattr(TwoPush, 'send_notification', lambda **kwargs: [('serverchan', True)])
    logger = logging.getLogger('test_execute_push_restores_proxy_environment')

    try:
        assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 0
        assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
        assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
        assert os.environ.get('ALL_PROXY') == 'http://old-all.proxy'
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


def test_execute_push_does_not_set_proxy_when_template_invalid(monkeypatch):
    """模板变量错误时不应设置新的推送代理环境变量"""
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_all_proxy = os.environ.get('ALL_PROXY')
    os.environ['HTTP_PROXY'] = 'http://old-http.proxy'
    os.environ['HTTPS_PROXY'] = 'http://old-https.proxy'
    os.environ['ALL_PROXY'] = 'http://old-all.proxy'

    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {missing_var}',
        'content': '内容 {current_time}',
        'proxy': 'http://secret:token@new.proxy',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })
    logger = logging.getLogger('test_execute_push_does_not_set_proxy_when_template_invalid')

    try:
        assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 2
        assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
        assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
        assert os.environ.get('ALL_PROXY') == 'http://old-all.proxy'
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


def test_execute_push_invalid_json_retry_max_count_falls_back(monkeypatch):
    """JSON retry.max_count 非整数时应回退默认重试次数"""
    captured = {}
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'retry': {'interval': '1s', 'max_count': 'abc'},
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })

    def fake_send_notification(**kwargs):
        """捕获重试设置并模拟发送成功"""
        captured.update(kwargs)
        return [('serverchan', True)]

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger('test_execute_push_invalid_json_retry_max_count_falls_back')

    assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 0
    assert captured['retry_settings']['max_count'] == 3


@pytest.mark.parametrize(
    'title',
    ['标题 {host_name', '标题 {0}', '标题 {host_name!z}', '标题 {host_name:>{width}}'],
    ids=['unclosed_brace', 'positional_field', 'unknown_conversion', 'nested_field'],
)
def test_execute_push_invalid_template_syntax_returns_error(monkeypatch, title):
    """模板占位符语法非法时应返回错误码而非抛出未捕获异常"""
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': title,
        'content': '内容 {current_time}',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })

    def fake_send_notification(**kwargs):
        """渲染失败时不应触达发送环节"""
        raise AssertionError('模板渲染失败后不应调用发送逻辑')

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger('test_execute_push_invalid_template_syntax_returns_error')

    assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 2


@pytest.mark.parametrize(
    'field,value',
    [
        ('title', 123),
        ('title', ['标题']),
        ('content', 123),
        ('content', {'text': '内容'}),
    ],
    ids=['title_int', 'title_list', 'content_int', 'content_dict'],
)
def test_execute_push_non_string_template_field_returns_error(
    monkeypatch, field, value
):
    """title/content 为非字符串时应返回错误码而非抛出 AttributeError"""
    template = {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    }
    template[field] = value
    monkeypatch.setattr(
        TwoPush, 'load_json_template', lambda path, logger: template
    )

    def fake_send_notification(**kwargs):
        """字段类型非法时不应触达发送环节"""
        raise AssertionError('模板字段类型非法后不应调用发送逻辑')

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger(
        'test_execute_push_non_string_template_field_returns_error'
    )

    assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 2


@pytest.mark.parametrize(
    'value',
    [123, True, {'http': 'http://127.0.0.1:7890'}, ['http://127.0.0.1:7890']],
    ids=['int', 'bool', 'dict', 'list'],
)
def test_execute_push_non_string_proxy_returns_error(monkeypatch, value):
    """JSON proxy 为非字符串时应返回错误码而非抛出 AttributeError/TypeError"""
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'proxy': value,
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })

    def fake_send_notification(**kwargs):
        """代理类型非法时不应触达发送环节"""
        raise AssertionError('代理类型非法后不应调用发送逻辑')

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger(
        'test_execute_push_non_string_proxy_returns_error'
    )

    assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 2


@pytest.mark.parametrize(
    'value',
    [123, True, {'http': 'http://u:pw@host'}, ['http://u:pw@host']],
    ids=['int', 'bool', 'dict', 'list'],
)
def test_mask_proxy_authentication_non_string_returns_placeholder(value):
    """脱敏函数收到非字符串时返回占位符，不得抛异常或回显原值"""
    assert TwoPush.mask_proxy_authentication(value) == '***'


@pytest.mark.parametrize(
    'value',
    ['5s', ['5s'], 5, 5.5],
    ids=['str', 'list', 'int', 'float'],
)
def test_execute_push_non_dict_retry_returns_error(monkeypatch, value):
    """JSON retry 为非对象真值时应返回错误码而非抛出 AttributeError"""
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'retry': value,
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })

    def fake_send_notification(**kwargs):
        """retry 类型非法时不应触达发送环节"""
        raise AssertionError('retry 类型非法后不应调用发送逻辑')

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger('test_execute_push_non_dict_retry_returns_error')

    assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 2


@pytest.mark.parametrize(
    'value',
    ['1e308h', '1e308m', None, ['5s'], {'v': '5s'}],
    ids=['overflow_hour', 'overflow_minute', 'none', 'list', 'dict'],
)
def test_execute_push_invalid_json_retry_interval_falls_back(
    monkeypatch, value
):
    """JSON retry.interval 溢出或类型非法时应回退默认间隔而非崩溃"""
    captured = {}
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'retry': {'interval': value, 'max_count': 2},
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })

    def fake_send_notification(**kwargs):
        """捕获重试设置并模拟发送成功"""
        captured.update(kwargs)
        return [('serverchan', True)]

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger(
        'test_execute_push_invalid_json_retry_interval_falls_back'
    )

    assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 0
    assert captured['retry_settings']['interval'] == 3


@pytest.mark.parametrize(
    'configured',
    ['1e308h', '1e308m', '5e304h'],
    ids=['overflow_hour', 'overflow_minute', 'threshold_hour'],
)
def test_execute_push_ini_retry_interval_overflow_falls_back(
    monkeypatch, configured
):
    """INI retry_interval 乘以单位系数溢出时应回退默认间隔而非崩溃"""
    captured = {}
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })

    class OverflowIntervalConfig(FakeConfig):
        """返回溢出 retry_interval 的配置对象"""

        def get_attr(self, key, default=''):
            """返回被测的溢出重试间隔"""
            if key == 'retry_interval':
                return configured
            return super().get_attr(key, default)

    def fake_send_notification(**kwargs):
        """捕获重试设置并模拟发送成功"""
        captured.update(kwargs)
        return [('serverchan', True)]

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger(
        'test_execute_push_ini_retry_interval_overflow_falls_back'
    )

    assert TwoPush.execute_push(
        'unused.json', OverflowIntervalConfig(), logger
    ) == 0
    assert captured['retry_settings']['interval'] == 3


@pytest.mark.parametrize('configured', [0, -5], ids=['zero', 'negative'])
def test_execute_push_ini_retry_max_count_has_lower_bound(monkeypatch, configured):
    """INI retry_max_count 配置为非正数时应钳制到至少一次重试"""
    captured = {}
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })

    class LowCountConfig(FakeConfig):
        """返回非正 retry_max_count 的配置对象"""

        def get_attr_int(self, key, default=0):
            """返回被测的非正重试次数"""
            if key == 'retry_max_count':
                return configured
            return default

    def fake_send_notification(**kwargs):
        """捕获重试设置并模拟发送成功"""
        captured.update(kwargs)
        return [('serverchan', True)]

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger('test_execute_push_ini_retry_max_count_has_lower_bound')

    assert TwoPush.execute_push('unused.json', LowCountConfig(), logger) == 0
    assert captured['retry_settings']['max_count'] == 1


def test_execute_push_logs_rendered_push_preview_before_send(monkeypatch, caplog):
    """execute_push 应在发送前记录渲染后的推送预览"""
    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '每日报告 - {host_name}',
        'content': '截止 {current_time}，系统运行正常',
        'proxy': 'http://127.0.0.1:7890',
        'retry': {'interval': '5s', 'max_count': 2},
        'channels': [
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'qmsg', 'key': 'secret-key'},
        ],
    })
    monkeypatch.setattr(TwoPush, 'render_template_vars', lambda: {
        'host_name': 'HOST',
        'current_time': '2026/07/12 12:00:00',
        'short_current_time': '12:00:00',
    })

    send_called = []

    def fake_send_notification(**kwargs):
        """记录发送调用并模拟成功"""
        send_called.append(kwargs)
        return [('serverchan', True), ('qmsg', True)]

    monkeypatch.setattr(TwoPush, 'send_notification', fake_send_notification)
    logger = logging.getLogger('test_execute_push_logs_rendered_push_preview_before_send')

    with caplog.at_level(logging.INFO, logger=logger.name):
        assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 0

    assert send_called
    assert '推送预览：' in caplog.text
    assert '"title": "每日报告 - HOST"' in caplog.text
    assert '"content": "截止 2026/07/12 12:00:00，系统运行正常"' in caplog.text
    assert '"proxy": "http://127.0.0.1:7890"' in caplog.text
    assert '"retry": {"interval": 5, "max_count": 2}' in caplog.text
    assert '"channels": ["serverchan", "qmsg"]' in caplog.text
    assert 'SCTxxxx' not in caplog.text
    assert 'secret-key' not in caplog.text


def test_format_push_preview_outputs_stable_shape():
    """推送预览应输出无外层大括号的稳定 5 行结构"""
    preview = TwoPush.format_push_preview(
        title='每日报告 - HOST',
        content='截止 2026/07/12 12:00:00，系统运行正常',
        proxy='http://127.0.0.1:7890',
        retry_settings={'interval': 5, 'max_count': 2},
        channels=[
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'qmsg', 'key': 'xxx'},
        ],
    )

    assert preview == (
        '"title": "每日报告 - HOST",\n'
        '"content": "截止 2026/07/12 12:00:00，系统运行正常",\n'
        '"proxy": "http://127.0.0.1:7890",\n'
        '"retry": {"interval": 5, "max_count": 2},\n'
        '"channels": ["serverchan", "qmsg"]'
    )
    assert not preview.startswith('{')
    assert not preview.endswith('}')
    assert all(not line.startswith(' ') for line in preview.splitlines())


def test_format_push_preview_masks_proxy_authentication():
    """推送预览应脱敏代理认证信息"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='socks5://user:password@127.0.0.1:7890',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert '"proxy": "socks5://***:***@127.0.0.1:7890"' in preview
    assert 'user:password' not in preview
    assert 'socks5://user:password@127.0.0.1:7890' not in preview


def test_mask_proxy_authentication_malformed_port_returns_placeholder():
    """代理端口非数字时应返回固定占位符而不抛异常"""
    assert TwoPush.mask_proxy_authentication(
        'http://alice:secret@proxy.test:notaport'
    ) == '***'


def test_mask_proxy_authentication_malformed_ipv6_returns_placeholder():
    """代理 IPv6 地址畸形时应返回固定占位符而不抛异常"""
    assert TwoPush.mask_proxy_authentication(
        'http://alice:secret@[2001:db8::1'
    ) == '***'


def test_format_push_preview_masks_proxy_malformed_port():
    """推送预览对端口非数字的代理应输出占位符且不抛异常"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://alice:secret@proxy.test:notaport',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert '"proxy": "***"' in preview
    assert 'notaport' not in preview
    assert 'alice:secret' not in preview


def test_mask_proxy_authentication_keeps_wellformed_proxy_variants():
    """合法代理（含端口、无端口、IPv6、无认证）脱敏行为应保持不变"""
    assert TwoPush.mask_proxy_authentication(
        'socks5://user:password@127.0.0.1:7890'
    ) == 'socks5://***:***@127.0.0.1:7890'
    assert TwoPush.mask_proxy_authentication(
        'http://alice:pass@proxy.test'
    ) == 'http://***:***@proxy.test'
    assert TwoPush.mask_proxy_authentication(
        'http://alice:pass@[::1]:8080'
    ) == 'http://***:***@[::1]:8080'
    assert TwoPush.mask_proxy_authentication(
        'http://proxy.test:notaport'
    ) == 'http://proxy.test:notaport'


def test_format_push_preview_masks_proxy_query_sensitive_key():
    """推送预览应脱敏代理 URL query 中的敏感键值"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://proxy.test:8080?access_token=QUERY',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert 'access_token=***' in preview
    assert 'QUERY' not in preview


def test_mask_proxy_authentication_masks_double_encoded_sensitive_query_key():
    """代理 URL query 键名双层编码时也应脱敏敏感值"""
    assert TwoPush.mask_proxy_authentication(
        'http://proxy.test:8080?access%255Ftoken=QUERY&name=value'
    ) == 'http://proxy.test:8080?access%255Ftoken=***&name=value'


def test_format_push_preview_masks_proxy_query_sensitive_key_case_insensitive():
    """代理 URL query 敏感键匹配应大小写不敏感"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://proxy.test:8080?PASSWORD=SECRETVALUE',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert 'PASSWORD=***' in preview
    assert 'SECRETVALUE' not in preview


def test_format_push_preview_masks_proxy_query_preserves_plus_for_space():
    """代理 URL query 中的空格 + 在脱敏重组后应保持 + 而非 %20"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://proxy.test/x?a=b+c&access_token=Q',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert 'a=b+c&access_token=***' in preview
    assert 'b%20c' not in preview
    assert 'Q' not in preview


def test_format_push_preview_masks_proxy_query_empty_sensitive_value():
    """代理 URL query 中敏感键为空值时应脱敏为空值标记 ***"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://proxy.test/x?password=',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert 'password=***' in preview


def test_format_push_preview_masks_proxy_query_percent_encoded_key():
    """代理 URL query 中百分号编码的敏感键名应被解码识别并脱敏"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://proxy.test/x?access%5Ftoken=Q',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert 'access_token=***' in preview
    assert 'access%5Ftoken=Q' not in preview


def test_format_push_preview_removes_proxy_fragment():
    """推送预览应移除代理 URL 的 fragment"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://proxy.test:8080#secret=FRAG',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert '"proxy": "http://proxy.test:8080"' in preview
    assert 'FRAG' not in preview


def test_format_push_preview_masks_proxy_combined_credentials_query_fragment():
    """代理 URL 同时含认证、敏感 query 与 fragment 时应全部脱敏"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy='http://alice:pass@proxy.test:8080?password=QUERY#token=F',
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'serverchan'}],
    )

    assert '"proxy": "http://***:***@proxy.test:8080?password=***"' in preview
    assert 'alice:pass' not in preview
    assert 'QUERY' not in preview
    assert 'token=F' not in preview


def test_format_push_preview_keeps_proxy_without_credentials():
    """无认证、无敏感 query 且无 fragment 的代理应原样返回"""
    for proxy in ('http://127.0.0.1:7890', 'http://proxy.test:8080?x=1'):
        preview = TwoPush.format_push_preview(
            title='标题',
            content='内容',
            proxy=proxy,
            retry_settings={'interval': 3, 'max_count': 3},
            channels=[{'provider': 'serverchan'}],
        )

        assert f'"proxy": "{proxy}"' in preview


def test_format_push_preview_keeps_retry_order_with_reversed_settings():
    """推送预览应固定 retry 字段顺序，不受传入字典顺序影响"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy=None,
        retry_settings={'max_count': 2, 'interval': 5},
        channels=[{}],
    )

    assert '"retry": {"interval": 5, "max_count": 2},' in preview
    assert '"channels": ["?"]' in preview


def test_format_push_preview_hides_channel_parameters():
    """推送预览的 channels 只应显示通道名，不泄露参数"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy=None,
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[
            {
                'provider': 'serverchan',
                'sckey': 'SCTxxxx',
                'key': 'secret-key',
                'token': 'secret-token',
                'secret': 'secret-value',
                'webhook': 'https://example.test/webhook',
                'password': 'secret-password',
            },
            {'provider': 'smtp', 'password': 'mail-password'},
        ],
    )

    assert '"channels": ["serverchan", "smtp"]' in preview
    for forbidden_field in (
            '"sckey"', '"key"', '"token"',
            '"secret"', '"webhook"', '"password"'):
        assert forbidden_field not in preview
    for forbidden_value in (
            'SCTxxxx', 'secret-key', 'secret-token', 'secret-value',
            'https://example.test/webhook', 'secret-password',
            'mail-password'):
        assert forbidden_value not in preview


def test_format_push_preview_masks_sensitive_title_and_content():
    """预览中的标题与正文含敏感信息时应脱敏"""
    mobile = make_mobile_number()
    preview = TwoPush.format_push_preview(
        title=f'通知 {mobile} access_token=abc',
        content='正文 sign=xyz secret=SECa',
        proxy=None,
        retry_settings={'interval': 3, 'max_count': 3},
        channels=[{'provider': 'dingtalk'}],
    )

    assert mobile not in preview
    assert mask_mobile_number(mobile) in preview
    assert 'access_token=abc' not in preview
    assert 'sign=xyz' not in preview
    assert 'SECa' not in preview


def test_parse_args_accepts_template_options(monkeypatch):
    """模板生成参数应支持 README 中定义的形式"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-T'])
    args = TwoPush.parse_args()
    assert args.template == 'TwoPush.templates.json'

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--template', 'custom.json'])
    args = TwoPush.parse_args()
    assert args.template == 'custom.json'

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--Template', 'X:/TEST/custom.json'])
    args = TwoPush.parse_args()
    assert args.template == 'X:/TEST/custom.json'


def test_parse_args_accepts_template_force_options(monkeypatch):
    """模板强制生成参数应支持可选路径"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--template-force'])
    args = TwoPush.parse_args()
    assert args.template_force == 'TwoPush.templates.json'

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--template-force', 'custom.json'])
    args = TwoPush.parse_args()
    assert args.template_force == 'custom.json'

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--Template-Force', 'X:/TEST/custom.json'])
    args = TwoPush.parse_args()
    assert args.template_force == 'X:/TEST/custom.json'


def test_parse_args_accepts_silent_options(monkeypatch):
    """静默模式参数应支持 README 中定义的形式"""
    for option in ("-S", "--silent", "--Silent"):
        monkeypatch.setattr(sys, 'argv', ['TwoPush.py', option])
        args = TwoPush.parse_args()
        assert args.silent is True


def test_parse_args_captures_positional_jsonfile(monkeypatch):
    """位置参数 jsonfile 应捕获拖放的文件路径"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', 'report.json'])
    args = TwoPush.parse_args()
    assert args.jsonfile == 'report.json'
    assert args.push is None


def test_parse_args_jsonfile_none_when_no_positional_arg(monkeypatch):
    """无位置参数时 jsonfile 应为 None"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py'])
    args = TwoPush.parse_args()
    assert args.jsonfile is None


def test_parse_args_push_takes_priority_over_jsonfile(monkeypatch):
    """-p 参数应正常解析，同时位置参数捕获拖放文件"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-p', 'cli.json', 'drag.json'])
    args = TwoPush.parse_args()
    assert args.push == 'cli.json'
    assert args.jsonfile == 'drag.json'


def test_build_default_json_template_matches_readme_example():
    """默认 JSON 模板内容应使用 README 示例结构"""
    template = build_default_json_template()

    assert template == {
        'title': '每日报告 - {host_name}',
        'content': '截止 {current_time}，系统运行正常',
        'proxy': 'http://127.0.0.1:7890',
        'retry': {
            'interval': '5s',
            'max_count': 2,
        },
        'channels': [
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'qmsg', 'key': 'xxx', 'qq': 'xxx'},
            {'provider': 'dingtalk', 'token': 'xxx', 'secret': 'xxx'},
            {'provider': 'lark', 'webhook': 'xxx', 'sign': 'xxx'},
            {
                'provider': 'smtp',
                'host': 'xxx',
                'user': 'xxx',
                'password': 'xxx',
                'port': 587,
                'ssl': True,
            },
        ],
    }


def test_write_json_template_file_creates_file(tmp_path, caplog):
    """模板写入函数应创建 UTF-8 JSON 文件"""
    template_file = tmp_path / 'custom.json'
    logger = logging.getLogger('TwoPush')

    with caplog.at_level(logging.INFO, logger='TwoPush'):
        result = json_manager.write_json_template_file(str(template_file), logger)

    assert result is True
    assert template_file.exists()
    loaded = json.loads(template_file.read_text(encoding='utf-8'))
    assert loaded['title'] == '每日报告 - {host_name}'
    assert loaded['channels'][0]['provider'] == 'serverchan'
    assert f'已生成 JSON 模板文件: {template_file}' in caplog.text


def test_write_json_template_file_verbose_path_false_shows_basename(tmp_path, caplog):
    """verbose_path=False 时日志应仅显示文件名"""
    template_file = tmp_path / 'custom.json'
    logger = logging.getLogger('TwoPush')

    with caplog.at_level(logging.INFO, logger='TwoPush'):
        result = json_manager.write_json_template_file(
            str(template_file), logger, verbose_path=False,
        )

    assert result is True
    assert '已生成 JSON 模板文件: custom.json' in caplog.text
    assert str(template_file) not in caplog.text


def test_template_command_creates_default_file(monkeypatch, tmp_path):
    """-T 不传路径时应在程序目录生成默认模板文件"""
    script_file = tmp_path / 'TwoPush.py'
    script_file.write_text('', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', [str(script_file), '-T'])
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    template_file = tmp_path / 'TwoPush.templates.json'
    assert exc_info.value.code == 0
    assert template_file.exists()


def test_template_command_creates_custom_file(monkeypatch, tmp_path):
    """--template path 应生成指定模板文件"""
    template_file = tmp_path / 'custom.json'
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--template', str(template_file)])
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert template_file.exists()


def test_template_command_refuses_existing_file_without_force(monkeypatch, tmp_path, caplog):
    """模板文件已存在且无 Force 时不应覆盖"""
    template_file = tmp_path / 'custom.json'
    template_file.write_text('{"keep": true}\n', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--template', str(template_file)])
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda *args, **kwargs: None)

    with caplog.at_level(logging.CRITICAL, logger='TwoPush'):
        with pytest.raises(SystemExit) as exc_info:
            TwoPush.main()

    assert exc_info.value.code == 1
    assert template_file.read_text(encoding='utf-8') == '{"keep": true}\n'
    assert 'JSON 模板文件已存在' in caplog.text


def test_template_force_command_overwrites_existing_file(monkeypatch, tmp_path):
    """--template-force path 应覆盖已有模板文件"""
    template_file = tmp_path / 'custom.json'
    template_file.write_text('{"keep": true}\n', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '--template-force', str(template_file)])
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    loaded = json.loads(template_file.read_text(encoding='utf-8'))
    assert loaded['title'] == '每日报告 - {host_name}'


def test_default_config_initialization_creates_json_template(monkeypatch, tmp_path, mock_cleanup_residue):
    """默认配置首次初始化时应同时生成 JSON 模板"""
    script_file = tmp_path / 'TwoPush.py'
    script_file.write_text('', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', [str(script_file)])
    monkeypatch.setattr(TwoPush, 'should_start_web', lambda: False)
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert (tmp_path / 'config.ini').exists()
    assert (tmp_path / 'TwoPush.templates.json').exists()


def test_default_config_initialization_does_not_overwrite_existing_template(monkeypatch, tmp_path, mock_cleanup_residue):
    """默认配置首次初始化不应覆盖已有 JSON 模板"""
    script_file = tmp_path / 'TwoPush.py'
    script_file.write_text('', encoding='utf-8')
    template_file = tmp_path / 'TwoPush.templates.json'
    template_file.write_text('{"keep": true}\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', [str(script_file)])
    monkeypatch.setattr(TwoPush, 'should_start_web', lambda: False)
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert (tmp_path / 'config.ini').exists()
    assert template_file.read_text(encoding='utf-8') == '{"keep": true}\n'


def test_main_exits_when_push_json_missing(monkeypatch, tmp_path, caplog):
    """显式指定的推送 JSON 不存在时应 CRITICAL 退出且不生成模板"""
    config_file = tmp_path / 'config.ini'
    push_file = tmp_path / 'missing.json'
    template_file = tmp_path / 'TwoPush.templates.json'
    config_file.write_text('[Update]\nauto_check = false\n', encoding='utf-8')
    monkeypatch.setattr(
        sys,
        'argv',
        ['TwoPush.py', '--config', str(config_file), '--push', str(push_file)],
    )
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda *args, **kwargs: None)
    monkeypatch.setattr(TwoPush, 'auto_update_check', lambda config, logger: None)

    with caplog.at_level(logging.CRITICAL, logger='TwoPush'):
        with pytest.raises(SystemExit) as exc_info:
            TwoPush.main()

    assert exc_info.value.code == 1
    assert not push_file.exists()
    assert not template_file.exists()
    assert f'指定的 JSON 推送文件不存在: {push_file}' in caplog.text


def test_resolve_proxy_passes_socks5_from_json():
    """JSON proxy 为 socks5:// 时应原样返回"""
    config = FakeConfig(enable_proxy=False, proxy='http://ini.proxy')
    assert TwoPush.resolve_proxy(
        {'proxy': 'socks5://127.0.0.1:1080'}, config,
    ) == 'socks5://127.0.0.1:1080'


def test_resolve_proxy_passes_socks5_from_json_with_auth():
    """JSON proxy 含认证信息的 socks5:// 应原样返回"""
    config = FakeConfig()
    assert TwoPush.resolve_proxy(
        {'proxy': 'socks5://user:pass@192.168.1.100:1080'}, config,
    ) == 'socks5://user:pass@192.168.1.100:1080'


def test_resolve_proxy_passes_socks5_from_ini():
    """INI 代理为 socks5:// 且 enable_proxy_for_push=true 时应返回"""
    config = FakeConfig(enable_proxy=True, proxy='socks5://127.0.0.1:1080')
    assert TwoPush.resolve_proxy({}, config) == 'socks5://127.0.0.1:1080'


def test_push_proxy_environment_sets_socks5():
    """push_proxy_environment 应正确设置 socks5:// 代理环境变量并恢复"""
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_all_proxy = os.environ.get('ALL_PROXY')
    os.environ['HTTP_PROXY'] = 'http://old-http.proxy'
    os.environ['HTTPS_PROXY'] = 'http://old-https.proxy'
    os.environ['ALL_PROXY'] = 'http://old-all.proxy'

    proxy = 'socks5://127.0.0.1:1080'
    logger = logging.getLogger('test_push_proxy_environment_sets_socks5')

    try:
        with TwoPush.push_proxy_environment(proxy, logger):
            assert os.environ.get('HTTP_PROXY') == proxy
            assert os.environ.get('HTTPS_PROXY') == proxy
            assert os.environ.get('ALL_PROXY') == proxy
        assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
        assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
        assert os.environ.get('ALL_PROXY') == 'http://old-all.proxy'
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


def test_execute_push_sets_socks5_proxy_environment(monkeypatch):
    """execute_push 应正确设置和恢复 socks5:// 代理环境变量"""
    proxy = 'socks5://secret:token@127.0.0.1:1080'
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_all_proxy = os.environ.get('ALL_PROXY')
    os.environ['HTTP_PROXY'] = 'http://old-http.proxy'
    os.environ['HTTPS_PROXY'] = 'http://old-https.proxy'
    os.environ['ALL_PROXY'] = 'http://old-all.proxy'

    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'proxy': proxy,
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })
    monkeypatch.setattr(TwoPush, 'send_notification', lambda **kwargs: [('serverchan', True)])
    logger = logging.getLogger('test_execute_push_sets_socks5_proxy_environment')

    try:
        assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 0
        assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
        assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
        assert os.environ.get('ALL_PROXY') == 'http://old-all.proxy'
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


def test_push_proxy_environment_noop_when_proxy_is_none():
    """proxy 为 None 时 push_proxy_environment 不修改环境变量"""
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_all_proxy = os.environ.get('ALL_PROXY')
    os.environ['HTTP_PROXY'] = 'http://old-http.proxy'
    os.environ['HTTPS_PROXY'] = 'http://old-https.proxy'
    os.environ['ALL_PROXY'] = 'http://old-all.proxy'

    logger = logging.getLogger('test_push_proxy_environment_noop_when_proxy_is_none')
    try:
        with TwoPush.push_proxy_environment(None, logger):
            assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
            assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
            assert os.environ.get('ALL_PROXY') == 'http://old-all.proxy'
        assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
        assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
        assert os.environ.get('ALL_PROXY') == 'http://old-all.proxy'
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


def test_json_manager_module_exposes_all_functions():
    """新模块应导出全部 7 个函数 + 常量"""
    import modules.json_manager as jm

    assert callable(jm.build_default_json_template)
    assert callable(jm.write_json_template_file)
    assert callable(jm.resolve_default_template_path)
    assert callable(jm.resolve_template_command)
    assert callable(jm.handle_template_command)
    assert callable(jm.load_json_template)
    assert callable(jm.ensure_default_template_on_first_run)
    assert jm.DEFAULT_TEMPLATE_FILE == "TwoPush.templates.json"


def test_main_passes_silent_to_setup_logger(monkeypatch, tmp_path, mock_cleanup_residue):
    """静默模式下 setup_logger 应收到 console_enabled=False"""
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[Network]\n'
        'proxy = \n'
        'enable_proxy_for_push = false\n'
        '\n'
        '[Push]\n'
        'retry_interval = 3s\n'
        'retry_max_count = 3\n'
        '\n'
        '[Update]\n'
        'auto_check = false\n'
        'channel = stable\n'
        '\n'
        '[Logs]\n'
        'save_enabled = false\n'
        'max_files = 15\n',
        encoding='utf-8',
    )
    calls = []

    class FakeLogger:
        def debug(self, message):
            pass

        def info(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            pass

        def critical(self, message):
            pass

    def fake_setup_logger(name='TwoPush', console_enabled=True):
        calls.append(console_enabled)
        return FakeLogger()

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-S', '-c', str(config_file)])
    monkeypatch.setattr(TwoPush, 'setup_logger', fake_setup_logger)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert calls == [False]


def test_main_silent_mode_still_respects_file_log_config(monkeypatch, tmp_path, mock_cleanup_residue):
    """静默模式下文件日志配置应不受影响"""
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[Network]\n'
        'proxy = \n'
        'enable_proxy_for_push = false\n'
        '\n'
        '[Push]\n'
        'retry_interval = 3s\n'
        'retry_max_count = 3\n'
        '\n'
        '[Update]\n'
        'auto_check = false\n'
        'channel = stable\n'
        '\n'
        '[Logs]\n'
        'save_enabled = true\n'
        'max_files = 15\n',
        encoding='utf-8',
    )
    calls = []

    class FakeLogger:
        def debug(self, message):
            pass

        def info(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            pass

        def critical(self, message):
            pass

    fake_logger = FakeLogger()

    def fake_setup_logger(name='TwoPush', console_enabled=True):
        return fake_logger

    def fake_add_file_logger(logger, version='', **kwargs):
        calls.append((logger, version))

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-S', '-c', str(config_file)])
    monkeypatch.setattr(TwoPush, 'setup_logger', fake_setup_logger)
    monkeypatch.setattr(TwoPush, 'add_file_logger', fake_add_file_logger)
    monkeypatch.setattr(TwoPush, 'cleanup_old_logs', lambda logger, max_files, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert calls == [(fake_logger, TwoPush.VERSION)]


def test_main_invalid_max_files_does_not_crash(monkeypatch, tmp_path, mock_cleanup_residue):
    """max_files 配置为非整数时应回退默认值而非抛出 ValueError"""
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[Network]\n'
        'proxy = \n'
        'enable_proxy_for_push = false\n'
        '\n'
        '[Push]\n'
        'retry_interval = 3s\n'
        'retry_max_count = 3\n'
        '\n'
        '[Update]\n'
        'auto_check = false\n'
        'channel = stable\n'
        '\n'
        '[Logs]\n'
        'save_enabled = true\n'
        'max_files = abc\n',
        encoding='utf-8',
    )
    captured = []

    class FakeLogger:
        def debug(self, message):
            pass

        def info(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            pass

        def critical(self, message):
            pass

    def fake_setup_logger(name='TwoPush', console_enabled=True):
        return FakeLogger()

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', '-S', '-c', str(config_file)])
    monkeypatch.setattr(TwoPush, 'setup_logger', fake_setup_logger)
    monkeypatch.setattr(TwoPush, 'add_file_logger', lambda logger, **kwargs: None)
    monkeypatch.setattr(
        TwoPush,
        'cleanup_old_logs',
        lambda logger, max_files, **kwargs: captured.append(max_files),
    )

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert captured == [15]


def test_main_drag_drop_executes_push_and_pauses(monkeypatch, tmp_path, mock_cleanup_residue):
    json_file = tmp_path / 'push.json'
    json_file.write_text(
        '{"title": "test", "content": "drag", "channels": []}',
        encoding='utf-8',
    )
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[Network]\n'
        'proxy = \n'
        'enable_proxy_for_push = false\n'
        '\n'
        '[Push]\n'
        'retry_interval = 3s\n'
        'retry_max_count = 3\n'
        '\n'
        '[Update]\n'
        'auto_check = false\n'
        'channel = stable\n'
        '\n'
        '[Logs]\n'
        'save_enabled = false\n'
        'max_files = 15\n',
        encoding='utf-8',
    )
    push_calls = []
    input_calls = []

    class FakeLogger:
        def debug(self, message):
            pass

        def info(self, message):
            pass

        def warning(self, message):
            pass

        def error(self, message):
            pass

        def critical(self, message):
            pass

    def fake_execute_push(push_file, config, logger):
        push_calls.append(push_file)
        return 0

    def fake_input(prompt=''):
        input_calls.append(str(prompt))

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', str(json_file), '-c', str(config_file)])
    monkeypatch.setattr(TwoPush, 'execute_push', fake_execute_push)
    monkeypatch.setattr('builtins.input', fake_input)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert push_calls == [str(json_file)]
    assert any('按任意键退出' in p for p in input_calls)


def test_main_drag_drop_missing_file_pauses_and_exits(monkeypatch, tmp_path):
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[Network]\nproxy = \n'
        'enable_proxy_for_push = false\n'
        '\n[Push]\nretry_interval = 3s\nretry_max_count = 3\n'
        '\n[Update]\nauto_check = false\nchannel = stable\n'
        '\n[Logs]\nsave_enabled = false\nmax_files = 15\n',
        encoding='utf-8',
    )
    input_calls = []

    class FakeLogger:
        def debug(self, msg): pass
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
        def critical(self, msg): pass

    def fake_input(prompt=''):
        input_calls.append(str(prompt))

    monkeypatch.setattr(sys, 'argv', ['TwoPush.py', 'nonexistent.json', '-c', str(config_file)])
    monkeypatch.setattr('builtins.input', fake_input)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 1
    assert any('按任意键退出' in p for p in input_calls)


def test_main_push_flag_overrides_drag_drop_no_pause(monkeypatch, tmp_path, mock_cleanup_residue):
    json_file = tmp_path / 'drag.json'
    json_file.write_text(
        '{"title": "test", "content": "drag", "channels": []}',
        encoding='utf-8',
    )
    cli_file = tmp_path / 'cli.json'
    cli_file.write_text(
        '{"title": "test", "content": "cli", "channels": []}',
        encoding='utf-8',
    )
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[Network]\nproxy = \n'
        'enable_proxy_for_push = false\n'
        '\n[Push]\nretry_interval = 3s\nretry_max_count = 3\n'
        '\n[Update]\nauto_check = false\nchannel = stable\n'
        '\n[Logs]\nsave_enabled = false\nmax_files = 15\n',
        encoding='utf-8',
    )
    push_calls = []
    input_called = [False]

    class FakeLogger:
        def debug(self, msg): pass
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
        def critical(self, msg): pass

    def fake_execute_push(push_file, config, logger):
        push_calls.append(push_file)
        return 0

    def fake_input(prompt=''):
        input_called[0] = True

    monkeypatch.setattr(
        sys, 'argv',
        ['TwoPush.py', '-p', str(cli_file), str(json_file), '-c', str(config_file)]
    )
    monkeypatch.setattr(TwoPush, 'execute_push', fake_execute_push)
    monkeypatch.setattr('builtins.input', fake_input)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert push_calls == [str(cli_file)]
    assert input_called[0] is False


def test_main_push_mode_skips_auto_update_check(monkeypatch, tmp_path, mock_cleanup_residue):
    """-p 模式应跳过 auto_update_check"""
    config_file = tmp_path / 'config.ini'
    push_file = tmp_path / 'push.json'
    config_file.write_text(
        '[Network]\nproxy = \nenable_proxy_for_push = false\n'
        '[Push]\nretry_interval = 3s\nretry_max_count = 3\n'
        '[Update]\nauto_check = false\nchannel = stable\n'
        '[Logs]\nsave_enabled = false\nmax_files = 15\n',
        encoding='utf-8',
    )
    push_file.write_text('{}', encoding='utf-8')

    call_log = {}

    def fake_execute_push(push_file, config, logger):
        return 0

    def fake_auto_check(config, logger):
        call_log['auto_check'] = True

    monkeypatch.setattr(
        sys, 'argv',
        ['TwoPush.py', '-c', str(config_file), '-p', str(push_file)],
    )
    monkeypatch.setattr(TwoPush, 'execute_push', fake_execute_push)
    monkeypatch.setattr(TwoPush, 'auto_update_check', fake_auto_check)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert 'auto_check' not in call_log


def test_main_drag_drop_push_skips_auto_update_check(monkeypatch, tmp_path, mock_cleanup_residue):
    """拖放 JSON 推送模式应跳过 auto_update_check"""
    config_file = tmp_path / 'config.ini'
    push_file = tmp_path / 'push.json'
    config_file.write_text(
        '[Network]\nproxy = \nenable_proxy_for_push = false\n'
        '[Push]\nretry_interval = 3s\nretry_max_count = 3\n'
        '[Update]\nauto_check = true\nchannel = stable\n'
        '[Logs]\nsave_enabled = false\nmax_files = 15\n',
        encoding='utf-8',
    )
    push_file.write_text('{}', encoding='utf-8')

    call_log = {}

    def fake_execute_push(push_file, config, logger):
        return 0

    def fake_auto_check(config, logger):
        call_log['auto_check'] = True

    monkeypatch.setattr(
        sys, 'argv',
        ['TwoPush.py', '-c', str(config_file), str(push_file)],
    )
    monkeypatch.setattr(TwoPush, 'execute_push', fake_execute_push)
    monkeypatch.setattr(TwoPush, 'auto_update_check', fake_auto_check)
    monkeypatch.setattr('builtins.input', lambda prompt='': None)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert 'auto_check' not in call_log


def test_main_push_with_update_still_runs_update_command(monkeypatch, tmp_path, mock_cleanup_residue):
    """-p --update 组合时 handle_update_command 仍应被调用"""
    config_file = tmp_path / 'config.ini'
    push_file = tmp_path / 'push.json'
    config_file.write_text(
        '[Network]\nproxy = \nenable_proxy_for_push = false\n'
        '[Push]\nretry_interval = 3s\nretry_max_count = 3\n'
        '[Update]\nauto_check = false\nchannel = stable\n'
        '[Logs]\nsave_enabled = false\nmax_files = 15\n',
        encoding='utf-8',
    )
    push_file.write_text('{}', encoding='utf-8')

    call_log = {}

    def fake_execute_push(push_file, config, logger):
        return 0

    def fake_handle_update(config, logger, force=False):
        call_log['update'] = True
        call_log['force'] = force

    def fake_auto_check(config, logger):
        call_log['auto_check'] = True

    monkeypatch.setattr(
        sys, 'argv',
        ['TwoPush.py', '-c', str(config_file), '-p', str(push_file), '--update'],
    )
    monkeypatch.setattr(TwoPush, 'execute_push', fake_execute_push)
    monkeypatch.setattr(TwoPush, 'handle_update_command', fake_handle_update)
    monkeypatch.setattr(TwoPush, 'auto_update_check', fake_auto_check)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 0
    assert call_log.get('update') is True
    assert call_log.get('force') is False
    assert 'auto_check' not in call_log


def test_main_push_failed_exits_before_update(monkeypatch, tmp_path, mock_cleanup_residue):
    """push 失败时应提前退出，不执行更新命令，退出码为 push 退出码"""
    config_file = tmp_path / 'config.ini'
    push_file = tmp_path / 'push.json'
    config_file.write_text(
        '[Network]\nproxy = \nenable_proxy_for_push = false\n'
        '[Push]\nretry_interval = 3s\nretry_max_count = 3\n'
        '[Update]\nauto_check = false\nchannel = stable\n'
        '[Logs]\nsave_enabled = false\nmax_files = 15\n',
        encoding='utf-8',
    )
    push_file.write_text('{}', encoding='utf-8')

    call_log = {}

    def fake_execute_push(push_file, config, logger):
        return 2  # push 失败

    def fake_handle_update(config, logger, force=False):
        call_log['update'] = True

    def fake_auto_check(config, logger):
        call_log['auto_check'] = True

    monkeypatch.setattr(
        sys, 'argv',
        ['TwoPush.py', '-c', str(config_file), '-p', str(push_file), '--update'],
    )
    monkeypatch.setattr(TwoPush, 'execute_push', fake_execute_push)
    monkeypatch.setattr(TwoPush, 'handle_update_command', fake_handle_update)
    monkeypatch.setattr(TwoPush, 'auto_update_check', fake_auto_check)

    with pytest.raises(SystemExit) as exc_info:
        TwoPush.main()

    assert exc_info.value.code == 2
    assert 'update' not in call_log
    assert 'auto_check' not in call_log


def test_format_push_preview_preserves_dingtalk_route_label():
    """推送预览应保持原有结构，不新增分支字段"""
    preview = TwoPush.format_push_preview(
        title='每日报告 - HOST',
        content='截止 2026/07/12 12:00:00，系统运行正常',
        proxy='http://127.0.0.1:7890',
        retry_settings={'interval': 5, 'max_count': 2},
        channels=[{'provider': 'dingtalk'}],
    )

    assert 'branch=' not in preview
    assert 'dingtalk(onepush)' in preview
    assert 'dingtalk(builtin)' not in preview
    assert '每日报告 - HOST' in preview


def test_format_push_preview_shows_route_labels():
    """推送预览的 channels 应为钉钉通道标注 builtin/onepush 路由标识"""
    preview = TwoPush.format_push_preview(
        title='标题',
        content='内容',
        proxy=None,
        retry_settings={},
        channels=[
            {'provider': 'dingtalk'},
            {'provider': 'dingtalk', 'msgtype': 'markdown'},
        ],
    )

    assert '"channels": ["dingtalk(onepush)", "dingtalk(builtin)"]' in preview


def test_format_push_preview_keeps_non_string_values_unchanged():
    """预览对非字符串 title/content 应原样保留，不被 str() 转换"""
    preview = TwoPush.format_push_preview(
        title=123,
        content=456.5,
        proxy=None,
        retry_settings={},
        channels=[],
    )

    assert '"title": 123,' in preview
    assert '"content": 456.5,' in preview
    assert '"title": "123"' not in preview
    assert '"content": "456.5"' not in preview


def test_format_push_preview_still_masks_string_values():
    """预览对字符串 title/content 仍应执行脱敏"""
    mobile = make_mobile_number()
    preview = TwoPush.format_push_preview(
        title=f'标题 {mobile}',
        content=f'正文 {mobile}',
        proxy=None,
        retry_settings={},
        channels=[],
    )

    assert mask_mobile_number(mobile) in preview
    assert mobile not in preview


def test_execute_push_uses_original_content_after_preview_masking(
        monkeypatch, tmp_path):
    """推送预览脱敏不应影响实际发送参数，发送仍用原始 title/content"""
    original_title = f'标题 {make_mobile_number()}'
    original_content = '# 测试推送\n\n正文 access_token=raw-token'
    captured = {}

    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': original_title,
        'content': original_content,
        'channels': [{'provider': 'dingtalk'}],
    })
    monkeypatch.setattr(
        TwoPush,
        'send_notification',
        lambda **kwargs: captured.update(kwargs) or [('dingtalk', True)],
    )
    monkeypatch.setattr(TwoPush, 'resolve_proxy', lambda template, config: None)
    logger = logging.getLogger('test_execute_push_sends_original_title_and_content_after_preview_masking')

    result = TwoPush.execute_push(str(tmp_path / 'push.json'), FakeConfig(), logger)

    assert result == 0
    assert captured['title'] == original_title
    assert captured['content'] == original_content
