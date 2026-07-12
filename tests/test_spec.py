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



def test_self_updater_ps_quote_escapes_powershell_interpolation_chars():
    """PowerShell 双引号路径注入应转义会触发插值的字符"""
    from pathlib import Path
    from modules.self_updater import SelfUpdater

    quoted = SelfUpdater._ps_quote(Path('C:/A$B/Name`Part/Quote"Part'))

    assert quoted == 'C:\\A`$B\\Name``Part\\Quote`"Part'



def test_self_updater_build_runtime_paths_uses_localappdata(monkeypatch, tmp_path):
    """默认应使用 LOCALAPPDATA 下的应用 SelfUpdate 目录作为 runtime 根目录"""
    from modules.self_updater import SelfUpdater

    local_appdata = tmp_path / 'LocalAppData'
    exe = tmp_path / 'program' / 'TwoPush.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'exe')
    monkeypatch.setenv('LOCALAPPDATA', str(local_appdata))

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        logger=logging.getLogger('test_runtime_paths'),
    )

    paths = updater._build_update_runtime_paths(exe, 'v2.0.0')

    assert paths['program_dir'] == exe.parent
    assert paths['state_file'] == exe.parent / 'update_state.ini'
    assert paths['log_file'] == exe.parent / 'update.log'
    assert paths['temp_folder'] == local_appdata / 'TwoPush' / 'SelfUpdate'
    assert paths['runtime_dir'] == local_appdata / 'TwoPush' / 'SelfUpdate' / 'v2.0.0'
    assert paths['helper_ps1'] == paths['runtime_dir'] / 'TwoPush_Update_Helper.ps1'
    assert paths['update_ps1'] == paths['runtime_dir'] / 'TwoPush_Update.ps1'
    assert paths['lock_file'] == paths['runtime_dir'] / 'update_started.lock'
    assert paths['new_file'] == paths['runtime_dir'] / 'TwoPush.new.exe'
    assert paths['backup_file'] == paths['runtime_dir'] / 'TwoPush.backup.exe'



def test_self_updater_build_runtime_paths_uses_custom_temp_folder(tmp_path):
    """传入 temp_folder 时 runtime_dir 应为 temp_folder / version"""
    from modules.self_updater import SelfUpdater

    exe = tmp_path / 'program' / 'TwoPush.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'exe')
    custom_temp = tmp_path / 'custom-self-update'

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        temp_folder=str(custom_temp),
        logger=logging.getLogger('test_runtime_paths_custom'),
    )

    paths = updater._build_update_runtime_paths(exe, 'v2.0.0')

    assert paths['temp_folder'] == custom_temp
    assert paths['runtime_dir'] == custom_temp / 'v2.0.0'



def test_self_updater_build_runtime_paths_falls_back_to_program_dir(monkeypatch, tmp_path):
    """LOCALAPPDATA 不可用时应 fallback 到程序目录 SelfUpdate"""
    from modules.self_updater import SelfUpdater

    exe = tmp_path / 'program' / 'TwoPush.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'exe')
    monkeypatch.delenv('LOCALAPPDATA', raising=False)

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        logger=logging.getLogger('test_runtime_paths_fallback'),
    )

    paths = updater._build_update_runtime_paths(exe, 'v2.0.0')

    assert paths['temp_folder'] == exe.parent / 'SelfUpdate'
    assert paths['runtime_dir'] == exe.parent / 'SelfUpdate' / 'v2.0.0'


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


