#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""内置下载管理器测试"""

import logging

import pytest


def test_download_file_falls_back_to_single_thread_after_multithread_failure(
        monkeypatch, tmp_path):
    """多线程失败后应清理 part 文件并回退单线程下载。"""
    from modules.download_manager import DownloadManager, DownloadMetadata

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_fallback'), 16)
    target = tmp_path / 'TwoPush.exe'
    (tmp_path / 'TwoPush.exe.part0').write_bytes(b'partial')
    monkeypatch.setattr(manager, '_get_download_metadata',
                        lambda session, url: DownloadMetadata(16, True))
    monkeypatch.setattr(manager, '_download_multithreaded', lambda url, path, size: False)
    calls = []

    def download_single(session, url, path, size):
        """记录单线程下载调用。"""
        calls.append((url, path, size))
        path.write_bytes(b'0123456789abcdef')
        return True

    monkeypatch.setattr(manager, '_download_single_threaded', download_single)
    assert manager.download_file('https://example.com/TwoPush.exe', str(target))
    assert calls == [('https://example.com/TwoPush.exe', target, 16)]
    assert not (tmp_path / 'TwoPush.exe.part0').exists()


def test_download_file_uses_single_thread_when_range_is_unavailable(monkeypatch, tmp_path):
    """不支持 Range 时应走单线程下载。"""
    from modules.download_manager import DownloadManager, DownloadMetadata
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_single'), 16)
    monkeypatch.setattr(manager, '_get_download_metadata', lambda session, url: DownloadMetadata(0, False))
    monkeypatch.setattr(manager, '_download_single_threaded', lambda session, url, path, size: True)
    monkeypatch.setattr(manager, '_download_multithreaded', lambda *args: pytest.fail('不应使用多线程'))
    assert manager.download_file('https://example.com/file.exe', str(tmp_path / 'file.exe'))


def test_download_file_returns_false_when_both_paths_fail(monkeypatch, tmp_path):
    """多线程与单线程都失败时应返回 False。"""
    from modules.download_manager import DownloadManager, DownloadMetadata
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_both_fail'), 16)
    monkeypatch.setattr(manager, '_get_download_metadata', lambda session, url: DownloadMetadata(10, True))
    monkeypatch.setattr(manager, '_download_multithreaded', lambda *args: False)
    monkeypatch.setattr(manager, '_download_single_threaded', lambda *args: False)
    assert not manager.download_file('https://example.com/file.exe', str(tmp_path / 'file.exe'))


def test_download_manager_never_writes_console_output(monkeypatch, tmp_path, capsys):
    """下载管理器不应向控制台写入任何输出。"""
    from modules.download_manager import DownloadManager, DownloadMetadata
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_output'), 1)
    monkeypatch.setattr(manager, '_get_download_metadata', lambda session, url: DownloadMetadata(0, False))
    monkeypatch.setattr(manager, '_download_single_threaded', lambda *args: True)
    assert manager.download_file('https://example.com/file.exe', str(tmp_path / 'file.exe'))
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ''
