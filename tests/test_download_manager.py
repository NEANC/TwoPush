#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""内置下载管理器测试"""

import logging
from pathlib import Path

import pytest
import requests


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
        self._iterated = True
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
        self._calls.append((url, dict(kwargs.get('headers', {}))))
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
    target.write_bytes(b'wrongcontent12345')
    session = _FakeSession([
        _FakeResponse(200, {'Content-Length': '16'}, chunks=[b'new executable!!']),
    ])
    assert manager._download_single_threaded(
        session, 'https://example.com/TwoPush.exe', target, 16,
    )
    assert target.read_bytes() == b'new executable!!'


def test_download_single_threaded_rejects_200_length_mismatch_then_restarts(tmp_path):
    """200 声明大小与已知大小不一致时应删除残留并从头重下。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_200_length_mismatch'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abc')
    conflicting_response = _FakeResponse(
        200, {'Content-Length': '4'}, [b'0123456789abcdef'],
    )
    session = _FakeSession([
        conflicting_response,
        _FakeResponse(200, {'Content-Length': '16'}, [b'new executable!!']),
    ])

    assert manager._download_single_threaded(
        session, 'https://example.com/TwoPush.exe', target, 16,
    )
    assert target.read_bytes() == b'new executable!!'
    assert not conflicting_response._iterated
    assert not session._responses
    assert session._calls[0][1].get('Range') == 'bytes=3-'
    assert session._calls[1][1].get('Range') is None


@pytest.mark.parametrize('content_length', [None, 'invalid'])
def test_download_single_threaded_uses_known_total_without_valid_content_length(
        tmp_path, content_length):
    """200 缺失或声明非法长度时仍应按 HEAD 已知总大小校验。"""
    from modules.download_manager import DownloadManager

    headers = {}
    if content_length is not None:
        headers['Content-Length'] = content_length
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_known_total'), 16)
    target = tmp_path / 'TwoPush.exe'
    session = _FakeSession([
        _FakeResponse(200, headers, [b'bad']),
        _FakeResponse(200, {'Content-Length': '16'}, [b'0123456789abcdef']),
    ])

    assert manager._download_single_threaded(
        session, 'https://example.com/TwoPush.exe', target, 16,
    )
    assert target.read_bytes() == b'0123456789abcdef'


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
        segment, 8, 16, {'Content-Range': 'bytes 8-15/16'},
    )
    assert not manager._content_range_matches(
        segment, 8, 16, {'Content-Range': 'bytes 7-15/16'},
    )
    assert not manager._content_range_matches(
        segment, 8, 16, {'Content-Range': 'bytes 8-14/16'},
    )
    assert not manager._content_range_matches(
        segment, 8, 16, {'Content-Range': 'bytes 8-15/32'},
    )
    assert not manager._content_range_matches(
        segment, 8, 16, {'Content-Range': 'bytes 8-15/*'},
    )
    assert not manager._content_range_matches(segment, 8, 16, {})


def test_split_segments_covered_total_size():
    """分段应无缝覆盖整个文件区间且每段长度为正。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', '', logging.getLogger('test_split'), 16)
    segments = manager._split_segments(100, 16)
    assert sum(seg.length for seg in segments) == 100
    assert segments[0].start == 0
    assert segments[-1].end == 99
    assert all(seg.length > 0 for seg in segments)


def test_download_single_threaded_rejects_mismatched_206_start(tmp_path):
    """206 响应起点与本地续传偏移不一致时，应删除不可信文件并从头下载。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_mismatch_206'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')  # 本地已有 8 字节
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 0-7/16'}, [b'abcdefgh']),
        _FakeResponse(200, {'Content-Length': '16'}, [b'0123456789abcdef']),
    ])

    assert manager._download_single_threaded(session, 'https://example.com/file.exe',
                                             target, 16)
    assert target.read_bytes() == b'0123456789abcdef'
    assert session._calls[0][1].get('Range') == 'bytes=8-'
    assert 'Range' not in session._calls[1][1]


def test_download_single_threaded_accepts_full_206_with_unknown_total(tmp_path):
    """未知总长度的 206 若写满声明的区间，应判定成功。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_unknown_total_ok'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-15/*'}, [b'01234567']),
    ])

    assert manager._download_single_threaded(session, 'https://example.com/file.exe', target, 0)
    assert target.read_bytes() == b'abcdefgh01234567'
    assert session._calls[0][1].get('Range') == 'bytes=8-'


