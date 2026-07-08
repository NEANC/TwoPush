#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""TwoPush - 基于 onepush 的通知推送包装程序

通过 INI 管理全局配置，JSON 管理每次推送的内容和通道，
支持命令行调用和自我更新。
"""

import argparse
import json
import logging
import os
import sys

from modules.config_manager import ConfigManager
from modules.logger_manager import (
    add_file_logger,
    cleanup_old_logs,
    raw_read_save_enabled,
    setup_logger,
)
from modules.notification import render_template_vars, send_notification
from modules.utils import parse_push_channels, parse_time_string
from modules.version import VERSION

DEFAULT_CONFIG_FILE = "TwoPush.ini"


def parse_args():
    """解析命令行参数

    Returns:
        argparse.Namespace: 命令行参数命名空间
    """
    parser = argparse.ArgumentParser(
        description='TwoPush - 基于 onepush 的通知推送工具',
        add_help=False,
    )
    parser.add_argument(
        '-h', '-H', '-help', '-Help', '--help', '--Help',
        action='help',
        default=argparse.SUPPRESS,
        help='显示帮助信息并退出',
    )
    parser.add_argument(
        '-c', '-C', '-config', '-Config', '--config', '--Config',
        default=DEFAULT_CONFIG_FILE,
        help='指定配置文件路径，示例 -c C:\\path\\TwoPush.ini',
    )
    parser.add_argument(
        '-p', '-P', '-push', '-Push',
        default=None,
        help='指定推送 JSON 文件路径，示例 -p C:\\path\\report.json',
    )
    parser.add_argument(
        '--version', action='store_true',
        help='打印版本号',
    )
    parser.add_argument(
        '--update', action='store_true',
        help='检查并执行自我更新',
    )
    parser.add_argument(
        '--update-force', action='store_true', dest='update_force',
        help='强制更新到最新版本',
    )

    parser.add_argument('--self-update-verify', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--expected-sha256', type=str, default='', help=argparse.SUPPRESS)
    parser.add_argument('--expected-version', type=str, default='', help=argparse.SUPPRESS)
    parser.add_argument('--retry-update', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--update-failed', action='store_true', help=argparse.SUPPRESS)

    return parser.parse_args()


def load_json_template(json_path, logger):
    """加载并验证 JSON 推送模板

    Args:
        json_path: JSON 文件路径
        logger: 日志记录器

    Returns:
        dict | None: 解析后的模板字典，失败返回 None
    """
    if not os.path.exists(json_path):
        logger.error(f"JSON 文件不存在: {json_path}")
        return None
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            template = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"JSON 文件解析失败: {e}")
        return None

    if not isinstance(template, dict):
        logger.error("JSON 根节点必须是对象")
        return None

    if not template.get('title'):
        logger.error("JSON 模板缺少 title 字段")
        return None
    if not template.get('content'):
        logger.error("JSON 模板缺少 content 字段")
        return None
    if not template.get('channels'):
        logger.error("JSON 模板缺少 channels 字段")
        return None

    return template


def resolve_proxy(json_template, config):
    """解析推送代理设置

    优先级：JSON proxy > INI enable_proxy_for_push + proxy > 不使用

    Args:
        json_template: JSON 模板字典
        config: ConfigManager 实例

    Returns:
        str | None: 代理地址，不使用代理时返回 None
    """
    json_proxy = json_template.get('proxy')
    if json_proxy:
        return json_proxy
    if config.get_attr_bool('enable_proxy_for_push', False):
        return config.get_attr('proxy', '') or None
    return None


def init_self_updater(config, logger):
    """初始化 SelfUpdater 实例

    Args:
        config: ConfigManager 实例
        logger: 日志记录器

    Returns:
        SelfUpdater | None: 非打包环境返回 None
    """
    from modules.self_updater import SelfUpdater
    from modules.self_utils import detect_package_type

    is_bundled, package_type = detect_package_type()
    if not is_bundled:
        logger.debug("源码运行模式，跳过自我更新初始化")
        return None

    return SelfUpdater(
        github_repo=config.get_attr('github_repo'),
        asset_pattern=r'^TwoPush-(Nuitka|PyInstaller)-v[\d.]+.*\.exe$',
        app_name="TwoPush",
        current_version=VERSION,
        proxy=config.get_attr('proxy', ''),
        logger=logger,
        self_update_channel=config.get_attr('channel', 'stable'),
        is_bundled=is_bundled,
        package_type=package_type,
    )


def handle_self_update_verify(args, logger):
    """处理 --self-update-verify（PS1 脚本调用）

    Args:
        args: 命令行参数
        logger: 日志记录器
    """
    from modules.self_updater import SelfUpdater
    exit_code = SelfUpdater.self_update_verify(
        expected_sha256=args.expected_sha256,
        expected_version=args.expected_version,
    )
    sys.exit(exit_code)


def handle_update_failed(logger):
    """处理 --update-failed（PS1 脚本调用）

    Args:
        logger: 日志记录器
    """
    from modules.self_config import UpdateState
    state = UpdateState.load()
    if state:
        failed_ver = state["new_version"]
        logger.critical(f"自更新失败：版本 {failed_ver} 多次验证不通过")
    else:
        logger.critical("自更新失败，但无法读取状态信息")
    sys.exit(1)


def handle_retry_update(config, logger):
    """处理 --retry-update（PS1 脚本回滚后重试）

    Args:
        config: ConfigManager 实例
        logger: 日志记录器
    """
    logger.info("正在重试自更新...")
    updater = init_self_updater(config, logger)
    if updater and updater.check_self_update():
        sys.exit(0)
    logger.error("重试更新失败")
    sys.exit(1)


def handle_update_command(config, logger, force=False):
    """处理 --update / --update-force

    Args:
        config: ConfigManager 实例
        logger: 日志记录器
        force: 是否强制更新
    """
    updater = init_self_updater(config, logger)
    if updater is None:
        logger.warning("当前为源码运行模式，无法执行自我更新")
        sys.exit(0)
    if updater.check_self_update(force=force):
        logger.info("已将新版本下载到临时文件夹，即将退出以完成更新...")
        sys.exit(0)
    logger.info("当前已是最新版本")
    sys.exit(0)


def auto_update_check(config, logger):
    """自动更新检查（若 INI 中 auto_check=true）

    Args:
        config: ConfigManager 实例
        logger: 日志记录器
    """
    if not config.get_attr_bool('auto_check', True):
        return
    updater = init_self_updater(config, logger)
    if updater is None:
        return
    if updater.check_self_update():
        logger.info("检测到新版本，即将退出以完成更新...")
        sys.exit(0)


def execute_push(json_path, config, logger):
    """执行推送操作

    Args:
        json_path: JSON 模板文件路径
        config: ConfigManager 实例
        logger: 日志记录器

    Returns:
        int: 退出码
    """
    template = load_json_template(json_path, logger)
    if template is None:
        return 2

    channels = parse_push_channels(template.get('channels'))
    if not channels:
        logger.error("推送通道解析结果为空，无法发送通知")
        return 2

    retry_settings = {}
    json_retry = template.get('retry')
    if json_retry:
        interval_str = json_retry.get('interval', '3s')
        try:
            retry_settings['interval'] = int(parse_time_string(interval_str))
        except (TypeError, ValueError):
            retry_settings['interval'] = 3
        retry_settings['max_count'] = max(int(json_retry.get('max_count', 3)), 1)
    else:
        interval_str = config.get_attr('retry_interval', '3s')
        try:
            retry_settings['interval'] = int(parse_time_string(interval_str))
        except (TypeError, ValueError):
            retry_settings['interval'] = 3
        retry_settings['max_count'] = config.get_attr_int('retry_max_count', 3)

    vars_ = render_template_vars()
    try:
        title = template['title'].format(**vars_)
        content = template['content'].format(**vars_)
    except KeyError as e:
        logger.error(f"模板变量缺失: {e}")
        return 2

    proxy = resolve_proxy(template, config)
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    if proxy:
        os.environ['HTTP_PROXY'] = proxy
        os.environ['HTTPS_PROXY'] = proxy
        logger.info("已启用推送代理")

    try:
        results = send_notification(
            title=title,
            content=content,
            channels=channels,
            retry_settings=retry_settings,
            logger=logger,
        )
    finally:
        if proxy:
            if old_http_proxy is None:
                os.environ.pop('HTTP_PROXY', None)
            else:
                os.environ['HTTP_PROXY'] = old_http_proxy
            if old_https_proxy is None:
                os.environ.pop('HTTPS_PROXY', None)
            else:
                os.environ['HTTPS_PROXY'] = old_https_proxy

    success_count = sum(1 for _, ok in results if ok)
    fail_count = len(results) - success_count
    logger.info(f"推送完成: {success_count} 成功, {fail_count} 失败")
    return 0 if fail_count == 0 else 1


def main():
    """主入口"""
    args = parse_args()

    if args.version:
        print(f"TwoPush {VERSION}")
        sys.exit(0)

    save_enabled = raw_read_save_enabled(args.config)
    logger = setup_logger("TwoPush")
    if save_enabled:
        add_file_logger(logger, version=VERSION, log_dir='logs', log_prefix='TwoPush')

    if args.self_update_verify:
        handle_self_update_verify(args, logger)

    if args.update_failed:
        handle_update_failed(logger)

    config = ConfigManager(
        config_file=args.config,
        logger=logger,
        app_name="TwoPush",
    )
    config.load()
    if not config.validate():
        sys.exit(2)

    if save_enabled:
        max_files = int(config.get_attr('max_files', '15'))
        if max_files > 0:
            cleanup_old_logs(logger, max_files, log_dir='logs', log_prefix='TwoPush')

    from modules.self_updater import SelfUpdater
    SelfUpdater._cleanup_update_residue(logger)

    if args.retry_update:
        handle_retry_update(config, logger)

    if args.update or args.update_force:
        handle_update_command(config, logger, force=args.update_force)

    auto_update_check(config, logger)

    if args.p:
        exit_code = execute_push(args.p, config, logger)
        sys.exit(exit_code)

    print("TwoPush - 基于 onepush 的通知推送工具")
    print(f"版本: {VERSION}")
    print("使用 -h 查看帮助")
    sys.exit(0)


if __name__ == '__main__':
    main()
