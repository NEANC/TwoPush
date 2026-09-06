#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Web 配置使用的 INI 读取、更新和校验。"""

import configparser
import os
import tempfile

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
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    parser = configparser.ConfigParser(strict=False)
    with open(path, 'r', encoding='utf-8') as config_file:
        parser.read_file(config_file)
    result = {}
    if parser.defaults():
        result['DEFAULT'] = dict(parser.defaults())
    for section in parser.sections():
        inherited_keys = set(parser.defaults())
        result[section] = {
            key: value for key, value in parser.items(section, raw=True)
            if key not in inherited_keys
        }
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
    """校验并更新 GUI 配置字段，以同目录唯一临时文件原子替换原文件。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    parser = configparser.ConfigParser(strict=False)
    with open(path, 'r', encoding='utf-8') as config_file:
        parser.read_file(config_file)

    updates = _normalise_values(values)
    valid_updates = {
        section: {
            key: value for key, value in section_values.items()
            if section in GUI_FIELDS and key in GUI_FIELDS[section]
            and not (section == 'Web' and key == 'access_token')
        }
        for section, section_values in updates.items()
    }
    valid_updates = {section: data for section, data in valid_updates.items() if data}
    if not valid_updates:
        return

    errors = validate_ini_values(valid_updates)
    if errors:
        raise ValueError('; '.join(f'{field}: {message}' for field, message in errors.items()))

    for section, section_values in valid_updates.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in section_values.items():
            parser.set(section, key, str(value))

    temporary_path = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', newline='', prefix=f'.{os.path.basename(path)}.',
        suffix='.tmp', dir=os.path.dirname(os.path.abspath(path)), delete=False,
    )
    temporary_name = temporary_path.name
    try:
        with temporary_path:
            parser.write(temporary_path)
        os.replace(temporary_name, path)
    except OSError:
        try:
            os.unlink(temporary_name)
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
                    if ((isinstance(value, (int, float)) and value < 0)
                            or (isinstance(value, str) and value.strip().startswith('-'))):
                        raise ValueError
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
