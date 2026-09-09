#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 按日文件日志测试。"""

import logging
import threading
from datetime import datetime

import pytest

import modules.logger_manager as logger_manager
from modules.logger_manager import (
    cleanup_gui_logs,
    close_gui_logger,
    sanitize_log_message,
    set_max_files,
    setup_gui_logger,
)


def test_sanitize_log_message_hides_credentials_urls_and_paths():
    """GUI 日志摘要不得泄露凭据、代理认证或绝对路径。"""
    text = sanitize_log_message(
        'token=abc secret: def password=ghi access_token=jkl '
        'Authorization: Bearer mno smtp_password=pqr '
        'http://user:pass@example.test/x C:\\Users\\name\\config.ini '
        '/home/name/config.ini', root='C:\\Users\\name')
    lowered = text.lower()
    for secret in ('abc', 'def', 'ghi', 'jkl', 'mno', 'pqr', 'user', 'pass@example'):
        assert secret not in lowered
    assert 'C:\\Users\\name' not in text
    assert '/home/name' not in text


class FakeClock:
    """提供可变的测试时间。"""

    def __init__(self, value):
        self.value = value

    def now(self):
        """返回当前测试时间。"""
        return self.value


def test_setup_does_not_clean_until_explicit_cleanup(tmp_path):
    """初始化不清理，显式清理才删除超限文件。"""
    log_dir = tmp_path / 'gui'
    log_dir.mkdir()
    for day in range(1, 4):
        (log_dir / f'TwoPush-GUI_2026-09-{day:02d}.log').write_text(
            str(day), encoding='utf-8')
    logger = setup_gui_logger(log_dir=log_dir, max_files=2,
                             clock=FakeClock(datetime(2026, 9, 3, 12)))
    assert (log_dir / 'TwoPush-GUI_2026-09-01.log').exists()
    cleanup_gui_logs(logger)
    assert not (log_dir / 'TwoPush-GUI_2026-09-01.log').exists()
    close_gui_logger(logger)


def test_same_day_appends_and_uses_exact_filename(tmp_path):
    """同日初始化和写入应追加到精确日期文件。"""
    clock = FakeClock(datetime(2026, 9, 9, 12))
    logger = setup_gui_logger(log_dir=tmp_path, clock=clock)
    logger.info('第一条中文日志')
    logger.info('第二条中文日志')
    path = tmp_path / 'TwoPush-GUI_2026-09-09.log'
    close_gui_logger(logger)
    assert path.read_text(encoding='utf-8').count('中文日志') == 2
    logger = setup_gui_logger(log_dir=tmp_path, clock=clock)
    logger.info('第三条中文日志')
    close_gui_logger(logger)
    assert path.read_text(encoding='utf-8').count('中文日志') == 3


def test_date_forward_and_backward_switch_files(tmp_path):
    """日期前进和回退都应切换到对应日期文件。"""
    clock = FakeClock(datetime(2026, 9, 10, 12))
    logger = setup_gui_logger(log_dir=tmp_path, clock=clock)
    logger.info('较晚日期日志')
    clock.value = datetime(2026, 9, 9, 12)
    logger.info('回退日期日志')
    clock.value = datetime(2026, 9, 11, 12)
    logger.info('前进日期日志')
    close_gui_logger(logger)
    assert '回退日期日志' in (tmp_path / 'TwoPush-GUI_2026-09-09.log').read_text(encoding='utf-8')
    assert '回退日期日志' not in (tmp_path / 'TwoPush-GUI_2026-09-10.log').read_text(encoding='utf-8')
    assert '前进日期日志' in (tmp_path / 'TwoPush-GUI_2026-09-11.log').read_text(encoding='utf-8')


