#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""下载管理器同步脚本测试。"""

from pathlib import Path
import os
import subprocess
import sys

import pytest

from tools import sync_download_manager


@pytest.fixture
def source_text():
    """读取仓库内受版本控制的转换来源。"""
    with open(sync_download_manager.SOURCE_PATH, 'r', encoding='utf-8', newline='') as source:
        return source.read()


def test_apply_replacements_rejects_missing_and_duplicate_anchor():
    """每个替换锚点必须在当时文本中精确出现一次。"""
    with pytest.raises(RuntimeError):
        sync_download_manager._apply_replacements('other', [('anchor', 'new')])
    with pytest.raises(RuntimeError):
        sync_download_manager._apply_replacements('anchor anchor', [('anchor', 'new')])


def test_transform_rejects_duplicate_entry_marker(source_text):
    """入口 marker 重复时必须拒绝转换。"""
    marker = sync_download_manager.ENTRY_MARKER
    with pytest.raises(RuntimeError):
        sync_download_manager.transform_manager(source_text + marker)


def test_transform_rejects_changed_entry_tail(source_text):
    """入口后的外部源码尾部摘要变化时必须拒绝转换。"""
    with pytest.raises(RuntimeError):
        sync_download_manager.transform_manager(source_text + '\nchanged tail\n')


def test_transform_normalizes_lf_crlf_and_cr_source(source_text):
    """等价 LF、CRLF 与 CR 来源应生成完全相同的目标文本。"""
    lf_source = source_text.replace('\r\n', '\n').replace('\r', '\n')
    crlf_source = lf_source.replace('\n', '\r\n')
    cr_source = lf_source.replace('\n', '\r')

    lf_result = sync_download_manager.transform_manager(lf_source)
    crlf_result = sync_download_manager.transform_manager(crlf_source)
    cr_result = sync_download_manager.transform_manager(cr_source)

    assert crlf_result == lf_result
    assert cr_result == lf_result
    assert '            total_size: 本轮下载的完整文件大小。\n' in lf_result


def test_forbidden_token_is_rejected():
    """转换结果残留禁用 token 时必须失败。"""
    with pytest.raises(RuntimeError):
        sync_download_manager._assert_no_forbidden_tokens('print(secret)')


def test_main_keeps_target_and_removes_temp_when_replace_fails(
        monkeypatch, tmp_path, source_text):
    """原子替换失败时应保留旧目标并清理临时文件。"""
    source = tmp_path / 'manager.py'
    target = tmp_path / 'download_manager.py'
    source.write_text(source_text, encoding='utf-8', newline='')
    target.write_text('original', encoding='utf-8')
    monkeypatch.setattr(
        sync_download_manager.os, 'replace',
        lambda source_path, target_path: (_ for _ in ()).throw(OSError('locked')),
    )

    with pytest.raises(OSError):
        sync_download_manager.main(source, target)
    assert target.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.glob('*.tmp')) == []


def test_main_keeps_target_when_transformed_source_has_syntax_error(
        monkeypatch, tmp_path):
    """生成结果语法无效时应保留旧目标且不创建临时残留。"""
    source = tmp_path / 'manager.py'
    target = tmp_path / 'download_manager.py'
    source.write_text('source', encoding='utf-8')
    target.write_text('original', encoding='utf-8')
    monkeypatch.setattr(sync_download_manager, 'transform_manager', lambda content: 'def broken(:\n')

    with pytest.raises(SyntaxError):
        sync_download_manager.main(source, target)
    assert target.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.glob('*.tmp')) == []


def test_main_uses_explicit_paths_and_atomically_replaces_target(tmp_path, source_text):
    """main 参数指定的来源与目标应完成可复现原子替换。"""
    source = tmp_path / 'manager.py'
    target = tmp_path / 'generated' / 'download_manager.py'
    source.write_text(source_text, encoding='utf-8', newline='')

    sync_download_manager.main(source, target)

    expected = sync_download_manager.transform_manager(source_text)
    assert target.read_text(encoding='utf-8') == expected
    assert list(target.parent.glob('*.tmp')) == []


def test_committed_download_manager_matches_controlled_source(source_text):
    """提交的下载模块必须与仓库内受控来源的转换结果一致。"""
    expected = sync_download_manager.transform_manager(source_text)
    with open(sync_download_manager.OUTPUT_PATH, 'r', encoding='utf-8', newline='') as target:
        assert target.read() == expected


def test_main_uses_source_environment_variable(monkeypatch, tmp_path, source_text):
    """未显式传来源时应采用环境变量指定的跨机器路径。"""
    source = tmp_path / 'manager.py'
    target = tmp_path / 'download_manager.py'
    source.write_text(source_text, encoding='utf-8', newline='')
    monkeypatch.setenv('TWOPUSH_DOWNLOAD_MANAGER_SOURCE', str(source))
    monkeypatch.setattr(sync_download_manager, 'OUTPUT_PATH', target)

    sync_download_manager.main()

    assert target.exists()