def test_download_single_threaded_rejects_short_206_with_unknown_total(tmp_path):
    """未知总长度的 206 短响应不应被判成功，应更新偏移重试直至失败。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_short_206'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-15/*'}, [b'x']),
        _FakeResponse(206, {'Content-Range': 'bytes 9-15/*'}, [b'y']),
        _FakeResponse(206, {'Content-Range': 'bytes 10-15/*'}, [b'z']),
    ])

    assert not manager._download_single_threaded(session, 'https://example.com/file.exe', target, 0)
    assert target.read_bytes() == b'abcdefghxyz'
    assert session._calls[0][1].get('Range') == 'bytes=8-'
    assert session._calls[1][1].get('Range') == 'bytes=9-'
    assert session._calls[2][1].get('Range') == 'bytes=10-'


def test_download_single_threaded_rejects_invalid_206_range(tmp_path):
    """畸形 Content-Range（start > end）应被拒绝并从头下载。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_invalid_range'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-7/*'}, [b'']),
        _FakeResponse(200, {'Content-Length': '16'}, [b'0123456789abcdef']),
    ])

    assert manager._download_single_threaded(session, 'https://example.com/file.exe',
                                             target, 16)
    assert target.read_bytes() == b'0123456789abcdef'


def test_download_single_threaded_rejects_206_total_mismatch(tmp_path):
    """206 声明总大小与已知大小不一致时应从头下载。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_206_total_mismatch'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-15/32'}, [b'01234567']),
        _FakeResponse(200, {'Content-Length': '16'}, [b'0123456789abcdef']),
    ])

    assert manager._download_single_threaded(session, 'https://example.com/file.exe', target, 16)
    assert target.read_bytes() == b'0123456789abcdef'
    assert session._calls[0][1].get('Range') == 'bytes=8-'
    assert session._calls[1][1].get('Range') is None


def test_download_single_threaded_rejects_206_end_outside_total(tmp_path):
    """206 结束位置超出声明总大小时应从头下载。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_206_end_outside_total'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-16/16'}),
        _FakeResponse(200, {'Content-Length': '16'}, [b'0123456789abcdef']),
    ])

    assert manager._download_single_threaded(session, 'https://example.com/file.exe', target, 16)
    assert target.read_bytes() == b'0123456789abcdef'
    assert session._calls[0][1].get('Range') == 'bytes=8-'
    assert session._calls[1][1].get('Range') is None


def test_download_single_threaded_rejects_oversized_206_with_unknown_total(tmp_path):
    """未知总大小的超额 206 响应应删除污染文件并从头下载。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_oversized_206'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-15/*'}, [b'012345678']),
        _FakeResponse(200, {'Content-Length': '16'}, [b'0123456789abcdef']),
    ])

    assert manager._download_single_threaded(session, 'https://example.com/file.exe', target, 0)
    assert target.read_bytes() == b'0123456789abcdef'
    assert session._calls[0][1].get('Range') == 'bytes=8-'
    assert session._calls[1][1].get('Range') is None


def test_download_single_threaded_416_incomplete_retries_then_fails(monkeypatch, tmp_path):
    """本地不完整的 416 响应应重试，耗尽后返回失败。"""
    from modules.download_manager import DownloadManager

    monkeypatch.setattr('modules.download_manager.time.sleep', lambda seconds: None)
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_416_incomplete'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')
    session = _FakeSession([
        _FakeResponse(416, {}),
        _FakeResponse(416, {}),
        _FakeResponse(416, {}),
    ])

    assert not manager._download_single_threaded(session, 'https://example.com/file.exe', target, 16)
    assert target.read_bytes() == b'abcdefgh'


def test_download_single_threaded_rejects_unclosed_206_range(tmp_path):
    """206 声明区间未闭合（end 小于 total-1）时即使大小吻合也应从头下载。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_unclosed_206'), 16)
    target = tmp_path / 'TwoPush.exe'
    target.write_bytes(b'abcdefgh')  # 本地已有 8 字节
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 8-14/16'}, [b'0123456']),
        _FakeResponse(200, {'Content-Length': '16'}, [b'0123456789abcdef']),
    ])

    assert manager._download_single_threaded(session, 'https://example.com/file.exe',
                                             target, 16)
    assert target.read_bytes() == b'0123456789abcdef'
    assert session._calls[0][1].get('Range') == 'bytes=8-'
    assert session._calls[1][1].get('Range') is None


