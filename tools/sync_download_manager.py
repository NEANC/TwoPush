#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""将外部自更新下载管理器源码转换为 TwoPush 内置模块。

转换规则：
    1. 删除 .progress 相对导入、threading.Lock 与 collections.deque 导入。
    2. 删除 NetworkSpeedMeter、_format_speed、_update_progress 等进度相关实现。
    3. 同步更新受影响的函数签名与内部调用。
    4. 公开入口改名为 download_file，并新增多线程失败后的单线程回退。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATH = Path(r'g:\GitHub\Python_Self-Updater\download\manager.py')
OUTPUT_PATH = PROJECT_ROOT / 'modules' / 'download_manager.py'

# 转换后不允许残留的进度/输出相关 token
FORBIDDEN_TOKENS = [
    '.progress',
    'Lock',
    'NetworkSpeedMeter',
    '_format_speed',
    '_update_progress',
    'pbar',
    'speed_meter',
    'progress_lock',
    'create_download_progress_bar',
    'format_ok',
    'format_error',
    'print(',
    'download_file_with_progress',
    'deque',
]

# 新入口方法与 part 清理方法（替换 download_file_with_progress 之后的内容）
_ENTRY_CODE = '''    def download_file(self, url: str, save_path: str) -> bool:
        """下载文件，多线程失败时自动回退单线程。

        Args:
            url: 下载 URL。
            save_path: 保存路径。

        Returns:
            bool: 操作是否成功。
        """
        file_name = Path(url).name
        self.logger.info(f"开始下载文件: {file_name}")
        self.logger.debug(f"下载 URL: {url}")

        try:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)

            metadata_session = None
            try:
                metadata_session = requests.Session()
                metadata = self._get_download_metadata(metadata_session, url)
            finally:
                if metadata_session is not None:
                    metadata_session.close()

            target = Path(save_path)
            use_multithread = (
                self.download_threads >= 2
                and metadata.total_size > 0
                and metadata.supports_range
            )

            if use_multithread:
                success = self._download_multithreaded(url, target, metadata.total_size)
                if not success:
                    self._cleanup_part_files(target)
                    with requests.Session() as session:
                        success = self._download_single_threaded(
                            session, url, target, metadata.total_size,
                        )
            else:
                with requests.Session() as session:
                    success = self._download_single_threaded(
                        session, url, target, metadata.total_size,
                    )
            return success
        except Exception as e:
            self.logger.error(f"下载文件时发生错误: {e}")
            return False

    def _cleanup_part_files(self, target_path: Path) -> None:
        """清理目标文件关联的 part 临时文件。

        Args:
            target_path: 目标文件路径。
        """
        for part_path in target_path.parent.glob(f'{target_path.name}.part*'):
            if not part_path.is_file():
                continue
            try:
                part_path.unlink()
            except OSError as exc:
                self.logger.debug(f"清理 part 文件失败: {part_path}: {exc}")
'''


def _apply_replacements(content: str, replacements: list) -> str:
    """按顺序应用文本替换，缺失源文本时抛出 RuntimeError。

    Args:
        content: 待转换的源码文本。
        replacements: (源片段, 目标片段) 替换对列表。

    Returns:
        str: 替换后的源码文本。
    """
    for old, new in replacements:
        if old not in content:
            raise RuntimeError(f'替换源文本不存在: {old[:60]!r}')
        content = content.replace(old, new)
    return content


