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

    def __init__(self, program_dir=None):
        """初始化控制器。"""
        self.program_dir = Path(program_dir or Path(__file__).resolve().parent.parent)
        self.temp_dir = self.program_dir / 'Temp'
        self._lock = threading.RLock()
        self._process = None
        self._task = None
        self._stop_requested = False
        self._reader_threads = []
    def _build_command(self, json_path, config_path):
        """构造 TwoPush CLI 命令。"""
        return [
            sys.executable,
            str(self.program_dir / 'TwoPush.py'),
            '--config',
            str(config_path),
            '--push',
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

    def _start(self, json_path, config_path, temporary_path=None):
        """启动一个 CLI 推送任务。"""
        with self._lock:
            if self._task and self._task['status'] == 'running':
                raise RuntimeError('已有推送任务正在运行')
            task_id = uuid.uuid4().hex
            self._task = {
                'task_id': task_id,
                'status': 'running',
                'exit_code': None,
                'outputs': [],
            }
            self._stop_requested = False
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
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
                args=(self._process.stdout, 'stdout'),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._read_stream,
                args=(self._process.stderr, 'stderr'),
                daemon=True,
            )
            self._reader_threads = [stdout_thread, stderr_thread]
            stdout_thread.start()
            stderr_thread.start()
            threading.Thread(target=self._wait_process, args=(temporary_path,), daemon=True).start()
            return task_id

    def start_file_push(self, json_path, config_path):
        """异步启动文件推送。"""
        return self._start(Path(json_path), Path(config_path))

    def start_payload_push(self, payload, config_path, source_name='push'):
        """写入临时 JSON 后异步启动推送，并在结束时清理。"""
        temp_path = self._temp_path(source_name)
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            return self._start(temp_path, Path(config_path), temp_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _read_stream(self, stream, stream_name):
        """读取子进程输出并分配全局递增序号。"""
        try:
            for line in stream:
                with self._lock:
                    if self._task:
                        self._task['outputs'].append({
                            'sequence': len(self._task['outputs']) + 1,
                            'stream': stream_name,
                            'text': line.rstrip('\r\n'),
                        })
        finally:
            stream.close()

    def _wait_process(self, temporary_path):
        """等待子进程和输出线程结束，更新状态并清理临时文件。"""
        process = self._process
        exit_code = process.wait()
        with self._lock:
            if self._task:
                self._task['exit_code'] = exit_code
                self._task['status'] = 'stopped' if self._stop_requested else ('success' if exit_code == 0 else 'failed')
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

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
            if not self._process or not self._task or self._task['status'] != 'running':
                return False
            self._stop_requested = True
            self._task['status'] = 'stopped'
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self._process.pid), '/T', '/F'], check=False)
            else:
                self._process.terminate()
            return True

    def shutdown(self):
        """服务退出时停止正在运行的任务。"""
        self.stop()