def test_download_file_logs_only_sanitized_basename(monkeypatch, tmp_path, caplog):
    """下载入口仅可记录 URL 路径中的安全 basename。"""
    from modules.download_manager import DownloadManager, DownloadMetadata

    logger = logging.getLogger('test_url_log')
    manager = DownloadManager('', str(tmp_path), logger, 1)
    monkeypatch.setattr(manager, '_get_download_metadata',
                        lambda session, url: DownloadMetadata(0, False))
    monkeypatch.setattr(manager, '_download_single_threaded', lambda *args: True)
    url = 'https://user:secret@example.com/TwoPush.exe?token=abc#frag'
    with caplog.at_level(logging.DEBUG, logger='test_url_log'):
        assert manager.download_file(url, str(tmp_path / 'file.exe'))
    assert 'TwoPush.exe' in caplog.text
    assert 'token=abc' not in caplog.text
    assert 'user:secret' not in caplog.text
    assert url not in caplog.text


@pytest.mark.parametrize('method_name', ['head', 'get'])
def test_requests_exceptions_do_not_leak_url_or_proxy_secrets(
        monkeypatch, tmp_path, caplog, method_name):
    """HEAD 与 GET 异常日志不得包含 URL 或代理认证正文。"""
    from modules.download_manager import DownloadManager

    secret = 'https://user:password@example.com/TwoPush.exe?token=abc'
    proxy = 'http://proxy-user:proxy-password@127.0.0.1:7890'
    manager = DownloadManager(proxy, str(tmp_path), logging.getLogger('test_secret_log'), 1)

    class _ErrorSession:
        """模拟抛出带敏感正文的 requests 会话。"""

        def head(self, url, **kwargs):
            """抛出 HEAD 网络异常。"""
            raise requests.ConnectionError(f'{secret} via {proxy}')

        def get(self, url, **kwargs):
            """抛出 GET 网络异常。"""
            raise requests.ConnectionError(f'{secret} via {proxy}')

    with caplog.at_level(logging.DEBUG, logger='test_secret_log'):
        if method_name == 'head':
            manager._get_download_metadata(_ErrorSession(), secret)
        else:
            monkeypatch.setattr('modules.download_manager.time.sleep', lambda seconds: None)
            manager._download_single_threaded(
                _ErrorSession(), secret, tmp_path / 'TwoPush.exe', 16,
            )
    assert 'token=abc' not in caplog.text
    assert 'password' not in caplog.text
    assert secret not in caplog.text


@pytest.mark.parametrize('content_range', [
    'bytes 0-7/16',
    'bytes 0-7/32',
    'bytes 0-7/*',
])
def test_download_part_validates_content_range_total(
        monkeypatch, tmp_path, content_range):
    """分段下载必须校验完整 Content-Range 与本轮总大小。"""
    from modules.download_manager import DownloadManager, DownloadSegment

    monkeypatch.setattr('modules.download_manager.time.sleep', lambda seconds: None)
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_part_total'), 16)
    target = tmp_path / 'TwoPush.exe'
    segment = DownloadSegment(0, 0, 7)
    responses = [
        _FakeResponse(206, {'Content-Range': content_range}, [b'01234567'])
        for _ in range(1 if content_range.endswith('/16') else 3)
    ]
    session = _FakeSession(responses)

    result = manager._download_part(session, 'https://example.com/file.exe', target,
                                    segment, 16)
    assert result is content_range.endswith('/16')


def test_download_part_resume_validates_range_and_total(tmp_path):
    """续传 part 应发送剩余 Range 并校验响应总大小。"""
    from modules.download_manager import DownloadManager, DownloadSegment

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_part_resume'), 16)
    target = tmp_path / 'TwoPush.exe'
    (tmp_path / 'TwoPush.exe.part0').write_bytes(b'0123')
    session = _FakeSession([
        _FakeResponse(206, {'Content-Range': 'bytes 4-7/16'}, [b'4567']),
    ])

    assert manager._download_part(
        session, 'https://example.com/file.exe', target,
        DownloadSegment(0, 0, 7), 16,
    )
    assert session._calls[0][1]['Range'] == 'bytes=4-7'