def test_main_uses_target_environment_variable(monkeypatch, tmp_path, source_text):
    """未显式传目标时应采用环境变量指定的目标路径。"""
    source = tmp_path / 'manager.py'
    target = tmp_path / 'generated' / 'download_manager.py'
    source.write_text(source_text, encoding='utf-8', newline='')
    monkeypatch.setenv('TWOPUSH_DOWNLOAD_MANAGER_TARGET', str(target))

    sync_download_manager.main(source)

    assert target.exists()


def test_cli_paths_override_environment_variables(monkeypatch, tmp_path, source_text):
    """CLI 来源与目标参数应覆盖环境变量路径。"""
    cli_source = tmp_path / 'cli_manager.py'
    cli_target = tmp_path / 'cli_download_manager.py'
    env_source = tmp_path / 'env_manager.py'
    env_target = tmp_path / 'env_download_manager.py'
    cli_source.write_text(source_text, encoding='utf-8', newline='')
    env_source.write_text('invalid source', encoding='utf-8')
    monkeypatch.setenv('TWOPUSH_DOWNLOAD_MANAGER_SOURCE', str(env_source))
    monkeypatch.setenv('TWOPUSH_DOWNLOAD_MANAGER_TARGET', str(env_target))
    monkeypatch.setattr(
        sys, 'argv',
        ['sync_download_manager.py', '--source', str(cli_source), '--target', str(cli_target)],
    )

    arguments = sync_download_manager._parse_args()
    sync_download_manager.main(arguments.source, arguments.target)

    assert cli_target.exists()
    assert not env_target.exists()


def test_cli_subprocess_paths_override_environment_variables(tmp_path, source_text):
    """真实 CLI 入口的来源与目标参数应覆盖错误的环境变量路径。"""
    script_path = sync_download_manager.PROJECT_ROOT / 'tools' / 'sync_download_manager.py'
    cli_source = tmp_path / 'cli_manager.py'
    cli_target = tmp_path / 'generated' / 'cli_download_manager.py'
    env = os.environ.copy()
    env['TWOPUSH_DOWNLOAD_MANAGER_SOURCE'] = str(tmp_path / 'missing_env_manager.py')
    env['TWOPUSH_DOWNLOAD_MANAGER_TARGET'] = str(tmp_path / 'wrong_env_target.py')
    cli_source.write_text(source_text, encoding='utf-8', newline='')

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            '--source',
            str(cli_source),
            '--target',
            str(cli_target),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f'子进程退出码 {result.returncode}\n'
        f'stdout: {result.stdout!r}\n'
        f'stderr: {result.stderr!r}'
    )
    assert cli_target.read_text(encoding='utf-8') == (
        sync_download_manager.transform_manager(source_text)
    )
    assert not Path(env['TWOPUSH_DOWNLOAD_MANAGER_TARGET']).exists()


def test_cli_subprocess_succeeds_with_non_ascii_target_under_ascii_stdout(
        tmp_path, source_text):
    """ASCII 标准输出下含中文目标路径的真实 CLI 同步应成功。"""
    script_path = sync_download_manager.PROJECT_ROOT / 'tools' / 'sync_download_manager.py'
    cli_source = tmp_path / '来源' / 'manager.py'
    cli_target = tmp_path / '生成目录' / '下载管理器.py'
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'ascii'
    env['TWOPUSH_DOWNLOAD_MANAGER_SOURCE'] = str(tmp_path / 'missing_env_manager.py')
    env['TWOPUSH_DOWNLOAD_MANAGER_TARGET'] = str(tmp_path / 'wrong_env_target.py')
    cli_source.parent.mkdir()
    cli_source.write_text(source_text, encoding='utf-8', newline='')

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            '--source',
            str(cli_source),
            '--target',
            str(cli_target),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f'子进程退出码 {result.returncode}\n'
        f'stdout: {result.stdout!r}\n'
        f'stderr: {result.stderr!r}'
    )
    assert cli_target.read_text(encoding='utf-8') == (
        sync_download_manager.transform_manager(source_text)
    )
    assert result.stdout == 'Generated successfully\n'
    result.stdout.encode('ascii')
    assert not Path(env['TWOPUSH_DOWNLOAD_MANAGER_TARGET']).exists()


def test_main_explicit_paths_override_environment_variables(
        monkeypatch, tmp_path, source_text):
    """main 显式来源与目标参数应覆盖环境变量路径。"""
    explicit_source = tmp_path / 'explicit_manager.py'
    explicit_target = tmp_path / 'explicit_download_manager.py'
    env_source = tmp_path / 'env_manager.py'
    env_target = tmp_path / 'env_download_manager.py'
    explicit_source.write_text(source_text, encoding='utf-8', newline='')
    env_source.write_text('invalid source', encoding='utf-8')
    monkeypatch.setenv('TWOPUSH_DOWNLOAD_MANAGER_SOURCE', str(env_source))
    monkeypatch.setenv('TWOPUSH_DOWNLOAD_MANAGER_TARGET', str(env_target))

    sync_download_manager.main(explicit_source, explicit_target)

    assert explicit_target.exists()
    assert not env_target.exists()


def test_main_reports_missing_source(tmp_path):
    """来源不存在时应抛出明确错误。"""
    with pytest.raises(FileNotFoundError, match='下载管理器来源文件不存在'):
        sync_download_manager.main(tmp_path / 'missing.py', tmp_path / 'target.py')
