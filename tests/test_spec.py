#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""PyInstaller spec 文件测试"""

import os


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_FILE = os.path.join(ROOT_DIR, 'TwoPush.spec')


def test_twopush_spec_uses_project_entry_and_name():
    """TwoPush.spec 应使用项目入口和项目名称"""
    with open(SPEC_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    assert "Analysis(['TwoPush.py']" in content
    assert "name='TwoPush'" in content
    assert 'M9A_Update_Assistant' not in content


def test_twopush_spec_declares_required_hidden_imports():
    """TwoPush.spec 应声明运行所需的隐藏导入"""
    with open(SPEC_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    for module_name in [
        'onepush',
        'requests',
        'socks',
        'colorama',
        'modules.config_manager',
        'modules.config_migration',
        'modules.logger_manager',
        'modules.notification',
        'modules.self_config',
        'modules.self_updater',
        'modules.self_utils',
        'modules.utils',
        'modules.version',
    ]:
        assert repr(module_name) in content


def test_self_updater_static_methods_exist():
    """SelfUpdater 应保留 clean_update_cache 和 rollback 静态方法"""
    from modules.self_updater import SelfUpdater
    assert hasattr(SelfUpdater, 'rollback')
    assert hasattr(SelfUpdater, 'clean_update_cache')
