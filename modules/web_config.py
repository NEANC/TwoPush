#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""Web 配置使用的 INI 读取、更新和校验。"""

import configparser
import os
import re
import tempfile
import threading
from pathlib import Path

from modules.config_manager import ConfigManager
from modules.utils import parse_time_string


GUI_FIELDS = {
    section: set(keys)
    for section, keys in ConfigManager(
        '', None, non_interactive=True,
    ).default_sections.items()
    if section != 'Web'
}
BOOLEAN_FIELDS = {
    'Network.enable_proxy_for_push',
    'Update.auto_check',
    'Logs.save_enabled',
}
_UPDATE_LOCKS = {}
_UPDATE_LOCKS_GUARD = threading.Lock()


def _get_update_lock(path):
    """获取规范化配置路径对应的进程内更新锁。"""
    normalised_path = str(Path(path).resolve())
    with _UPDATE_LOCKS_GUARD:
        return _UPDATE_LOCKS.setdefault(normalised_path, threading.RLock())


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
        result[section] = {
            key: value for key, value in parser._sections[section].items()
            if key != '__name__'
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
    lock = _get_update_lock(path)
    with lock:
        _update_ini_locked(path, values)


def _update_ini_locked(path, values):
    """在配置文件锁内执行一次完整的读取、校验和原子更新。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    parser = configparser.ConfigParser(strict=False)
    with open(path, 'r', encoding='utf-8') as config_file:
        parser.read_file(config_file)

    updates = _normalise_values(values)
    validation_updates = {
        section: {
            key: value for key, value in section_values.items()
            if not (section == 'Web' and key == 'access_token')
        }
        for section, section_values in updates.items()
    }
    errors = validate_ini_values(validation_updates)
    if errors:
        raise ValueError('; '.join(f'{field}: {message}' for field, message in errors.items()))
    valid_updates = validation_updates
    if not valid_updates:
        return

    for section, section_values in valid_updates.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in section_values.items():
            parser.set(section, key, str(value))

    temporary_file = None
    temporary_name = None
    try:
        temporary_file = tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='',
            prefix=f'.{os.path.basename(path)}.',
            suffix='.tmp',
            dir=os.path.dirname(os.path.abspath(path)), delete=False,
        )
        temporary_name = temporary_file.name
        with temporary_file:
            parser.write(temporary_file)
        os.replace(temporary_name, path)
    finally:
        if temporary_file is not None:
            close = getattr(temporary_file, 'close', None)
            if close is not None:
                try:
                    close()
                except OSError:
                    pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def validate_ini_values(values):
    """校验配置值并返回字段名到具体错误信息的映射。"""
    errors = {}
    normalised = _normalise_values(values)
    for section, section_values in normalised.items():
        for key, value in section_values.items():
            field = f'{section}.{key}'
            if section not in GUI_FIELDS or key not in GUI_FIELDS[section]:
                errors[field] = '不是允许更新的配置项'
            elif field in BOOLEAN_FIELDS:
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


def parse_ini_content(content):
    """解析并校验 INI 原文，返回校验错误和节键值。"""
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read_string(content)
    except configparser.Error as error:
        raise ValueError(f'INI 格式无效: {error}') from error
    values = {section: dict(parser.items(section, raw=True)) for section in parser.sections()}
    validation_values = {
        section: {
            key: value for key, value in section_values.items()
            if not (section == 'Web' and key == 'access_token')
        }
        for section, section_values in values.items()
    }
    return validate_ini_values(validation_values), values


def protect_access_token(content, original_content):
    """保留原配置中的 Web 访问令牌，同时允许其余 INI 原文更新。"""
    original = configparser.ConfigParser(strict=False)
    original.read_string(original_content)
    token = original.get('Web', 'access_token', fallback=None)
    if token is None:
        return content
    lines = content.splitlines(keepends=True)
    section = None
    replaced = False
    output = []
    for line in lines:
        match = re.match(r'\s*\[([^]]+)\]', line)
        if match:
            section = match.group(1).strip()
        if section == 'Web' and re.match(r'\s*access_token\s*=', line, re.IGNORECASE):
            newline = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
            line = f'access_token = {token}{newline}'
            replaced = True
        output.append(line)
    if not replaced:
        suffix = '' if not output or output[-1].endswith(('\n', '\r')) else '\n'
        output.append(f'{suffix}[Web]\naccess_token = {token}\n')
    return ''.join(output)