def test_cleanup_update_residue_removes_recorded_runtime_files(monkeypatch, tmp_path):
    """清理 verified 状态残留时应仅删除状态文件记录的运行时文件"""
    from modules.self_config import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime'
    program_dir.mkdir()
    runtime_dir.mkdir()

    target = program_dir / 'TwoPush.exe'
    helper_ps1 = runtime_dir / 'TwoPush_Update_Helper.ps1'
    update_ps1 = runtime_dir / 'TwoPush_Update.ps1'
    lock_file = runtime_dir / 'update_started.lock'
    new_file = runtime_dir / 'TwoPush.new.exe'
    backup_file = runtime_dir / 'TwoPush.backup.exe'
    update_log = program_dir / 'update.log'
    foreign_helper = program_dir / 'Other_Update_Helper.ps1'

    for path in [
        target,
        helper_ps1,
        update_ps1,
        lock_file,
        new_file,
        backup_file,
        update_log,
        foreign_helper,
    ]:
        path.write_text('test', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'verified'
    state['target'] = str(target)
    state['runtime_dir'] = str(runtime_dir)
    state['helper_ps1'] = str(helper_ps1)
    state['update_ps1'] = str(update_ps1)
    state['lock_file'] = str(lock_file)
    state['new_file'] = str(new_file)
    state['backup_file'] = str(backup_file)
    state.save()

    SelfUpdater._cleanup_update_residue(logging.getLogger('test_cleanup_update_residue'))

    assert not helper_ps1.exists()
    assert not update_ps1.exists()
    assert not lock_file.exists()
    assert not new_file.exists()
    assert not backup_file.exists()
    assert not runtime_dir.exists()
    assert not (program_dir / 'update_state.ini').exists()
    assert not update_log.exists()
    assert foreign_helper.exists()


def test_cleanup_update_residue_removes_legacy_residue_when_verified(monkeypatch, tmp_path):
    """verified 状态应额外清理旧版程序目录残留和固定旧缓存目录"""
    from modules.self_config import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime'
    legacy_cache = program_dir / 'UpdateCache'
    legacy_temp = program_dir / 'TEMP'
    legacy_temp_cache = legacy_temp / 'UpdateCache'
    unknown_cache = program_dir / 'OtherUpdateCache'

    for path in [program_dir, runtime_dir, legacy_cache, legacy_temp_cache, unknown_cache]:
        path.mkdir(parents=True)

    target = program_dir / 'TwoPush.exe'
    recorded_helper = runtime_dir / 'LegacyApp_Update_Helper.ps1'
    legacy_files = [
        program_dir / 'LegacyApp_Update_Helper.ps1',
        program_dir / 'LegacyApp_Update.ps1',
        program_dir / 'update_started.lock',
        program_dir / 'TwoPush.new.exe',
        program_dir / 'TwoPush.backup.exe',
        program_dir / 'update.log',
    ]
    temp_keep = legacy_temp / 'keep.txt'
    unknown_file = unknown_cache / 'keep.bin'

    for path in [target, recorded_helper, temp_keep, unknown_file, *legacy_files]:
        path.write_text('test', encoding='utf-8')
    (legacy_cache / 'old.bin').write_text('cache', encoding='utf-8')
    (legacy_temp_cache / 'old.bin').write_text('cache', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'verified'
    state['target'] = str(target)
    state['runtime_dir'] = str(runtime_dir)
    state['helper_ps1'] = str(recorded_helper)
    state.save()

    SelfUpdater._cleanup_update_residue(logging.getLogger('test_cleanup_legacy_residue'))

    for path in legacy_files:
        assert not path.exists()
    assert not legacy_cache.exists()
    assert not legacy_temp_cache.exists()
    assert legacy_temp.exists()
    assert temp_keep.exists()
    assert unknown_file.exists()
    assert not (program_dir / 'update_state.ini').exists()


def test_cleanup_update_residue_keeps_runtime_dir_when_not_verified(monkeypatch, tmp_path):
    """非 verified 状态不应清理运行时目录和状态文件"""
    from modules.self_config import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime'
    program_dir.mkdir()
    runtime_dir.mkdir()

    target = program_dir / 'TwoPush.exe'
    backup_file = runtime_dir / 'TwoPush.backup.exe'
    target.write_text('target', encoding='utf-8')
    backup_file.write_text('backup', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'replacing'
    state['target'] = str(target)
    state['runtime_dir'] = str(runtime_dir)
    state['backup_file'] = str(backup_file)
    state.save()

    SelfUpdater._cleanup_update_residue(logging.getLogger('test_cleanup_update_residue'))

    assert runtime_dir.exists()
    assert backup_file.exists()
    assert (program_dir / 'update_state.ini').exists()



def test_rollback_uses_backup_file_in_runtime_dir(monkeypatch, tmp_path):
    """回滚应使用状态文件中 runtime_dir 内的 backup_file"""
    from modules.self_config import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    runtime_dir = tmp_path / 'runtime' / 'v2.0.0'
    program_dir.mkdir()
    runtime_dir.mkdir(parents=True)
    target = program_dir / 'TwoPush.exe'
    backup = runtime_dir / 'TwoPush.backup.exe'
    target.write_bytes(b'broken')
    backup.write_bytes(b'old')

    monkeypatch.setattr(sys, 'argv', [str(target)])
    state = UpdateState()
    state['state'] = 'failed_disabled'
    state['target'] = str(target)
    state['runtime_dir'] = str(runtime_dir)
    state['backup_file'] = str(backup)
    state.save()

    assert SelfUpdater.rollback(logging.getLogger('test_rollback_runtime')) is True
    assert target.read_bytes() == b'old'
    assert not backup.exists()



def _assert_sha256_fallbacks(content, script_name):
    """验证 Get-SHA256 函数包含多路径 fallback 结构"""
    assert 'function Get-SHA256($filePath)' in content, (
        f'{script_name}: 缺少 Get-SHA256 函数定义'
    )
    assert '[System.IO.File]::OpenRead' in content, (
        f'{script_name}: 缺少 .NET 文件流读取'
    )
    assert '[System.Security.Cryptography.SHA256]::Create()' in content, (
        f'{script_name}: 缺少 .NET SHA256 创建'
    )
    assert '$sha256.Dispose()' in content, (
        f'{script_name}: 缺少 SHA256 资源释放'
    )
    assert '$stream.Dispose()' in content, (
        f'{script_name}: 缺少文件流资源释放'
    )
    assert 'Get-Command Get-FileHash -ErrorAction SilentlyContinue' in content, (
        f'{script_name}: 缺少 Get-FileHash 可用性探测'
    )
    assert 'Get-FileHash -Algorithm SHA256 -LiteralPath $filePath' in content, (
        f'{script_name}: 缺少 Get-FileHash fallback 调用'
    )
    assert 'certutil.exe -hashfile' in content, (
        f'{script_name}: 缺少 certutil.exe fallback'
    )
    assert "'^[0-9A-Fa-f]{64}$'" in content, (
        f'{script_name}: 缺少 certutil 输出正则解析'
    )
    assert 'throw "Get-SHA256 failed:' in content, (
        f'{script_name}: 缺少最终失败明确错误'
    )


def test_ps1_fragments_generate_sha256_function():
    """PS1 片段模块应生成 SHA256 多路径 fallback 函数"""
    from modules.ps1_fragments import generate_sha256_function_ps1

    content = generate_sha256_function_ps1()

    _assert_sha256_fallbacks(content, 'ps1_fragments.generate_sha256_function_ps1')
    assert content.count('function Get-SHA256') == 1
    assert '$errors = @()' in content
    assert '$LASTEXITCODE = 0' in content


def test_ps1_fragments_generate_common_function_groups():
    """公共 PS1 片段应包含基础、状态与移动函数"""
    from modules.ps1_fragments import (
        generate_common_base_functions_ps1,
        generate_common_state_functions_ps1,
        generate_move_with_retry_ps1,
    )

    base = generate_common_base_functions_ps1()
    state = generate_common_state_functions_ps1()
    move = generate_move_with_retry_ps1()

    assert 'function Normalize-IniValue' in base
    assert 'function Assert-NotEmpty' in base
    assert 'function Write-Log' in base
    assert 'function Read-IniValue' in state
    assert 'function Write-IniValue' in state
    assert 'function Set-UpdateStatus' in state
    assert 'function Move-WithRetry' in move
    assert 'function Get-SHA256' not in base + state + move


def test_ps1_fragments_generate_helper_only_function_groups():
    """Helper 独有 PS1 片段应按职责分组且不进入 Update 公共片段"""
    from modules.ps1_fragments import (
        generate_helper_argument_functions_ps1,
        generate_helper_file_cleanup_functions_ps1,
        generate_helper_lifecycle_functions_ps1,
        generate_helper_retry_functions_ps1,
    )

    argument = generate_helper_argument_functions_ps1()
    retry = generate_helper_retry_functions_ps1()
    cleanup = generate_helper_file_cleanup_functions_ps1()
    lifecycle = generate_helper_lifecycle_functions_ps1()

    assert 'function Quote-Arg' in argument
    assert 'function Get-RetryOrDefault' in retry
    assert 'function Remove-WithRetry' in cleanup
    assert 'function Commit-Update' in lifecycle
    assert 'function Restore-Backup' in lifecycle
    assert 'function Start-ProcWait' in lifecycle
    assert 'function Start-NormalAppVisible' in lifecycle


def _make_update_runtime_paths(updater, tmp_path):
    """构造用于生成更新脚本的运行时路径。"""
    current_exe = tmp_path / 'program' / 'TwoPush.exe'
    current_exe.parent.mkdir()
    current_exe.write_bytes(b'exe')
    return updater._build_update_runtime_paths(current_exe, 'v2.0.0')



def test_generated_update_scripts_use_injected_absolute_paths(tmp_path):
    """生成的更新脚本应使用注入的绝对路径访问运行时文件与状态文件。"""
    from modules.self_updater import SelfUpdater

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        logger=logging.getLogger('test_generated_update_scripts_paths'),
    )
    paths = _make_update_runtime_paths(updater, tmp_path)
    paths['runtime_dir'].mkdir(parents=True, exist_ok=True)

    updater._generate_helper_ps1(paths)
    updater._generate_update_ps1(paths)

    helper_text = paths['helper_ps1'].read_text(encoding='utf-8-sig')
    update_text = paths['update_ps1'].read_text(encoding='utf-8-sig')

    assert f'$runtimeDir = "{SelfUpdater._ps_quote(paths["runtime_dir"])}"' in helper_text
    assert f'$lockFile   = "{SelfUpdater._ps_quote(paths["lock_file"])}"' in helper_text
    assert f'$stateFile = "{SelfUpdater._ps_quote(paths["state_file"])}"' in helper_text
    assert f'$logFile   = "{SelfUpdater._ps_quote(paths["log_file"])}"' in helper_text
    assert f'$updatePs1 = "{SelfUpdater._ps_quote(paths["update_ps1"])}"' in helper_text
    assert 'Join-Path $scriptDir "update_state.ini"' not in helper_text
    assert 'Join-Path $scriptDir "update.log"' not in helper_text

    assert f'$runtimeDir = "{SelfUpdater._ps_quote(paths["runtime_dir"])}"' in update_text
    assert f'$stateFile = "{SelfUpdater._ps_quote(paths["state_file"])}"' in update_text
    assert f'$logFile   = "{SelfUpdater._ps_quote(paths["log_file"])}"' in update_text
    assert 'Join-Path $scriptDir "update_state.ini"' not in update_text
    assert 'Join-Path $scriptDir "update.log"' not in update_text



def test_generated_update_scripts_are_bom_encoded_and_keep_key_functions(tmp_path):
    """生成的 PowerShell 更新脚本应使用 BOM 编码并包含多路径 SHA256 fallback"""
    from modules.self_updater import SelfUpdater

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        logger=logging.getLogger('test_generated_update_scripts'),
    )

    paths = _make_update_runtime_paths(updater, tmp_path)
    paths['runtime_dir'].mkdir(parents=True, exist_ok=True)

    updater._generate_helper_ps1(paths)
    updater._generate_update_ps1(paths)

    helper = paths['helper_ps1']
    update = paths['update_ps1']
    assert helper.read_bytes().startswith(b'\xef\xbb\xbf')
    assert update.read_bytes().startswith(b'\xef\xbb\xbf')

    helper_text = helper.read_text(encoding='utf-8-sig')
    update_text = update.read_text(encoding='utf-8-sig')

    _assert_sha256_fallbacks(helper_text, 'Helper.ps1')
    _assert_sha256_fallbacks(update_text, 'Update.ps1')

    assert 'function Quote-Arg' in helper_text
    assert 'function Quote-Arg' not in update_text
    assert 'function Restore-Backup' in helper_text
    assert 'function Restore-Backup' not in update_text
    assert 'function Start-ProcWait' in helper_text
    assert 'function Move-WithRetry' in helper_text
    assert 'function Move-WithRetry' in update_text
    assert 'function Start-NormalAppVisible' in helper_text
    assert 'function Start-NormalAppVisible' not in update_text
    assert helper_text.count('function Get-SHA256') == 1
    assert update_text.count('function Get-SHA256') == 1
    assert helper_text.count('function Move-WithRetry') == 1
    assert update_text.count('function Move-WithRetry') == 1
    assert helper_text.count('function Set-UpdateStatus') == 1
    assert update_text.count('function Set-UpdateStatus') == 1
    assert 'Read-IniValue "Files" "target"' in update_text
    assert 'Read-IniValue "Version" "new_sha256"' in update_text
    assert 'Get-SHA256 $target' in helper_text
    assert 'Get-SHA256 $newFile' in update_text



def test_replace_executable_writes_runtime_paths_to_state(monkeypatch, tmp_path):
    """替换准备阶段应将隔离 runtime_dir 的绝对路径写入状态文件。"""
    from modules.self_config import UpdateState
    from modules.self_updater import SelfUpdater

    program_dir = tmp_path / 'program'
    current_exe = program_dir / 'TwoPush.exe'
    source_exe = tmp_path / 'downloaded.exe'
    sha_file = tmp_path / 'downloaded.sha256'
    custom_temp = tmp_path / 'custom_temp'
    program_dir.mkdir()
    current_exe.write_bytes(b'old exe')
    source_exe.write_bytes(b'new exe')
    sha_file.write_text('sha256', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', [str(current_exe)])
    monkeypatch.setattr('modules.self_updater.get_exe_path', lambda: current_exe)

    class FakeProcess:
        """模拟 PowerShell helper 进程。"""

        returncode = None

        def __init__(self, args, **kwargs):
            lock_index = args.index('-File') + 1
            self.helper_ps1 = args[lock_index]
            (custom_temp / 'v2.0.0' / 'update_started.lock').write_text(
                'started', encoding='utf-8'
            )

        def poll(self):
            """返回进程状态。"""
            return None

        def kill(self):
            """模拟终止进程。"""
            return None

    monkeypatch.setattr('modules.self_updater.subprocess.Popen', FakeProcess)

    updater = SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-.*\.exe$',
        app_name='TwoPush',
        current_version='v1.0.0',
        proxy='',
        temp_folder=str(custom_temp),
        logger=logging.getLogger('test_replace_executable_state'),
    )

    updater._replace_executable(source_exe, sha_file, 'v2.0.0', 'oldhash', 'newhash')

    state = UpdateState.load()
    assert state is not None
    runtime_dir = custom_temp / 'v2.0.0'
    assert state.get('Files', 'runtime_dir') == str(runtime_dir)
    assert state.get('Files', 'helper_ps1') == str(runtime_dir / 'TwoPush_Update_Helper.ps1')
    assert state.get('Files', 'update_ps1') == str(runtime_dir / 'TwoPush_Update.ps1')
    assert state.get('Files', 'lock_file') == str(runtime_dir / 'update_started.lock')
    assert not state._config.has_section('Runtime')
    assert state['new_file'] == str(runtime_dir / 'TwoPush.new.exe')
    assert state['backup_file'] == str(runtime_dir / 'TwoPush.backup.exe')
    assert (runtime_dir / 'TwoPush.new.exe').read_bytes() == b'new exe'
    assert (runtime_dir / 'TwoPush_Update_Helper.ps1').exists()
    assert (runtime_dir / 'TwoPush_Update.ps1').exists()
