#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""日志管理器：控制台彩色输出 + 文件日志 + 旧日志清理。"""

import configparser
import logging
import re
import threading

import colorama

from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = "logs"
LOG_PREFIX = "TwoPush"
_GUI_LOG_PATTERN = re.compile(r'^TwoPush-GUI_(\d{4}-\d{2}-\d{2})\.log$')


class ColoredFormatter(logging.Formatter):
    """带颜色的日志格式化器，仅作用于控制台输出。"""

    LEVEL_COLORS = {
        'DEBUG': colorama.Fore.CYAN,
        'INFO': colorama.Fore.WHITE,
        'WARNING': colorama.Fore.YELLOW,
        'ERROR': colorama.Fore.RED,
        'CRITICAL': colorama.Back.RED + colorama.Fore.BLACK + colorama.Style.BRIGHT,
    }

    def __init__(self, *args, strip_ansi: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.strip_ansi = strip_ansi
        colorama.init(autoreset=True)

    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        if self.strip_ansi:
            return result
        color = self.LEVEL_COLORS.get(record.levelname, colorama.Fore.WHITE)
        return f"{color}{result}{colorama.Style.RESET_ALL}"


def setup_logger(name: str = "TwoPush", console_enabled: bool = True) -> logging.Logger:
    """创建并配置控制台日志记录器。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if logger.handlers or not console_enabled:
        return logger
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(ColoredFormatter(
        '%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%H:%M:%S'))
    logger.addHandler(handler)
    return logger


def raw_read_save_enabled(config_file: str, section: str = 'Logs', key: str = 'save_enabled') -> bool:
    """粗读配置文件判断是否启用日志保存。"""
    if not Path(config_file).exists():
        return True
    try:
        raw = configparser.ConfigParser()
        raw.read(config_file, encoding='utf-8')
        return raw.getboolean(section, key, fallback=True)
    except Exception:
        return True


def add_file_logger(logger: logging.Logger, version: str = "", log_dir: Optional[str] = None,
                    log_prefix: Optional[str] = None) -> logging.FileHandler:
    """添加文件日志记录器。"""
    directory = Path(log_dir or LOG_DIR)
    directory.mkdir(exist_ok=True)
    prefix = log_prefix or LOG_PREFIX
    log_file = directory / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    handler = logging.FileHandler(log_file, encoding='utf-8')
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    logger.addHandler(handler)
    if version:
        logger.debug(f"当前软件版本: {version}")
    return handler


def cleanup_old_logs(logger: logging.Logger, max_files: int, log_dir: Optional[str] = None,
                     log_prefix: Optional[str] = None) -> None:
    """清理多余的日志文件。"""
    directory = Path(log_dir or LOG_DIR)
    if not directory.exists():
        return
    files = list(directory.glob(f"{log_prefix or LOG_PREFIX}_*.log"))
    if len(files) <= max_files:
        return
    for path in sorted(files, key=lambda item: item.stat().st_mtime)[:-max_files]:
        try:
            path.unlink()
        except OSError:
            logger.error(f"删除日志文件 {path} 失败")


class DailyGuiFileHandler(logging.Handler):
    """按日期切换并追加写入 GUI 日志文件。"""

    def __init__(self, log_dir, max_files=15, clock=None):
        super().__init__(logging.DEBUG)
        self.log_dir = Path(log_dir)
        self.max_files = max_files if isinstance(max_files, int) and max_files >= 1 else 15
        self.clock = clock or datetime
        self._lock = threading.RLock()
        self._current_date = None
        self._stream = None
        self.is_gui_handler = True
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _date(self):
        """获取当前日期字符串。"""
        return self.clock.now().strftime('%Y-%m-%d')

    def emit(self, record):
        """线程安全地写入一条日志。"""
        with self._lock:
            current_date = self._date()
            if self._stream is None or self._current_date != current_date:
                if self._stream is not None:
                    self._stream.close()
                self._stream = open(self.log_dir / f'TwoPush-GUI_{current_date}.log', 'a', encoding='utf-8')
                changed = self._current_date is not None
                self._current_date = current_date
                if changed:
                    cleanup_gui_logs(self)
            self._stream.write(self.format(record) + '\n')
            self._stream.flush()

    def set_max_files(self, max_files):
        """设置保留的 GUI 日志文件数量。"""
        if isinstance(max_files, int) and max_files >= 1:
            self.max_files = max_files

    def close(self):
        """幂等关闭当前日志文件。"""
        with self._lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            super().close()


def setup_gui_logger(name='TwoPush.GUI', log_dir=None, max_files=15, clock=None):
    """创建或复用 GUI 按日 UTF-8 日志记录器。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    directory = Path(log_dir) if log_dir is not None else Path.cwd() / 'logs' / 'gui'
    for handler in logger.handlers[:]:
        if getattr(handler, 'is_gui_handler', False):
            if (not handler._closed and Path(handler.log_dir) == directory
                    and (clock is None or handler.clock is clock)):
                handler.set_max_files(max_files)
                return logger
            logger.removeHandler(handler)
            handler.close()
    handler = DailyGuiFileHandler(directory, max_files, clock)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    logger.addHandler(handler)
    return logger


def cleanup_gui_logs(logger):
    """清理 GUI 日志目录中超出上限的严格匹配文件。"""
    handler = logger if isinstance(logger, DailyGuiFileHandler) else next(
        (item for item in logger.handlers if getattr(item, 'is_gui_handler', False)), None)
    if handler is None:
        return
    with handler._lock:
        files = []
        for path in handler.log_dir.iterdir():
            match = _GUI_LOG_PATTERN.match(path.name)
            if match and path.is_file():
                files.append((match.group(1), path.stat().st_mtime, path))
        files.sort(key=lambda item: (item[0], item[1]))
        for _, _, path in files[:-handler.max_files]:
            try:
                path.unlink()
            except OSError:
                pass


def close_gui_logger(logger):
    """关闭并移除 GUI handler，不影响其他 handler。"""
    for handler in logger.handlers[:]:
        if getattr(handler, 'is_gui_handler', False):
            logger.removeHandler(handler)
            handler.close()


def set_max_files(logger, max_files):
    """更新 GUI handler 的日志保留数量。"""
    for handler in logger.handlers:
        if getattr(handler, 'is_gui_handler', False):
            handler.set_max_files(max_files)
            return
