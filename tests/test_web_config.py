#!/usr/bin/env python3
# -_- coding: utf-8 -_- 

"""Web 配置首次启动行为测试"""

import configparser
import logging

from modules.config_manager import ConfigManager


def test_web_default_config_contains_access_token():
    """默认配置应包含 Web 访问令牌配置项"""
    manager = ConfigManager(
        'config.ini',
        logging.getLogger('test_web_default_config_contains_access_token'),
        default_sections={'Web': {}},
    )

    config = configparser.ConfigParser()
    config.read_string(manager._build_default_config())

    assert config.has_option('Web', 'access_token')
    assert config.get('Web', 'access_token') == ''


def test_web_missing_config_initializes_without_system_exit(tmp_path):
    """Web 非交互模式缺少配置文件时应生成配置并继续加载"""
    config_file = tmp_path / 'config.ini'
    manager = ConfigManager(
        str(config_file),
        logging.getLogger('test_web_missing_config_initializes_without_system_exit'),
        default_sections={'Web': {}},
        non_interactive=True,
    )

    manager.load()

    assert config_file.exists()
    assert manager.get_attr('access_token') == ''


def test_cli_missing_config_still_exits_after_generation(tmp_path):
    """CLI 默认模式缺少配置文件时仍应保持首次运行退出行为"""
    manager = ConfigManager(
        str(tmp_path / 'config.ini'),
        logging.getLogger('test_cli_missing_config_still_exits_after_generation'),
    )

    try:
        manager.load()
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError('CLI 首次运行应退出')
