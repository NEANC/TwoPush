#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""TwoPush CLI 推送进程控制器。"""

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path


_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class PushProcessManager:
    """管理单个 TwoPush CLI 子进程及其输出。"""

    def __init__(self, program_dir=None, gui_mode=False, logger=None):
        """初始化控制器。"""
        self.program_dir = Path(program_dir or Path(__file__).resolve().parent.parent)
        self.temp_dir = self.program_dir / 'Temp'
        self.gui_mode = gui_mode
        self.logger = logger
        self._lock = threading.RLock()
        self._process = None
        self._task = None
        self._stop_requested = False
        self._reader_threads = []
        self._wait_thread = None
        self._temporary_path = None
        self._sequence = 0

    def _build_command(self, json_path, config_path):
        """构造 TwoPush CLI 命令。"""
        return [
            sys.executable,
            str(self.program_dir / 'TwoPush.py'),
            '-c',
            str(config_path),
            '-p',
            str(json_path),
        ]

    @staticmethod
    def _safe_name(name):
        """替换 Windows 非法文件名字符并保留中文和空格。"""
        cleaned = _INVALID_NAME_CHARS.sub('_', str(name)).rstrip(' .')
        return cleaned or 'push'

    def _temp_path(self, source_name):
        """生成不覆盖既有文件的临时 JSON 路径。"""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        base = self._safe_name(Path(str(source_name)).stem)
        candidate = self.temp_dir / f'Temp_{base}.json'
        index = 1
        while candidate.exists():
            candidate = self.temp_dir / f'Temp_{base}_{index}.json'
            index += 1
        return candidate

    def _create_temp_file(self, source_name, content):
        """写入临时 JSON 并在完成后原子切换为可交接文件。"""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        base = self._safe_name(Path(str(source_name)).stem)
        index = 0
        while True:
            suffix = '' if index == 0 else f'_{index}'
            path = self.temp_dir / f'Temp_{base}{suffix}.json'
            try:
                with path.open('x', encoding='utf-8'):
                    pass
                staging_path = self.temp_dir / f'.{path.name}.{uuid.uuid4().hex}.tmp'
                try:
                    staging_path.write_text(content, encoding='utf-8')
                    os.replace(staging_path, path)
                except Exception:
                    staging_path.unlink(missing_ok=True)
                    path.unlink(missing_ok=True)
                    raise
                return path
            except FileExistsError:
                index += 1
            except Exception:
                path.unlink(missing_ok=True)
                raise

    def _start(self, json_path, config_path, temporary_path=None):
        """启动一个 CLI 推送任务。"""
        with self._lock:
            if self._wait_thread and self._wait_thread.is_alive():
                raise RuntimeError('上一个推送任务尚未完成收尾')
            task_id = uuid.uuid4().hex
            task = {
                'task_id': task_id,
                'status': 'running',
                'exit_code': None,
                'outputs': [],
                'reader_failed': False,
            }
            self._task = task
            self._stop_requested = False
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            env['PYTHONIOENCODING'] = 'utf-8'
            if self.gui_mode:
                env['TWOPUSH_GUI'] = '1'
                env['TWOPUSH_SAVE_LOGS'] = '0'
            else:
                env.pop('TWOPUSH_GUI', None)
                env.pop('TWOPUSH_SAVE_LOGS', None)
            try:
                self._process = subprocess.Popen(
                    self._build_command(json_path, config_path),
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding='utf-8',
                    errors='replace',
                    universal_newlines=True,
                    bufsize=1,
                    env=env,
                )
            except Exception:
                self._task['status'] = 'failed'
                raise
            stdout_thread = threading.Thread(
                target=self._read_stream,
                args=(self._process.stdout, 'stdout', task),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._read_stream,
                args=(self._process.stderr, 'stderr', task),
                daemon=True,
            )
            self._reader_threads = [stdout_thread, stderr_thread]
            self._temporary_path = temporary_path
            stdout_thread.start()
            stderr_thread.start()
            wait_thread = threading.Thread(
                target=self._wait_process,
                args=(temporary_path, task, list(self._reader_threads)),
                daemon=True,
            )
            self._wait_thread = wait_thread
            wait_thread.start()
            return task_id

    def start_file_push(self, json_path, config_path):
        """异步启动文件推送。"""
        return self._start(Path(json_path), Path(config_path))

    def start_payload_push(self, payload, config_path, source_name='push'):
        """写入临时 JSON 后异步启动推送，并在结束时清理。"""
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path = self._create_temp_file(source_name, content)
        try:
            return self._start(temp_path, Path(config_path), temp_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _read_stream(self, stream, stream_name, task):
        """读取子进程输出并分配全局递增序号。"""
        try:
            for line in stream:
                with self._lock:
                    self._sequence += 1
                    task['outputs'].append({
                        'sequence': self._sequence,
                        'stream': stream_name,
                        'message': line.rstrip('\r\n'),
                    })
                    terminal = sys.stderr if stream_name == 'stderr' else sys.stdout
                    terminal.write(line)
                    terminal.flush()
                    if self.gui_mode and self.logger is not None:
                        try:
                            from modules.logger_manager import sanitize_log_message
                            message = sanitize_log_message(line.rstrip('\r\n'), root=self.program_dir)
                            log_method = self.logger.error if stream_name == 'stderr' else self.logger.info
                            log_method(message)
                        except Exception:
                            pass
        except Exception:
            with self._lock:
                task['reader_failed'] = True
                if task['status'] == 'running':
                    task['status'] = 'failed'
        finally:
            try:
                stream.close()
            except Exception:
                with self._lock:
                    task['reader_failed'] = True
                    if task['status'] == 'running':
                        task['status'] = 'failed'

    def _wait_process(self, temporary_path, task, reader_threads):
        """等待子进程和输出线程结束，更新状态并清理临时文件。"""
        process = self._process
        try:
            exit_code = process.wait()
        except Exception:
            with self._lock:
                if task:
                    task['status'] = 'failed'
                    task['exit_code'] = None
            exit_code = None
        for reader_thread in reader_threads:
            if reader_thread.is_alive():
                reader_thread.join()
        with self._lock:
            if task:
                task['exit_code'] = exit_code
                if task['status'] == 'running':
                    if task['reader_failed'] or exit_code != 0:
                        task['status'] = 'failed'
                    else:
                        task['status'] = 'success'
        if temporary_path:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get_status(self, cursor=0):
        """返回任务状态及指定序号之后的输出。"""
        with self._lock:
            if not self._task:
                return {'task_id': None, 'status': 'idle', 'exit_code': None, 'outputs': []}
            result = dict(self._task)
            result['outputs'] = [item for item in self._task['outputs'] if item['sequence'] > cursor]
            return result

    def stop(self):
        """停止当前任务及其 Windows 进程树。"""
        with self._lock:
            if (
                    not self._process or
                    not self._task or
                    self._stop_requested or
                    not self._wait_thread or
                    not self._wait_thread.is_alive()):
                return False
            self._stop_requested = True
            if os.name == 'nt':
                try:
                    result = subprocess.run(['taskkill', '/PID', str(self._process.pid), '/T', '/F'], check=False)
                except OSError:
                    self._task['status'] = 'failed'
                    return False
                if result is not None and result.returncode != 0:
                    self._task['status'] = 'failed'
                    return False
            else:
                try:
                    self._process.terminate()
                except OSError:
                    self._task['status'] = 'failed'
                    return False
            self._task['status'] = 'stopped'
            return True

    def shutdown(self):
        """服务退出时停止正在运行的任务并等待收尾。"""
        with self._lock:
            stop_requested = self._stop_requested
        if not stop_requested:
            self.stop()
        wait_thread = self._wait_thread
        if wait_thread and wait_thread is not threading.current_thread():
            wait_thread.join(2)
        if wait_thread and wait_thread.is_alive():
            with self._lock:
                if self._task and self._task['status'] == 'running':
                    self._task['status'] = 'failed'
            return
        with self._lock:
            if self._task and self._task['status'] == 'running':
                self._task['status'] = 'stopped'
