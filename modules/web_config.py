#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Web 配置使用的 INI 读取、更新和校验。"""

import configparser
import os

from modules.utils import parse_time_string


GUI_FIELDS = {
    'Network': {'proxy', 'enable_proxy_for_push'},
    'Push': {'retry_interval', 'retry_max_count'},
    'Update': {'auto_check', 'channel'},
    'Logs': {'save_enabled', 'max_files'},
}
BOOLEAN_FIELDS = {
    'Network.enable_proxy_for_push',
    'Update.auto_check',
    'Logs.save_enabled',
}


def read_ini(path):
    """读取 INI 文件并返回节名到键值的嵌套字典。"""
    parser = configparser.ConfigParser(strict=False)
    parser.read(path, encoding='utf-8')
    result = {}
    if parser.defaults():
        result['DEFAULT'] = dict(parser.defaults())
    for section in parser.sections():
        result[section] = dict(parser.items(section, raw=True))
    return result


def _normalise_values(values):
    """将嵌套或扁平配置值统一为节名到键值的映射。"""
    normalised = {}
    for section, section_values in values.items():
        if '.' in section:
            section_name, key = section.split('.', 1)
            normalised.setdefault(section_name, {})[key] = section_values
        elif isinstance(section_values, dict):
            normalised.setdefault(section, {}).update(section_values)
    return normalised


def update_ini(path, values):
    """仅更新 GUI 配置字段，并以同目录临时文件原子替换原文件。"""
    parser = configparser.ConfigParser(strict=False)
    parser.read(path, encoding='utf-8')
    updates = _normalise_values(values)
    for section, section_values in updates.items():
        if section not in GUI_FIELDS:
            continue
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in section_values.items():
            if key in GUI_FIELDS[section]:
                parser.set(section, key, str(value))

    temporary_path = f'{path}.tmp'
    try:
        with open(temporary_path, 'w', encoding='utf-8', newline='') as config_file:
            parser.write(config_file)
        os.replace(temporary_path, path)
    except OSError:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def validate_ini_values(values):
    """校验配置值并返回字段名到具体错误信息的映射。"""
    errors = {}
    normalised = _normalise_values(values)
    for section, section_values in normalised.items():
        for key, value in section_values.items():
            field = f'{section}.{key}'
            if field in BOOLEAN_FIELDS:
                if str(value).strip().lower() not in {'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'}:
                    errors[field] = '必须是布尔值（true 或 false）'
            elif field == 'Push.retry_interval':
                try:
                    if parse_time_string(value) <= 0:
                        raise ValueError
                except (TypeError, ValueError, OverflowError):
                    errors[field] = '必须是大于 0 的时间值，例如 3s、15m 或 1h'
            elif field in {'Push.retry_max_count', 'Logs.max_files'}:
                try:
                    if int(value) <= 0 or str(value).strip() != str(int(value)):
                        raise ValueError
                except (TypeError, ValueError, OverflowError):
                    errors[field] = '必须是大于 0 的整数'
            elif field == 'Update.channel' and str(value).strip().lower() not in {'stable', 'preview'}:
                errors[field] = '必须是 stable 或 preview'
    return errors
