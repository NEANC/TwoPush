#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""PyInstaller spec 与自更新模块测试"""

import logging
import os
import sys


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


def test_self_update_verify_checks_expected_version_without_injected_func(monkeypatch, tmp_path):
    """自更新验证应在未注入版本函数时仍校验当前程序版本"""
    from modules.self_updater import SelfUpdater

    exe_path = tmp_path / 'TwoPush.exe'
    exe_path.write_bytes(b'fake exe')
    monkeypatch.setattr('modules.self_updater.get_exe_path', lambda: exe_path)
    monkeypatch.setattr('modules.version.VERSION', 'v1.0.0')

    result = SelfUpdater.self_update_verify(
        expected_sha256='expected-sha',
        expected_version='v9.9.9',
        sha256_calc=lambda path: 'expected-sha',
    )

    assert result == 3


def test_cleanup_update_residue_only_removes_current_app_files(monkeypatch, tmp_path):
    """清理更新残留时不应删除同目录下其他应用的更新文件"""
    from modules.self_config import UpdateState
    from modules.self_updater import SelfUpdater

    target = tmp_path / 'TwoPush.exe'
    backup = tmp_path / 'TwoPush.backup.exe'
    target.write_bytes(b'target')
    backup.write_bytes(b'backup')

    own_helper = tmp_path / 'TwoPush_Update_Helper.ps1'
    own_update = tmp_path / 'TwoPush_Update.ps1'
    own_new = tmp_path / 'TwoPush.new.exe'
    foreign_helper = tmp_path / 'Other_Update_Helper.ps1'
    foreign_update = tmp_path / 'Other_Update.ps1'
    foreign_new = tmp_path / 'Other.new.exe'
    for path in [own_helper, own_update, own_new, foreign_helper, foreign_update, foreign_new]:
        path.write_text('test', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'verified'
    state['target'] = str(target)
    state['backup_file'] = str(backup)
    state.save()

    SelfUpdater._cleanup_update_residue(logging.getLogger('test_cleanup_update_residue'))

    assert not own_helper.exists()
    assert not own_update.exists()
    assert not own_new.exists()
    assert not backup.exists()
    assert foreign_helper.exists()
    assert foreign_update.exists()
    assert foreign_new.exists()


def test_generated_update_scripts_are_bom_encoded_and_keep_key_functions(tmp_path):
    """生成的 PowerShell 更新脚本应使用 BOM 编码并保留关键函数"""
    from modules.self_updater import SelfUpdater

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        logger=logging.getLogger('test_generated_update_scripts'),
    )

    updater._generate_helper_ps1(tmp_path)
    updater._generate_update_ps1(tmp_path)

    helper = tmp_path / 'TwoPush_Update_Helper.ps1'
    update = tmp_path / 'TwoPush_Update.ps1'
    assert helper.read_bytes().startswith(b'\xef\xbb\xbf')
    assert update.read_bytes().startswith(b'\xef\xbb\xbf')

    helper_text = helper.read_text(encoding='utf-8-sig')
    update_text = update.read_text(encoding='utf-8-sig')
    assert 'function Restore-Backup' in helper_text
    assert 'function Start-ProcWait' in helper_text
    assert 'function Move-WithRetry' in helper_text
    assert 'function Move-WithRetry' in update_text
    assert 'Read-IniValue "Files" "target"' in update_text
    assert 'Read-IniValue "Version" "new_sha256"' in update_text
