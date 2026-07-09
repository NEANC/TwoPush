#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""JSON 模板构建、读写与命令处理"""

import json
import os
import sys

DEFAULT_TEMPLATE_FILE = "TwoPush.templates.json"


def build_default_json_template():
    """构建默认 JSON 推送模板

    Returns:
        dict: README 示例对应的 JSON 模板数据
    """
    return {
        'title': '每日报告 - {host_name}',
        'content': '截止 {current_time}，系统运行正常',
        'proxy': 'http://127.0.0.1:7890',
        'retry': {
            'interval': '5s',
            'max_count': 2,
        },
        'channels': [
            {'provider': 'serverchan', 'sckey': 'SCTxxxx'},
            {'provider': 'qmsg', 'key': 'xxx', 'qq': 'xxx'},
            {'provider': 'dingtalk', 'token': 'xxx', 'secret': 'xxx'},
            {'provider': 'lark', 'webhook': 'xxx', 'sign': 'xxx'},
            {
                'provider': 'smtp',
                'host': 'xxx',
                'user': 'xxx',
                'password': 'xxx',
                'port': 587,
                'ssl': True,
            },
        ],
    }


def write_json_template_file(path, logger, force=False, verbose_path=True):
    """写入 JSON 模板文件

    Args:
        path: 模板文件路径
        logger: 日志记录器
        force: 文件存在时是否覆盖
        verbose_path: 成功日志显示完整路径还是仅文件名

    Returns:
        bool: 写入成功返回 True，否则返回 False
    """
    if os.path.exists(path) and not force:
        logger.critical(f"JSON 模板文件已存在，请使用 --template-force 覆盖: {path}")
        return False

    tmp_path = f'{path}.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(build_default_json_template(), f, ensure_ascii=False, indent=4)
            f.write('\n')
        os.replace(tmp_path, path)
    except OSError as e:
        logger.critical(f"生成 JSON 模板文件失败 (路径: {path}): {e}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False

    display_path = path if verbose_path else os.path.basename(path)
    logger.info(f"已生成 JSON 模板文件: {display_path}")
    return True


def resolve_default_template_path():
    """解析程序目录下的默认 JSON 模板路径

    Returns:
        str: 默认模板文件路径
    """
    return os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), DEFAULT_TEMPLATE_FILE)


def resolve_template_command(args):
    """解析模板命令目标路径和覆盖策略

    注意：当用户显式指定与 DEFAULT_TEMPLATE_FILE 同名的路径时（如
    `-T TwoPush.templates.json`），无法区分这是 const 默认值还是用户
    显式传入，路径均解析到脚本所在目录。

    Args:
        args: 命令行参数

    Returns:
        tuple[str | None, bool]: 目标路径和是否覆盖；未触发模板命令时路径为 None
    """
    if args.template_force:
        path = args.template_force
        if path == DEFAULT_TEMPLATE_FILE:
            path = resolve_default_template_path()
        return path, True
    if args.template:
        path = args.template
        if path == DEFAULT_TEMPLATE_FILE:
            path = resolve_default_template_path()
        return path, False
    return None, False


def handle_template_command(args, logger):
    """处理 JSON 模板生成命令

    Args:
        args: 命令行参数
        logger: 日志记录器
    """
    template_path, force = resolve_template_command(args)
    if not template_path:
        return
    if write_json_template_file(template_path, logger, force=force):
        sys.exit(0)
    sys.exit(1)


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


def ensure_default_template_on_first_run(logger):
    """首次生成默认配置时同步生成 JSON 模板

    Args:
        logger: 日志记录器
    """
    template_path = resolve_default_template_path()
    if os.path.exists(template_path):
        logger.info(f"JSON 模板文件已存在，跳过生成: {template_path}")
        return
    write_json_template_file(template_path, logger, force=False, verbose_path=False)