def test_failed_date_switch_resets_state_and_retries_on_next_emit(tmp_path, monkeypatch):
    """日期切换打开失败后应重置状态并支持重试及回退。"""
    clock = FakeClock(datetime(2026, 9, 9, 12))
    logger = setup_gui_logger(log_dir=tmp_path, clock=clock)
    handler = next(handler for handler in logger.handlers
                   if getattr(handler, 'is_gui_handler', False))
    logger.info('第一天日志')
    original_open = open
    failed_path = tmp_path / 'TwoPush-GUI_2026-09-10.log'

    def broken_second_day_open(path, *args, **kwargs):
        """仅模拟第二天文件首次打开失败。"""
        if path == failed_path:
            raise OSError('second day open failed')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr('builtins.open', broken_second_day_open)
    clock.value = datetime(2026, 9, 10, 12)
    logger.info('第二天失败日志')
    assert handler._stream is None
    assert handler._current_date is None

    monkeypatch.setattr('builtins.open', original_open)
    logger.info('第二天成功日志')
    clock.value = datetime(2026, 9, 9, 12)
    logger.info('回退后日志')
    close_gui_logger(logger)

    first_day = (tmp_path / 'TwoPush-GUI_2026-09-09.log').read_text(encoding='utf-8')
    second_day = failed_path.read_text(encoding='utf-8')
    assert first_day.count('第一天日志') == 1
    assert first_day.count('回退后日志') == 1
    assert '第二天成功日志' in second_day
    assert '第二天失败日志' not in second_day


