#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""内置下载管理器测试"""

import logging
from pathlib import Path

import pytest


class _FakeResponse:
    """模拟 requests 响应对象。"""

    def __init__(self, status_code, headers, chunks=()):
        self.status_code = status_code
        self.headers = headers
        self._chunks = list(chunks)
        self._iterated = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def iter_content(self, chunk_size=None):
        for chunk in self._chunks:
            yield chunk


class _FakeSession:
    """模拟 requests Session。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self._calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self._calls.append((url, kwargs.get('headers', {})))
        return self._responses.pop(0)


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


def test_download_file_falls_back_when_multithread_raises(monkeypatch, tmp_path):
    """多线程抛异常时应清理 part 文件并回退单线程下载。"""
    from modules.download_manager import DownloadManager, DownloadMetadata

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_raise_fallback'), 16)
    target = tmp_path / 'TwoPush.exe'
    (tmp_path / 'TwoPush.exe.part0').write_bytes(b'partial')
    monkeypatch.setattr(manager, '_get_download_metadata',
                        lambda session, url: DownloadMetadata(16, True))

    def raise_error(url, path, size):
        """模拟多线程下载抛异常。"""
        raise OSError('disk full')

    monkeypatch.setattr(manager, '_download_multithreaded', raise_error)
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


def test_download_single_threaded_206_resumes_existing_file(tmp_path):
    """单线程下载收到 206 时应从已有字节续传追加。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_206_resume'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'01234567')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-15/16'}, chunks=[b'89abcdef']),
    ])
    assert manager._download_single_threaded(
        session, 'https://example.com/TwoPush.exe', target, 16,
    )
    assert target.read_bytes() == b'0123456789abcdef'


def test_download_single_threaded_200_restarts_overwrite(tmp_path):
    """单线程下载收到 200 时应覆盖从头下载。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_200_overwrite'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'wrongcontent')
    session = _FakeSession([
        _FakeResponse(200, {'Content-Length': '4'}, chunks=[b'new!']),
    ])
    assert manager._download_single_threaded(
        session, 'https://example.com/TwoPush.exe', target, 16,
    )
    assert target.read_bytes() == b'new!'


def test_download_single_threaded_416_complete_file_returns_true(tmp_path):
    """单线程下载收到 416 且本地文件已完整时应视为成功。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_416_complete'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'0123456789abcdef')
    session = _FakeSession([_FakeResponse(416, {})])
    assert manager._download_single_threaded(
        session, 'https://example.com/TwoPush.exe', target, 16,
    )


def test_content_range_matches_validates_start_and_end():
    """Content-Range 校验应同时核对起始与结束字节。"""
    from modules.download_manager import DownloadManager, DownloadSegment

    manager = DownloadManager('', '', logging.getLogger('test_content_range'), 16)
    segment = DownloadSegment(0, 8, 15)
    assert manager._content_range_matches(
        segment, 8, {'Content-Range': 'bytes 8-15/16'},
    )
    assert not manager._content_range_matches(
        segment, 8, {'Content-Range': 'bytes 7-15/16'},
    )
    assert not manager._content_range_matches(
        segment, 8, {'Content-Range': 'bytes 8-14/16'},
    )
    assert not manager._content_range_matches(segment, 8, {})


def test_split_segments_covered_total_size():
    """分段应无缝覆盖整个文件区间且每段长度为正。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', '', logging.getLogger('test_split'), 16)
    segments = manager._split_segments(100, 16)
    assert sum(seg.length for seg in segments) == 100
    assert segments[0].start == 0
    assert segments[-1].end == 99
    assert all(seg.length > 0 for seg in segments)
