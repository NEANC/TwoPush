#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""TwoPush 主流程与通道解析测试"""

import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import TwoPush
import modules.json_manager as json_manager
from modules.json_manager import build_default_json_template
from modules.utils import parse_push_channels


@pytest.fixture
def mock_cleanup_residue(monkeypatch):
    """mock _cleanup_update_residue，避免测试中触发文件系统副作用"""
    monkeypatch.setattr(
        'modules.self_updater.SelfUpdater._cleanup_update_residue',
        lambda logger: None,
    )


def test_parse_args_uses_config_ini_by_default(monkeypatch):
    """未指定配置路径时应默认使用 config.ini"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py'])

    args = TwoPush.parse_args()

    assert args.config == 'config.ini'


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
