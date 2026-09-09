#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""GUI 按日文件日志测试。"""

import logging
import threading
from datetime import datetime

import pytest

from modules.logger_manager import (
    cleanup_gui_logs,
    close_gui_logger,
    set_max_files,
    setup_gui_logger,
)


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
