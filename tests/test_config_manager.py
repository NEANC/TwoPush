#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""配置管理器首次运行行为测试"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.config_manager import ConfigManager


class FakeStdin:
    """用于模拟交互式标准输入"""

    def isatty(self):
        """返回当前标准输入是否为交互终端"""
        return True


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
