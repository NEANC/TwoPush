#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""TwoPush - 基于 onepush 的通知推送包装程序

通过 INI 管理全局配置，JSON 管理每次推送的内容和通道，
支持命令行调用和自我更新。
"""

import argparse
import os
import sys

from contextlib import contextmanager

from modules.config_manager import ConfigManager
from modules.logger_manager import (
    add_file_logger,
    cleanup_old_logs,
    raw_read_save_enabled,
    setup_logger,
)
from modules.notification import render_template_vars, send_notification
from modules.utils import parse_push_channels, parse_time_string
from modules.json_manager import (
    DEFAULT_TEMPLATE_FILE,
    ensure_default_template_on_first_run,
    handle_template_command,
    load_json_template,
)
from modules.version import VERSION

DEFAULT_CONFIG_FILE = "config.ini"


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
        '-h', '-H', '--help', '--Help',
        action='help',
        default=argparse.SUPPRESS,
        help='显示帮助信息',
    )
    parser.add_argument(
        '-v', '--version', action='store_true',
        help='显示版本号',
    )
    parser.add_argument(
        '-c', '-C', '--config', '--Config',
        default=DEFAULT_CONFIG_FILE,
        help='指定配置文件路径，示例 -c C:\\path\\config.ini',
    )
    parser.add_argument(
        '-p', '-P', '--push', '--Push',
        default=None,
        help='指定推送 JSON 文件路径，示例 -p C:\\path\\report.json',
    )
    parser.add_argument(
        '--update', '--Update', action='store_true', dest='update',
        help='检查并执行自我更新',
    )
    parser.add_argument(
        '--update-force', '--UpdateForce', action='store_true', dest='update_force',
        help='强制更新到最新版本',
    )
    parser.add_argument(
        '-S', '--silent', '--Silent',
        action='store_true',
        dest='silent',
        help='静默模式，不输出控制台日志',
    )
    parser.add_argument(
        '-T', '--template', '--Template',
        nargs='?',
        const=DEFAULT_TEMPLATE_FILE,
        default=None,
        dest='template',
        help='生成 JSON 模板文件，未指定路径时生成 TwoPush.templates.json，示例 -T C:\\path\\template.json',
    )
    parser.add_argument(
        '--template-force', '--Template-Force',
        nargs='?',
        const=DEFAULT_TEMPLATE_FILE,
        default=None,
        dest='template_force',
        help='生成 JSON 模板文件并允许覆盖已有文件，示例 --Template-Force C:\\path\\template.json',
    )
    # 自更新相关参数
    parser.add_argument('--self-update-verify', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--expected-sha256', type=str, default='', help=argparse.SUPPRESS)
    parser.add_argument('--expected-version', type=str, default='', help=argparse.SUPPRESS)
    parser.add_argument('--retry-update', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--update-failed', action='store_true', help=argparse.SUPPRESS)
    # 用于文件拖放
    parser.add_argument('jsonfile',nargs='?',default=None,help=argparse.SUPPRESS)

    return parser.parse_args()


def is_config_path_explicit(argv):
    """判断命令行是否显式指定了配置文件路径

    Args:
        argv: 命令行参数列表，通常为 sys.argv

    Returns:
        bool: 显式传入配置文件参数时返回 True
    """
    config_flags = {'-c', '-C', '--config', '--Config'}
    short_config_flags = {'-c', '-C'}
    for arg in argv[1:]:
        if arg in config_flags:
            return True
        if any(arg.startswith(f'{flag}=') for flag in config_flags):
            return True
        if any(arg.startswith(flag) and arg != flag for flag in short_config_flags):
            return True
    return False


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


@contextmanager
def push_proxy_environment(proxy, logger):
    """临时设置推送代理环境变量，仅适合同进程串行推送

    支持 HTTP/HTTPS/SOCKS5 代理协议。同时设置 HTTP_PROXY、HTTPS_PROXY
    和 ALL_PROXY，确保 DNS 解析也走代理隧道，避免 DNS 泄漏。

    Args:
        proxy: HTTP/HTTPS/SOCKS5 代理服务器地址，为空时不修改环境变量
        logger: 日志记录器
    """
    if not proxy:
        yield
        return

    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    old_all_proxy = os.environ.get('ALL_PROXY')
    os.environ['HTTP_PROXY'] = proxy
    os.environ['HTTPS_PROXY'] = proxy
    os.environ['ALL_PROXY'] = proxy
    logger.info("已启用推送代理")
    try:
        yield
    finally:
        if old_http_proxy is None:
            os.environ.pop('HTTP_PROXY', None)
        else:
            os.environ['HTTP_PROXY'] = old_http_proxy
        if old_https_proxy is None:
            os.environ.pop('HTTPS_PROXY', None)
        else:
            os.environ['HTTPS_PROXY'] = old_https_proxy
        if old_all_proxy is None:
            os.environ.pop('ALL_PROXY', None)
        else:
            os.environ['ALL_PROXY'] = old_all_proxy


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
        logger.debug("源码运行，跳过自我更新")
        return None

    return SelfUpdater(
        github_repo='NEANC/TwoPush',
        asset_pattern=r'^TwoPush-(Nuitka|PyInstaller)-v[\d.]+.*\.exe$',
        app_name="TwoPush",
        current_version=VERSION,
        proxy=config.get_attr('proxy', ''),
        logger=logger,
        self_update_channel=config.get_attr('channel', 'stable'),
        is_bundled=is_bundled,
        package_type=package_type,
    )


def handle_self_update_verify(args):
    """处理 --self-update-verify（PS1 脚本调用）

    Args:
        args: 命令行参数
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
        try:
            retry_settings['max_count'] = max(int(json_retry.get('max_count', 3)), 1)
        except (TypeError, ValueError):
            retry_settings['max_count'] = 3
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
    with push_proxy_environment(proxy, logger):
        results = send_notification(
            title=title,
            content=content,
            channels=channels,
            retry_settings=retry_settings,
            logger=logger,
        )

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

    if not args.silent:
        print("TwoPush - 基于 onepush 的通知推送工具")
        print(f"版本: {VERSION}")

    save_enabled = raw_read_save_enabled(args.config)
    logger = setup_logger(console_enabled=not args.silent)
    if save_enabled:
        add_file_logger(logger, version=VERSION, log_dir='logs', log_prefix='TwoPush')

    if args.self_update_verify:
        handle_self_update_verify(args)

    if args.update_failed:
        handle_update_failed(logger)

    handle_template_command(args, logger)

    if is_config_path_explicit(sys.argv) and not os.path.exists(args.config):
        logger.critical(f"指定的配置文件不存在: {args.config}")
        sys.exit(1)

    config = ConfigManager(
        config_file=args.config,
        logger=logger,
        app_name="TwoPush",
        first_run_callback=lambda: ensure_default_template_on_first_run(logger),
    )
    config.load()
    if not config.validate():
        sys.exit(2)

    if save_enabled:
        max_files = int(config.get_attr('max_files', '15'))
        if max_files > 0:
            cleanup_old_logs(logger, max_files, log_dir='logs', log_prefix='TwoPush')

    push_file = args.push or args.jsonfile
    is_drag_drop = bool(args.jsonfile and not args.push)
    push_exit_code = None

    if push_file:
        if not os.path.exists(push_file):
            logger.critical(f"指定的 JSON 推送文件不存在: {push_file}")
            if is_drag_drop:
                input("按任意键退出...")
            sys.exit(1)
        push_exit_code = execute_push(push_file, config, logger)
        if is_drag_drop:
            print(f"\n推送完成，退出码: {push_exit_code}")
            input("按任意键退出...")

    # 清理更新残留，无论是否 -p 模式都执行
    from modules.self_updater import SelfUpdater
    SelfUpdater._cleanup_update_residue(logger)

    # push 失败时提前退出，避免退出码被更新命令覆盖
    if push_exit_code is not None and push_exit_code != 0:
        sys.exit(push_exit_code)

    if args.retry_update:
        handle_retry_update(config, logger)

    if args.update or args.update_force:
        handle_update_command(config, logger, force=args.update_force)

    # 自动更新检查仅在非 -p 模式下执行（--update/--update-force 始终生效）
    if not args.push:
        auto_update_check(config, logger)

    if push_exit_code is not None:
        sys.exit(push_exit_code)
    sys.exit(0)


if __name__ == '__main__':
    main()
