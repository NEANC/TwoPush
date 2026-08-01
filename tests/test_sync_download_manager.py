#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""下载管理器同步脚本测试。"""

from pathlib import Path

import pytest

from tools import sync_download_manager


@pytest.fixture
def source_text():
    """读取真实外部来源并保留原始换行。"""
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


def test_main_uses_explicit_paths_and_atomically_replaces_target(tmp_path, source_text):
    """main 参数指定的来源与目标应完成可复现原子替换。"""
    source = tmp_path / 'manager.py'
    target = tmp_path / 'generated' / 'download_manager.py'
    source.write_text(source_text, encoding='utf-8', newline='')

    sync_download_manager.main(source, target)

    expected = sync_download_manager.transform_manager(source_text)
    assert target.read_text(encoding='utf-8') == expected
    assert list(target.parent.glob('*.tmp')) == []


def test_main_uses_source_environment_variable(monkeypatch, tmp_path, source_text):
    """未显式传来源时应采用环境变量指定的跨机器路径。"""
    source = tmp_path / 'manager.py'
    target = tmp_path / 'download_manager.py'
    source.write_text(source_text, encoding='utf-8', newline='')
    monkeypatch.setenv('TWOPUSH_DOWNLOAD_MANAGER_SOURCE', str(source))
    monkeypatch.setattr(sync_download_manager, 'OUTPUT_PATH', target)

    sync_download_manager.main()

    assert target.exists()


def test_main_reports_missing_source(tmp_path):
    """来源不存在时应抛出明确错误。"""
    with pytest.raises(FileNotFoundError, match='下载管理器来源文件不存在'):
        sync_download_manager.main(tmp_path / 'missing.py', tmp_path / 'target.py')