def test_concurrent_writes_are_complete_lines(tmp_path):
    """并发写入应保留所有完整日志行。"""
    logger = setup_gui_logger(log_dir=tmp_path,
                             clock=FakeClock(datetime(2026, 9, 9)))
    total = 200

    def write(start):
        """写入一组唯一编号日志。"""
        for index in range(start, start + total // 4):
            logger.info('line-%03d', index)

    threads = [threading.Thread(target=write, args=(i * total // 4,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    close_gui_logger(logger)
    lines = (tmp_path / 'TwoPush-GUI_2026-09-09.log').read_text(encoding='utf-8').splitlines()
    assert len(lines) == total
    assert sorted(line.rsplit(' | ', 1)[-1] for line in lines) == [f'line-{i:03d}' for i in range(total)]


def test_cleanup_strictly_preserves_invalid_backup_and_cli_files(tmp_path):
    """清理只删除严格匹配的 GUI 日期文件。"""
    for date in ('2026-09-07', '2026-09-08', '2026-09-09'):
        (tmp_path / f'TwoPush-GUI_{date}.log').write_text(date, encoding='utf-8')
    for name in ('TwoPush-GUI_bad.log', 'TwoPush-GUI_2026-09-09.log.bak', 'TwoPush_20260909_120000.log'):
        (tmp_path / name).write_text('keep', encoding='utf-8')
    logger = setup_gui_logger(log_dir=tmp_path, max_files=2,
                             clock=FakeClock(datetime(2026, 9, 9)))
    cleanup_gui_logs(logger)
    close_gui_logger(logger)
    assert not (tmp_path / 'TwoPush-GUI_2026-09-07.log').exists()
    for name in ('TwoPush-GUI_2026-09-08.log', 'TwoPush-GUI_2026-09-09.log',
                 'TwoPush-GUI_bad.log', 'TwoPush-GUI_2026-09-09.log.bak',
                 'TwoPush_20260909_120000.log'):
        assert (tmp_path / name).exists()


@pytest.mark.parametrize('initial,updated,expected', [(15, 2, 2), (2, 20, 3)])
def test_max_files_can_increase_and_decrease(tmp_path, initial, updated, expected):
    """运行时调整上限应影响显式清理结果。"""
    for day in range(1, 4):
        (tmp_path / f'TwoPush-GUI_2026-09-{day:02d}.log').write_text(str(day), encoding='utf-8')
    logger = setup_gui_logger(log_dir=tmp_path, max_files=initial,
                             clock=FakeClock(datetime(2026, 9, 3)))
    set_max_files(logger, updated)
    cleanup_gui_logs(logger)
    close_gui_logger(logger)
    assert len(list(tmp_path.glob('TwoPush-GUI_*.log'))) == expected


def test_repeated_initialization_and_close_are_idempotent_and_preserve_handlers(tmp_path):
    """重复初始化不重复 handler，关闭只移除 GUI handler。"""
    logger = logging.getLogger('gui-idempotent-test')
    logger.handlers.clear()
    other = logging.StreamHandler()
    logger.addHandler(other)
    first = setup_gui_logger(log_dir=tmp_path, clock=FakeClock(datetime(2026, 9, 9)), name=logger.name)
    second = setup_gui_logger(log_dir=tmp_path, clock=FakeClock(datetime(2026, 9, 9)), name=logger.name)
    assert first is second
    assert len([handler for handler in logger.handlers if getattr(handler, 'is_gui_handler', False)]) == 1
    close_gui_logger(logger)
    close_gui_logger(logger)
    assert logger.handlers == [other]
    other.close()
    logger.handlers.clear()


def test_setup_replaces_closed_gui_handler(tmp_path):
    """已关闭但仍挂载的 GUI handler 不应被重新复用。"""
    logger = logging.getLogger('gui-closed-handler-test')
    logger.handlers.clear()
    clock = FakeClock(datetime(2026, 9, 9))
    setup_gui_logger(log_dir=tmp_path, clock=clock, name=logger.name)
    closed_handler = next(
        handler for handler in logger.handlers
        if getattr(handler, 'is_gui_handler', False))
    closed_handler.close()

    setup_gui_logger(log_dir=tmp_path, clock=clock, name=logger.name)
    handlers = [handler for handler in logger.handlers
                if getattr(handler, 'is_gui_handler', False)]
    assert len(handlers) == 1
    assert handlers[0] is not closed_handler
def test_setup_concurrent_initialization_keeps_one_gui_handler(tmp_path, monkeypatch):
    """20 个线程并发初始化只能挂载一个 GUI handler。"""
    logger = logging.getLogger('gui-concurrent-setup-test')
    logger.handlers.clear()
    barrier = threading.Barrier(20)
    constructor_entered = threading.Event()
    release_constructor = threading.Event()
    constructor_calls = 0
    constructor_lock = threading.Lock()
    errors = []
    clock = FakeClock(datetime(2026, 9, 9))
    original_init = logger_manager.DailyGuiFileHandler.__init__

    def blocked_init(handler, *args, **kwargs):
        """阻塞首个 handler 构造以验证初始化锁。"""
        nonlocal constructor_calls
        with constructor_lock:
            constructor_calls += 1
            first_call = constructor_calls == 1
        if first_call:
            constructor_entered.set()
            assert release_constructor.wait(timeout=2)
        original_init(handler, *args, **kwargs)

    monkeypatch.setattr(logger_manager.DailyGuiFileHandler, '__init__', blocked_init)

    def setup():
        """同步后初始化 GUI logger。"""
        try:
            barrier.wait(timeout=2)
            setup_gui_logger(name=logger.name, log_dir=tmp_path, clock=clock)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=setup) for _ in range(20)]
    for thread in threads:
        thread.start()
    assert constructor_entered.wait(timeout=2)
    assert not [handler for handler in logger.handlers
                if getattr(handler, 'is_gui_handler', False)]
    release_constructor.set()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert constructor_calls == 1
    assert len([handler for handler in logger.handlers
                if getattr(handler, 'is_gui_handler', False)]) == 1
    close_gui_logger(logger)
    logger.handlers.clear()


def test_close_racing_with_emit_does_not_reopen_handler(tmp_path, monkeypatch):
    """关闭与 emit 竞态时，已关闭 handler 不得重新打开文件。"""
    logger = setup_gui_logger(log_dir=tmp_path,
                             clock=FakeClock(datetime(2026, 9, 9)))
    handler = next(handler for handler in logger.handlers
                   if getattr(handler, 'is_gui_handler', False))
    original_open = open
    entered = threading.Event()
    release = threading.Event()

    def blocked_open(*args, **kwargs):
        """阻塞文件打开以制造关闭竞态。"""
        entered.set()
        release.wait()
        return original_open(*args, **kwargs)

    monkeypatch.setattr('builtins.open', blocked_open)
    emit_thread = threading.Thread(target=lambda: logger.info('race'))
    emit_thread.start()
    assert entered.wait(timeout=2)
    close_thread = threading.Thread(target=lambda: close_gui_logger(logger))
    close_thread.start()
    release.set()
    emit_thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert handler._closed
    assert handler._stream is None
    assert not list(tmp_path.glob('TwoPush-GUI_*.log'))
    logger.handlers.clear()


def test_emit_io_error_calls_handle_error_without_propagating(tmp_path, monkeypatch):
    """emit 的 I/O 异常应调用 handleError 且不向调用方传播。"""
    logger = setup_gui_logger(log_dir=tmp_path,
                             clock=FakeClock(datetime(2026, 9, 9)))
    handler = next(handler for handler in logger.handlers
                   if getattr(handler, 'is_gui_handler', False))
    errors = []
    monkeypatch.setattr(handler, 'handleError', errors.append)

    def broken_open(*args, **kwargs):
        """模拟文件打开失败。"""
        raise OSError('open failed')

    monkeypatch.setattr('builtins.open', broken_open)
    logger.info('io error')

    assert len(errors) == 1
    close_gui_logger(logger)
    logger.handlers.clear()