def test_download_multithreaded_success_path(monkeypatch, tmp_path):
    """多线程下载成功路径：分段下载、合并、part 清理。"""
    from modules.download_manager import DownloadManager, DownloadSegment

    class _SessionStub:
        """模拟 requests.Session：无网络行为，仅支持上下文与关闭。"""

        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def close(self):
            self.closed = True

    monkeypatch.setattr('modules.download_manager.requests.Session', _SessionStub)
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_mt_success'), 16)
    target = tmp_path / 'TwoPush.exe'
    segments = [DownloadSegment(0, 0, 7), DownloadSegment(1, 8, 15)]
    monkeypatch.setattr(manager, '_split_segments', lambda size, threads: segments)
    part_calls = []

    def fake_download_part(session, url, save_path, segment, total_size):
        """模拟分段下载：写入对应 part 文件并返回成功。"""
        part_calls.append(segment.index)
        part_path = tmp_path / f'TwoPush.exe.part{segment.index}'
        part_path.write_bytes(b'01234567' if segment.index == 0 else b'89abcdef')
        return True

    monkeypatch.setattr(manager, '_download_part', fake_download_part)
    assert manager._download_multithreaded('https://example.com/TwoPush.exe', target, 16)
    assert part_calls == [0, 1]
    assert target.read_bytes() == b'0123456789abcdef'
    assert not list(tmp_path.glob('TwoPush.exe.part*'))


def test_download_multithreaded_part_failure_cleans_and_falls_back(monkeypatch, tmp_path):
    """任一分段失败时 _download_multithreaded 返回 False，download_file 清理 part 并回退单线程。"""
    from modules.download_manager import DownloadManager, DownloadMetadata, DownloadSegment

    class _SessionStub:
        """模拟 requests.Session：无网络行为，仅支持上下文与关闭。"""

        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def close(self):
            self.closed = True

    monkeypatch.setattr('modules.download_manager.requests.Session', _SessionStub)
    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_mt_part_fail'), 16)
    target = tmp_path / 'TwoPush.exe'
    segments = [DownloadSegment(0, 0, 7), DownloadSegment(1, 8, 15)]
    monkeypatch.setattr(manager, '_split_segments', lambda size, threads: segments)
    monkeypatch.setattr(manager, '_get_download_metadata',
                        lambda session, url: DownloadMetadata(16, True))

    def fake_download_part(session, url, save_path, segment, total_size):
        """模拟分段 0 成功写 part，分段 1 失败。"""
        if segment.index == 1:
            return False
        (tmp_path / 'TwoPush.exe.part0').write_bytes(b'01234567')
        return True

    monkeypatch.setattr(manager, '_download_part', fake_download_part)
    calls = []

    def download_single(session, url, path, size):
        """记录单线程回退调用。"""
        calls.append((url, path, size))
        path.write_bytes(b'0123456789abcdef')
        return True

    monkeypatch.setattr(manager, '_download_single_threaded', download_single)
    assert manager.download_file('https://example.com/TwoPush.exe', str(target))
    assert calls == [('https://example.com/TwoPush.exe', target, 16)]
    assert not list(tmp_path.glob('TwoPush.exe.part*'))


def test_cleanup_part_files_swallows_unlink_oserror(monkeypatch, tmp_path):
    """清理 part 文件时 unlink 抛 OSError 不应逃逸。"""
    from modules.download_manager import DownloadManager

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_cleanup_oserror'), 16)
    target = tmp_path / 'TwoPush.exe'
    (tmp_path / 'TwoPush.exe.part0').write_bytes(b'partial')

    def raise_oserror(path):
        """模拟 unlink 失败。"""
        raise OSError('permission denied')

    monkeypatch.setattr('modules.download_manager.Path.unlink', raise_oserror)
    assert manager._cleanup_part_files(target) is None


def test_merge_parts_returns_false_when_target_unwritable(tmp_path):
    """目标路径不可写时 _merge_parts 应返回 False 而非抛异常。"""
    from modules.download_manager import DownloadManager, DownloadSegment

    manager = DownloadManager('', str(tmp_path), logging.getLogger('test_merge_unwritable'), 16)
    target = tmp_path / 'blocked'
    target.mkdir()  # 用目录占用目标路径
    (tmp_path / 'blocked.part0').write_bytes(b'01234567')
    assert not manager._merge_parts(target, [DownloadSegment(0, 0, 7)])
