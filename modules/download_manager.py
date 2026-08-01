#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import logging
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
DOWNLOAD_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
DOWNLOAD_RETRIES = 3
CHUNK_SIZE = 128 * 1024
USER_AGENT = 'M9A-Update-Assistant'


@dataclass(frozen=True)
class DownloadMetadata:
    """下载元数据。"""

    total_size: int
    supports_range: bool


@dataclass(frozen=True)
class DownloadSegment:
    """下载分段闭区间。"""

    index: int
    start: int
    end: int

    @property
    def length(self) -> int:
        """返回分段字节长度。"""
        return self.end - self.start + 1


class DownloadManager:
    """下载管理器，负责文件下载与缓存检查"""

    def __init__(self, proxy: str, temp_folder: str, logger: logging.Logger,
                 download_threads: int = 4):
        """
        初始化下载管理器

        Args:
            proxy: 代理地址
            temp_folder: 临时文件夹路径
            logger: 日志记录器
            download_threads: 下载线程数
        """
        self.proxy = proxy
        self.temp_folder = temp_folder
        self.logger = logger
        self.download_threads = download_threads

    def _build_proxies(self):
        """构建 requests 代理参数。"""
        if not self.proxy:
            return None
        return {'http': self.proxy, 'https': self.proxy}

    def _get_download_metadata(self, session, url: str) -> DownloadMetadata:
        """通过 HEAD 获取下载元数据。"""
        try:
            with session.head(
                url,
                headers={'User-Agent': USER_AGENT},
                timeout=DOWNLOAD_TIMEOUT,
                proxies=self._build_proxies(),
                allow_redirects=True,
            ) as response:
                response.raise_for_status()
                try:
                    total_size = int(response.headers.get('Content-Length', '0'))
                except ValueError:
                    total_size = 0
                supports_range = response.headers.get('Accept-Ranges', '').lower() == 'bytes'
                return DownloadMetadata(
                    total_size=max(total_size, 0),
                    supports_range=supports_range,
                )
        except requests.RequestException as exc:
            self.logger.debug(
                f"HEAD 探测失败，降级单线程下载: {type(exc).__name__}",
            )
            return DownloadMetadata(total_size=0, supports_range=False)

    def _split_segments(self, total_size: int, threads: int) -> list[DownloadSegment]:
        """将文件大小拆分为闭区间分段。"""
        segment_count = min(max(threads, 1), total_size)
        base_size, remainder = divmod(total_size, segment_count)
        segments = []
        start = 0
        for index in range(segment_count):
            length = base_size + (1 if index < remainder else 0)
            end = start + length - 1
            segments.append(DownloadSegment(index, start, end))
            start = end + 1
        return segments

    def _parse_content_range(self, response_headers: dict):
        """解析 Content-Range 响应头为 (start, end, total)。

        总长度未知时 total 为 -1，无法解析时返回 None。

        Returns:
            三元组 (start, end, total) 或 None。
        """
        content_range = response_headers.get('Content-Range', '')
        if not content_range.startswith('bytes '):
            return None
        try:
            range_part, total_part = content_range.split(' ', 1)[1].split('/', 1)
            start, end = (int(part) for part in range_part.split('-'))
            if start > end:
                return None
            total = int(total_part) if total_part.isdigit() else -1
        except (ValueError, IndexError):
            return None
        return start, end, total

    def _download_single_threaded(self, session, url: str, target_path: Path,
                                  total_size: int) -> bool:
        """单线程下载，支持续传和重试。

        Args:
            session: requests Session。
            url: 下载 URL。
            target_path: 目标文件路径。
            total_size: 已知总大小（0 表示未知）。

        Returns:
            bool: 是否下载成功。
        """
        target = Path(target_path)

        # GET 可从响应头推导完整总大小时用于最终校验
        known_total = total_size

        # 已知总大小且文件已完整
        if known_total > 0 and target.exists() and target.stat().st_size == known_total:
            return True

        existing_size = 0
        if target.exists():
            existing_size = target.stat().st_size

        # 本地文件大于已知总大小，覆盖重下
        if known_total > 0 and existing_size >= known_total:
            existing_size = 0

        headers = {'User-Agent': USER_AGENT}
        if existing_size > 0:
            headers['Range'] = f'bytes={existing_size}-'

        for attempt in range(DOWNLOAD_RETRIES):
            try:
                with session.get(
                    url,
                    headers=headers,
                    timeout=DOWNLOAD_TIMEOUT,
                    proxies=self._build_proxies(),
                    stream=True,
                ) as response:

                    if response.status_code == 206:
                        response.raise_for_status()
                        # 解析 Content-Range 并校验起点与本地续传偏移一致
                        range_info = self._parse_content_range(response.headers)
                        if range_info is None or range_info[0] != existing_size:
                            self.logger.debug(
                                f"206 响应起点与本地偏移 {existing_size} 不一致，"
                                f"删除不可信文件重新下载",
                            )
                            if target.exists():
                                target.unlink()
                            existing_size = 0
                            if 'Range' in headers:
                                del headers['Range']
                            continue
                        range_start, range_end, range_total = range_info
                        if (
                            (range_total > 0 and range_end >= range_total)
                            or (known_total > 0 and range_total != known_total)
                        ):
                            self.logger.debug(
                                "206 响应范围或总大小与预期不一致，删除不可信文件重新下载",
                            )
                            if target.exists():
                                target.unlink()
                            existing_size = 0
                            if 'Range' in headers:
                                del headers['Range']
                            continue
                        # 续传追加并统计本次实际写入的字节数
                        written = 0
                        with open(target, 'ab') as f:
                            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)
                                    written += len(chunk)

                        if range_total > 0:
                            # 总长度已知：声明区间必须闭合且完整文件大小与 total 一致
                            if known_total == 0:
                                known_total = range_total
                            expected = range_end - range_start + 1
                            if written < expected:
                                existing_size = target.stat().st_size
                                headers['Range'] = f'bytes={existing_size}-'
                                continue
                            if written > expected or range_end != known_total - 1:
                                self.logger.debug(
                                    "206 响应区间未闭合或写入字节数异常，删除不可信文件重新下载",
                                )
                                if target.exists():
                                    target.unlink()
                                existing_size = 0
                                if 'Range' in headers:
                                    del headers['Range']
                                continue
                            if target.stat().st_size != known_total:
                                existing_size = target.stat().st_size
                                headers['Range'] = f'bytes={existing_size}-'
                                continue
                            return True
                        # 总长度未知：必须写满声明的区间长度，短读则更新偏移重试
                        expected = range_end - range_start + 1
                        if written < expected:
                            existing_size = target.stat().st_size
                            headers['Range'] = f'bytes={existing_size}-'
                            continue
                        if written > expected:
                            self.logger.debug(
                                "206 响应写入字节超过声明范围，删除不可信文件重新下载",
                            )
                            if target.exists():
                                target.unlink()
                            existing_size = 0
                            if 'Range' in headers:
                                del headers['Range']
                            continue
                        return True

                    elif response.status_code == 200:
                        response.raise_for_status()
                        # 覆盖从头下载
                        content_length = response.headers.get('Content-Length')
                        response_total = (
                            int(content_length)
                            if content_length and content_length.isdigit()
                            else 0
                        )
                        if known_total > 0 and response_total > 0 and response_total != known_total:
                            self.logger.debug(
                                "200 响应声明大小与预期不一致，重新下载",
                            )
                            if target.exists():
                                target.unlink()
                            existing_size = 0
                            if 'Range' in headers:
                                del headers['Range']
                            continue
                        if known_total == 0 and response_total > 0:
                            known_total = response_total

                        with open(target, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)

                        # C1: 使用可变的 known_total 做校验
                        if known_total == 0:
                            return target.exists() and target.stat().st_size > 0
                        if target.stat().st_size != known_total:
                            self.logger.debug(
                                "200 响应大小与预期不一致，删除不可信文件重新下载",
                            )
                            if target.exists():
                                target.unlink()
                            existing_size = 0
                            if 'Range' in headers:
                                del headers['Range']
                            continue
                        return True

                    elif response.status_code == 416:
                        # I2: 416 进入重试（规格要求），不直接返回 False
                        # 文件完整时直接成功
                        if known_total > 0 and target.exists() and target.stat().st_size == known_total:
                            return True
                        # 不完整 → 记录失败并进入重试循环
                        self.logger.debug(
                            f"下载尝试 {attempt + 1}: 416 Range Not Satisfiable，"
                            f"文件大小={target.stat().st_size if target.exists() else 0}，"
                            f"已知总大小={known_total}",
                        )

                    else:
                        response.raise_for_status()

            except requests.RequestException as exc:
                self.logger.debug(
                    f"下载尝试 {attempt + 1} 失败: {type(exc).__name__}",
                )

            # 重试前重新读取文件大小，更新 Range 偏移
            existing_size = target.stat().st_size if target.exists() else 0
            if known_total > 0 and existing_size >= known_total:
                existing_size = 0
            if existing_size > 0:
                headers['Range'] = f'bytes={existing_size}-'
            elif 'Range' in headers:
                del headers['Range']

            if attempt < DOWNLOAD_RETRIES - 1:
                time.sleep(1)

        return False

    def _get_part_path(self, save_path: Path, index: int) -> Path:
        """返回分段临时文件路径。

        Args:
            save_path: 目标文件路径。
            index: 分段索引。

        Returns:
            Path: part 文件路径。
        """
        return Path(f"{save_path}.part{index}")

    def _get_valid_part_size(self, save_path: Path, segment: DownloadSegment) -> int:
        """判断 part 文件有效性并返回有效字节数。

        Args:
            save_path: 目标文件路径。
            segment: 下载分段。

        Returns:
            int: 0 表示需重新下载，-1 表示已完成，
                正数表示已有字节数（用于续传）。
        """
        part_path = self._get_part_path(save_path, segment.index)
        if not part_path.exists():
            return 0
        size = part_path.stat().st_size
        if size < segment.length:
            return size
        if size == segment.length:
            return -1
        # size > segment.length，异常情况，删除重下
        try:
            part_path.unlink()
        except OSError as exc:
            self.logger.debug(f"删除异常 part 文件失败: {part_path}: {exc}")
            return 0
        return 0

    def _content_range_matches(self, segment: DownloadSegment, request_start: int,
                               total_size: int, response_headers: dict) -> bool:
        """校验响应 Content-Range 的区间与完整文件大小。

        Args:
            segment: 下载分段。
            request_start: 请求的起始字节。
            total_size: 本轮下载的完整文件大小。
            response_headers: 响应头字典。

        Returns:
            bool: Content-Range 是否匹配。
        """
        content_range = self._parse_content_range(response_headers)
        if content_range is None:
            return False
        start, end, total = content_range
        return (
            start == request_start
            and end == segment.end
            and total > 0
            and total == total_size
            and end < total
        )

    def _download_part(self, session, url: str, save_path: Path,
                       segment: DownloadSegment, total_size: int) -> bool:
        """下载单个分段到 part 文件，内部执行 3 次重试。

        Args:
            session: 独立 requests Session。
            url: 下载 URL。
            save_path: 目标文件路径（用于生成 part 路径）。
            segment: 下载分段。

        Returns:
            bool: 分段是否下载成功。
        """
        valid_size = self._get_valid_part_size(save_path, segment)
        if valid_size == -1:
            return True

        range_start = segment.start + valid_size

        for attempt in range(DOWNLOAD_RETRIES):
            headers = {'User-Agent': USER_AGENT}
            headers['Range'] = f'bytes={range_start}-{segment.end}'

            try:
                with session.get(
                    url,
                    headers=headers,
                    timeout=DOWNLOAD_TIMEOUT,
                    proxies=self._build_proxies(),
                    stream=True,
                ) as response:

                    if response.status_code == 206:
                        if not self._content_range_matches(
                            segment, range_start, total_size, response.headers,
                        ):
                            part_path = self._get_part_path(save_path, segment.index)
                            if part_path.exists():
                                try:
                                    part_path.unlink()
                                except OSError:
                                    self.logger.debug("删除错位 part 文件失败")
                                    return False
                            range_start = segment.start
                            valid_size = 0
                            continue

                        response.raise_for_status()

                        part_path = self._get_part_path(save_path, segment.index)
                        mode = 'ab' if range_start > segment.start else 'wb'
                        with open(part_path, mode) as f:
                            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)

                        part_size = part_path.stat().st_size
                        if part_size == segment.length:
                            return True
                        # 写入后 part 不完整，继续重试续传
                        valid_size = part_size
                        range_start = segment.start + valid_size
                        continue

                    elif response.status_code == 200:
                        continue

                    elif response.status_code == 416:
                        part_path = self._get_part_path(save_path, segment.index)
                        if part_path.exists() and part_path.stat().st_size == segment.length:
                            return True
                        # I1: 416 不完整时不删除 part，进入重试
                        valid_size = self._get_valid_part_size(save_path, segment)
                        if valid_size == -1:
                            return True
                        range_start = segment.start + max(valid_size, 0)
                        continue

                    else:
                        response.raise_for_status()

            except requests.RequestException as exc:
                self.logger.debug(
                    f"分段 {segment.index} 下载尝试 {attempt + 1} 失败: "
                    f"{type(exc).__name__}",
                )

            if attempt < DOWNLOAD_RETRIES - 1:
                time.sleep(1)

        return False

    def _merge_parts(self, save_path: Path, segments: list[DownloadSegment]) -> bool:
        """按顺序合并所有 part 文件（调用方负责校验成功后删除）。

        Args:
            save_path: 目标文件路径。
            segments: 下载分段列表。

        Returns:
            bool: 合并是否成功。
        """
        try:
            with open(save_path, 'wb') as target:
                for segment in segments:
                    part_path = self._get_part_path(save_path, segment.index)
                    with open(part_path, 'rb') as part:
                        while True:
                            chunk = part.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            target.write(chunk)
            return True
        except Exception:
            return False

    def _download_multithreaded(self, url: str, target_path: Path,
                                total_size: int) -> bool:
        """多线程分段下载协调器。

        Args:
            url: 下载 URL。
            target_path: 目标文件路径。
            total_size: 文件总大小。

        Returns:
            bool: 是否全部下载成功。
        """
        segments = self._split_segments(total_size, self.download_threads)

        sessions = [requests.Session() for _ in segments]
        try:
            with ThreadPoolExecutor(max_workers=len(segments)) as executor:
                futures = {}
                for i, segment in enumerate(segments):
                    future = executor.submit(
                        self._download_part,
                        sessions[i],
                        url,
                        target_path,
                        segment,
                        total_size,
                    )
                    futures[future] = segment

                for future in as_completed(futures):
                    if not future.result():
                        return False

            if not self._merge_parts(target_path, segments):
                return False

            if target_path.exists() and target_path.stat().st_size == total_size:
                for segment in segments:
                    part_path = self._get_part_path(target_path, segment.index)
                    if not part_path.exists():
                        continue
                    try:
                        part_path.unlink()
                    except OSError:
                        self.logger.debug("删除已合并 part 文件失败")
                        return False
                return True
            return False
        finally:
            for session in sessions:
                session.close()

    def download_file(self, url: str, save_path: str) -> bool:
        """下载文件，多线程失败时自动回退单线程。

        Args:
            url: 下载 URL。
            save_path: 保存路径。

        Returns:
            bool: 操作是否成功。
        """
        file_name = Path(urlsplit(url).path).name or '下载文件'
        self.logger.info(f"开始下载文件: {file_name}")

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
                try:
                    success = self._download_multithreaded(url, target, metadata.total_size)
                except Exception as exc:
                    self.logger.debug(
                        f"多线程下载异常，回退单线程下载: {type(exc).__name__}",
                    )
                    success = False
                if not success:
                    if not self._cleanup_part_files(target):
                        return False
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
        except Exception as exc:
            self.logger.error(f"下载文件时发生错误: {type(exc).__name__}")
            return False

    def _cleanup_part_files(self, target_path: Path) -> bool:
        """清理目标文件关联的 part 临时文件。

        Args:
            target_path: 目标文件路径。

        Returns:
            bool: 是否已清理全部 part 文件。
        """
        cleanup_succeeded = True
        for part_path in target_path.parent.glob(f'{target_path.name}.part*'):
            if not part_path.is_file():
                continue
            try:
                part_path.unlink()
            except OSError:
                self.logger.debug("清理 part 文件失败")
                cleanup_succeeded = False
        return cleanup_succeeded