# 源码文本到目标文本的替换对（按源码出现顺序）
_REPLACEMENTS = [
    # 1. 精简导入区
    (
        'import logging\n'
        'import time\n'
        '\n'
        'from collections import deque\n'
        'from concurrent.futures import ThreadPoolExecutor, as_completed\n'
        'from dataclasses import dataclass\n'
        'from pathlib import Path\n'
        'from threading import Lock\n'
        '\n'
        'import requests\n'
        '\n'
        'from .progress import create_download_progress_bar, format_ok, format_error\n',
        'import logging\n'
        'import time\n'
        '\n'
        'from concurrent.futures import ThreadPoolExecutor, as_completed\n'
        'from dataclasses import dataclass\n'
        'from pathlib import Path\n'
        '\n'
        'import requests\n',
    ),
    # 2. 删除 NetworkSpeedMeter 类
    (
        'class NetworkSpeedMeter:\n'
        '    """统计网络层 chunk 到达速度。"""\n'
        '\n'
        '    def __init__(self, time_func=time.monotonic):\n'
        '        """初始化速度统计器。"""\n'
        '        self.time_func = time_func\n'
        '        self.started_at = time_func()\n'
        '        self.samples = deque()\n'
        '        self.total_bytes = 0\n'
        '\n'
        '    def update(self, byte_count: int) -> float:\n'
        '        """记录网络收到的字节数并返回 bytes/s。"""\n'
        '        now = self.time_func()\n'
        '        self.total_bytes += byte_count\n'
        '        self.samples.append((now, byte_count))\n'
        '        while self.samples and now - self.samples[0][0] > 1:\n'
        '            self.samples.popleft()\n'
        '        window_bytes = sum(size for _, size in self.samples)\n'
        '        window_seconds = now - self.samples[0][0] if self.samples else 0\n'
        '        if window_seconds > 0:\n'
        '            return window_bytes / window_seconds\n'
        '        elapsed = max(now - self.started_at, 0.001)\n'
        '        return self.total_bytes / elapsed\n'
        '\n'
        '\n',
        '',
    ),
    # 3. 删除 _format_speed 方法
    (
        '    def _format_speed(self, bytes_per_second: float) -> str:\n'
        '        """格式化网络下载速度。"""\n'
        '        if bytes_per_second < 1024:\n'
        '            return f"{bytes_per_second:.2f}B/s"\n'
        '        if bytes_per_second < 1024 * 1024:\n'
        '            return f"{bytes_per_second / 1024:.2f}KiB/s"\n'
        '        return f"{bytes_per_second / 1024 / 1024:.2f}MiB/s"\n'
        '\n',
        '',
    ),
    # 4. 删除 _update_progress 方法
    (
        '    def _update_progress(self, pbar, progress_lock: Lock, speed_meter: NetworkSpeedMeter,\n'
        '                         byte_count: int) -> None:\n'
        '        """更新进度条和网络速度。"""\n'
        '        with progress_lock:\n'
        '            speed = speed_meter.update(byte_count)\n'
        '            pbar.update(byte_count)\n'
        '            pbar.download_rate_fmt = self._format_speed(speed)\n'
        '            pbar.refresh()\n'
        '\n',
        '',
    ),
    # 5. _download_single_threaded 签名去掉进度参数
    (
        '    def _download_single_threaded(self, session, url: str, target_path: Path,\n'
        '                                  total_size: int, pbar, speed_meter: NetworkSpeedMeter,\n'
        '                                  progress_lock: Lock) -> bool:\n',
        '    def _download_single_threaded(self, session, url: str, target_path: Path,\n'
        '                                  total_size: int) -> bool:\n',
    ),
    # 6. 三个 docstring 中删除进度参数说明（保留 Args 与 Returns 之间的空行）
    (
        '            pbar: tqdm 进度条实例。\n'
        '            speed_meter: 网速统计器。\n'
        '            progress_lock: 进度更新锁。\n'
        '\n',
        '\n',
    ),
    # 7. 单线程 206 分支去掉进度更新
    (
        '                        if real_total > 0:\n'
        '                            known_total = real_total\n'
        '                            with progress_lock:\n'
        '                                pbar.total = real_total\n'
        '                                pbar.refresh()\n',
        '                        if real_total > 0:\n'
        '                            known_total = real_total\n',
    ),
    # 8. 单线程 200 分支去掉进度更新
    (
        '                        # 覆盖从头下载（进度条复位需在锁保护下）\n'
        '                        content_length = response.headers.get(\'Content-Length\')\n'
        '                        with progress_lock:\n'
        '                            pbar.n = 0\n'
        '                            if content_length and content_length.isdigit():\n'
        '                                known_total = int(content_length)\n'
        '                                pbar.total = known_total\n'
        '                            pbar.refresh()\n',
        '                        # 覆盖从头下载\n'
        '                        content_length = response.headers.get(\'Content-Length\')\n'
        '                        if content_length and content_length.isdigit():\n'
        '                            known_total = int(content_length)\n',
    ),
    # 9. 删除进度更新调用行（三处）
    (
        '\n'
        '                                    self._update_progress(pbar, progress_lock, speed_meter, len(chunk))',
        '',
    ),
    # 10. _download_part 签名去掉进度参数
    (
        '    def _download_part(self, session, url: str, save_path: Path, segment: DownloadSegment,\n'
        '                       pbar, speed_meter: NetworkSpeedMeter, progress_lock: Lock) -> bool:\n',
        '    def _download_part(self, session, url: str, save_path: Path,\n'
        '                       segment: DownloadSegment) -> bool:\n',
    ),
    # 11. _download_part 206 分支去掉进度回退
    (
        '                            if part_path.exists():\n'
        '                                if valid_size > 0:\n'
        '                                    with progress_lock:\n'
        '                                        pbar.n = max(0, pbar.n - valid_size)\n'
        '                                        pbar.refresh()\n'
        '                                part_path.unlink()\n',
        '                            if part_path.exists():\n'
        '                                part_path.unlink()\n',
    ),
    # 12. _download_multithreaded 签名去掉进度参数
    (
        '    def _download_multithreaded(self, url: str, target_path: Path, total_size: int,\n'
        '                                pbar, speed_meter: NetworkSpeedMeter,\n'
        '                                progress_lock: Lock) -> bool:\n',
        '    def _download_multithreaded(self, url: str, target_path: Path,\n'
        '                                total_size: int) -> bool:\n',
    ),
    # 13. 线程池提交去掉进度参数
    (
        '                    future = executor.submit(\n'
        '                        self._download_part,\n'
        '                        sessions[i],\n'
        '                        url,\n'
        '                        target_path,\n'
        '                        segment,\n'
        '                        pbar,\n'
        '                        speed_meter,\n'
        '                        progress_lock,\n'
        '                    )\n',
        '                    future = executor.submit(\n'
        '                        self._download_part,\n'
        '                        sessions[i],\n'
        '                        url,\n'
        '                        target_path,\n'
        '                        segment,\n'
        '                    )\n',
    ),
    # 14. 删除 _get_existing_bytes 方法（进度条初始字节统计）
    (
        '    def _get_existing_bytes(self, target_path: Path, total_size: int) -> int:\n'
        '        """获取进度条初始已完成字节数。\n'
        '\n'
        '        Args:\n'
        '            target_path: 目标文件路径。\n'
        '            total_size: 已知总大小（0 表示未知）。\n'
        '\n'
        '        Returns:\n'
        '            int: 已完成字节数。\n'
        '        """\n'
        '        if not target_path.exists():\n'
        '            return 0\n'
        '        current_size = target_path.stat().st_size\n'
        '        if total_size > 0 and current_size > total_size:\n'
        '            return 0\n'
        '        return current_size\n'
        '\n',
        '',
    ),
]


