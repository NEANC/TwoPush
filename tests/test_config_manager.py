#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""配置管理器首次运行行为测试"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config_manager import ConfigManager
from modules.logger_manager import raw_read_save_enabled, setup_logger


class FakeStdin:
    """用于模拟交互式标准输入"""

    def isatty(self):
        """返回当前标准输入是否为交互终端"""
        return True
def test_setup_logger_reuse_does_not_duplicate_stream_handlers():
    """重复获取同名日志记录器不应叠加控制台 handler"""
    logger_name = 'test_setup_logger_reuse_does_not_duplicate_stream_handlers'
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()

    first_logger = setup_logger(logger_name)
    second_logger = setup_logger(logger_name)

    stream_handlers = [
        handler for handler in second_logger.handlers
        if isinstance(handler, logging.StreamHandler)
    ]
    assert first_logger is second_logger
    assert len(stream_handlers) == 1


def test_generate_default_config_pauses_in_interactive_terminal(tmp_path, monkeypatch):
    """人工交互终端首次生成配置后应等待用户确认再退出"""
    config_file = tmp_path / 'TwoPush.ini'
    logger = logging.getLogger('test_generate_default_config_pauses_in_interactive_terminal')
    manager = ConfigManager(str(config_file), logger)
    prompts = []

    monkeypatch.setattr(sys, 'stdin', FakeStdin())
    monkeypatch.setattr('builtins.input', lambda prompt='': prompts.append(prompt) or '')

    try:
        manager._generate_default_config()
    except SystemExit as e:
        assert e.code == 0

    assert prompts == ['按任意键退出...']


def test_default_config_excludes_github_repo(tmp_path):
    """github_repo 应硬编码在程序中，不出现在默认配置文件的 Update 节"""
    config_file = tmp_path / 'TwoPush.ini'
    logger = logging.getLogger('test_default_config_excludes_github_repo')
    manager = ConfigManager(str(config_file), logger)

    try:
        manager._generate_default_config()
    except SystemExit:
        pass

    with open(config_file, 'r', encoding='utf-8') as f:
        content = f.read()

    assert 'github_repo' not in content


def test_default_config_enables_file_logs_by_default(tmp_path):
    """默认配置应启用文件日志保存"""
    config_file = tmp_path / 'TwoPush.ini'
    logger = logging.getLogger('test_default_config_enables_file_logs_by_default')
    manager = ConfigManager(str(config_file), logger)

    try:
        manager._generate_default_config()
    except SystemExit:
        pass

    with open(config_file, 'r', encoding='utf-8') as f:
        content = f.read()

    assert 'save_enabled = true' in content


def test_raw_read_save_enabled_defaults_to_true_when_config_missing(tmp_path):
    """配置文件不存在时应默认保存日志"""
    assert raw_read_save_enabled(str(tmp_path / 'missing.ini')) is True


def test_init_self_updater_uses_hardcoded_repo(monkeypatch):
    """init_self_updater 应使用硬编码的 NEANC/TwoPush 而非配置"""
    import TwoPush as twopush
    from modules.self_updater import SelfUpdater as _RealUpdater

    class FakeConfig:
        """用于捕获自更新初始化参数的配置替身"""

        def get_attr(self, key, default=''):
            """返回指定配置键对应的测试值"""
            if key == 'channel':
                return 'stable'
            if key == 'proxy':
                return ''
            raise AssertionError(f'意外读取配置键: {key}')

        def get_attr_bool(self, key, default=False):
            """禁止读取未预期的布尔配置键"""
            raise AssertionError(f'意外读取配置键: {key}')

    captured_kwargs = {}

    def _fake_updater(**kwargs):
        captured_kwargs.update(kwargs)
        return _RealUpdater.__new__(_RealUpdater)

    monkeypatch.setattr(
        'modules.self_updater.SelfUpdater',
        _fake_updater,
    )
    monkeypatch.setattr(
        'modules.self_utils.detect_package_type',
        lambda: (True, 'Nuitka'),
    )

    twopush.init_self_updater(FakeConfig(), logging.getLogger('test'))
    assert captured_kwargs['github_repo'] == 'NEANC/TwoPush'