def transform_manager(source: str) -> str:
    """将外部下载管理器源码转换为无进度输出的内置模块。

    Args:
        source: 外部下载管理器源码文本。

    Returns:
        str: 转换后的源码文本。
    """
    content = source.replace('\r\n', '\n').replace('\r', '\n')
    content = _apply_replacements(content, _REPLACEMENTS)
    # 重写公开入口：download_file_with_progress -> download_file
    marker = '    def download_file_with_progress(self, url: str, save_path: str) -> bool:'
    head, sep, _tail = content.partition(marker)
    if not sep:
        raise RuntimeError('未找到 download_file_with_progress 入口方法')
    return head + _ENTRY_CODE


def _assert_no_forbidden_tokens(content: str) -> None:
    """校验转换结果不含任何进度/输出相关 token。

    Args:
        content: 转换后的源码文本。

    Raises:
        RuntimeError: 存在禁用 token 时抛出。
    """
    for token in FORBIDDEN_TOKENS:
        if token in content:
            raise RuntimeError(f'转换结果仍包含禁用 token: {token!r}')


def main() -> None:
    """执行转换并写入目标模块。"""
    source = SOURCE_PATH.read_text(encoding='utf-8')
    transformed = transform_manager(source)
    _assert_no_forbidden_tokens(transformed)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(transformed, encoding='utf-8', newline='\n')
    print(f'已生成: {OUTPUT_PATH}')


if __name__ == '__main__':
    main()
