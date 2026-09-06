#!/usr/bin/env python3
# -_- coding: utf-8 -_- 

"""Web 配置首次启动行为测试"""

import configparser
import logging
import os
import threading

from modules.config_manager import ConfigManager


def test_web_default_config_contains_access_token():
    """默认配置应包含 Web 访问令牌配置项"""
    manager = ConfigManager(
        'config.ini',
        logging.getLogger('test_web_default_config_contains_access_token'),
        default_sections={'Web': {}},
    )

    config = configparser.ConfigParser()
    config.read_string(manager._build_default_config())

    assert config.has_option('Web', 'access_token')
    assert config.get('Web', 'access_token') == ''


def test_web_missing_config_initializes_without_system_exit(tmp_path):
    """Web 非交互模式缺少配置文件时应生成配置并继续加载"""
    config_file = tmp_path / 'config.ini'
    manager = ConfigManager(
        str(config_file),
        logging.getLogger('test_web_missing_config_initializes_without_system_exit'),
        default_sections={'Web': {}},
        non_interactive=True,
    )

    manager.load()

    assert config_file.exists()
    assert manager.get_attr('access_token') == ''


def test_web_regeneration_preserves_unknown_default_keys(tmp_path):
    """配置重建时应保留 DEFAULT 中的未知键"""
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[DEFAULT]\ncustom_setting = keep-me\n\n[Web]\naccess_token = valid-token\n',
        encoding='utf-8',
    )
    manager = ConfigManager(
        str(config_file),
        logging.getLogger('test_web_regeneration_preserves_unknown_default_keys'),
        default_sections={'Web': {'new_setting': 'default'}},
        non_interactive=True,
    )

    manager.load()

    config = configparser.ConfigParser()
    config.read(config_file, encoding='utf-8')
    assert config['DEFAULT']['custom_setting'] == 'keep-me'


def test_web_regeneration_preserves_existing_short_access_token(tmp_path):
    """配置重建时应原样保留已有短访问令牌"""
    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[Web]\naccess_token = short\n',
        encoding='utf-8',
    )
    manager = ConfigManager(
        str(config_file),
        logging.getLogger('test_web_regeneration_preserves_existing_short_access_token'),
        default_sections={'Web': {'new_setting': 'default'}},
        non_interactive=True,
    )

    manager.load()

    config = configparser.ConfigParser()
    config.read(config_file, encoding='utf-8')
    assert config.get('Web', 'access_token') == 'short'


def test_cli_missing_config_still_exits_after_generation(tmp_path):
    """CLI 默认模式缺少配置文件时仍应保持首次运行退出行为"""
    manager = ConfigManager(
        str(tmp_path / 'config.ini'),
        logging.getLogger('test_cli_missing_config_still_exits_after_generation'),
    )

    try:
        manager.load()
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError('CLI 首次运行应退出')


def test_read_ini_and_update_ini_preserve_unmanaged_content(tmp_path):
    """更新 GUI 配置时应保留 Web 令牌、未知节和未知键"""
    from modules.web_config import read_ini, update_ini

    config_file = tmp_path / 'config.ini'
    original = (
        '[Network]\nproxy = old\nunknown_network = keep\n\n'
        '[Web]\naccess_token = secret-token\nweb_unknown = keep-web\n\n'
        '[Custom]\ncustom_key = custom-value\n'
    )
    config_file.write_text(original, encoding='utf-8')

    values = {
        'Network': {'proxy': 'new'},
        'Push': {'retry_interval': '5s'},
        'Web': {'access_token': 'changed'},
    }
    loaded = read_ini(str(config_file))
    update_ini(str(config_file), values)

    assert loaded['Web']['access_token'] == 'secret-token'
    content = config_file.read_text(encoding='utf-8')
    assert 'proxy = new' in content
    assert 'retry_interval = 5s' in content
    assert 'access_token = secret-token' in content
    assert 'web_unknown = keep-web' in content
    assert 'unknown_network = keep' in content
    assert '[Custom]' in content
    assert 'custom_key = custom-value' in content


def test_validate_ini_values_reports_specific_errors():
    """配置校验应返回各字段的具体错误"""
    from modules.web_config import validate_ini_values

    errors = validate_ini_values({
        'Push': {'retry_interval': 'bad', 'retry_max_count': '0'},
        'Logs': {'max_files': '-1', 'save_enabled': 'maybe'},
        'Update': {'auto_check': 'sometimes', 'channel': 'nightly'},
    })

    assert 'Push.retry_interval' in errors
    assert 'Push.retry_max_count' in errors
    assert 'Logs.max_files' in errors
    assert 'Logs.save_enabled' in errors
    assert 'Update.auto_check' in errors
    assert 'Update.channel' in errors


def test_update_ini_cleans_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    """原子替换失败时应清理临时文件"""
    from modules.web_config import update_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[Web]\naccess_token = keep\n', encoding='utf-8')
    monkeypatch.setattr('modules.web_config.os.replace', lambda *_: (_ for _ in ()).throw(OSError('fail')))

    try:
        update_ini(str(config_file), {'Network': {'proxy': 'new'}})
    except OSError:
        pass
    else:
        raise AssertionError('原子替换失败应抛出 OSError')

    assert not (tmp_path / 'config.ini.tmp').exists()
    assert config_file.read_text(encoding='utf-8') == '[Web]\naccess_token = keep\n'


def test_validate_ini_values_accepts_valid_values():
    """合法配置值应通过校验"""
    from modules.web_config import validate_ini_values

    assert validate_ini_values({
        'Push': {'retry_interval': '1h', 'retry_max_count': '3'},
        'Logs': {'max_files': '15', 'save_enabled': 'true'},
        'Update': {'auto_check': 'false', 'channel': 'preview'},
    }) == {}


def test_update_ini_accepts_flat_gui_values(tmp_path):
    """更新接口应支持以节名和键名组成的扁平字段映射"""
    from modules.web_config import update_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[Network]\nproxy = old\n', encoding='utf-8')
    update_ini(str(config_file), {'Network.proxy': 'new'})

    assert 'proxy = new' in config_file.read_text(encoding='utf-8')




def test_read_ini_does_not_duplicate_default_keys_into_sections(tmp_path):
    """读取配置时节字典不应包含继承自 DEFAULT 的键"""
    from modules.web_config import read_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[DEFAULT]\nshared = default\n\n[Network]\nproxy = local\n', encoding='utf-8')

    loaded = read_ini(str(config_file))

    assert loaded['DEFAULT'] == {'shared': 'default'}
    assert loaded['Network'] == {'proxy': 'local'}


def test_read_ini_preserves_explicit_section_override_of_default(tmp_path):
    """读取配置时应保留普通节对 DEFAULT 同名键的显式覆盖"""
    from modules.web_config import read_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text(
        '[DEFAULT]\nshared = default\n\n[Network]\nshared = local\n',
        encoding='utf-8',
    )

    loaded = read_ini(str(config_file))

    assert loaded['DEFAULT'] == {'shared': 'default'}
    assert loaded['Network'] == {'shared': 'local'}


def test_update_ini_cleans_actual_temporary_path_when_write_fails(tmp_path, monkeypatch):
    """临时文件写入异常时应清理实际创建的临时路径"""
    from modules.web_config import update_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[Network]\nproxy = old\n', encoding='utf-8')
    temporary_paths = []
    real_temporary_file = __import__('tempfile').NamedTemporaryFile

    def create_temporary_file(*args, **kwargs):
        temporary_file = real_temporary_file(*args, **kwargs)
        temporary_paths.append(temporary_file.name)
        return temporary_file

    monkeypatch.setattr('modules.web_config.tempfile.NamedTemporaryFile', create_temporary_file)
    monkeypatch.setattr('modules.web_config.configparser.ConfigParser.write',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('write fail')))

    try:
        update_ini(str(config_file), {'Network': {'proxy': 'new'}})
    except OSError:
        pass
    else:
        raise AssertionError('写入失败应抛出 OSError')

    assert temporary_paths
    assert not os.path.exists(temporary_paths[0])


def test_update_ini_cleans_temporary_file_when_context_setup_fails(tmp_path, monkeypatch):
    """临时文件创建后进入上下文异常时应清理实际路径"""
    from modules.web_config import update_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[Network]\nproxy = old\n', encoding='utf-8')
    real_temporary_file = __import__('tempfile').NamedTemporaryFile
    temporary_paths = []

    class FailingContext:
        """创建真实临时文件但在进入上下文时失败"""

        def __init__(self, temporary_file):
            self.temporary_file = temporary_file
            self.name = temporary_file.name

        def close(self):
            """关闭底层临时文件"""
            self.temporary_file.close()

        def __enter__(self):
            raise OSError('enter fail')

        def __exit__(self, *_args):
            self.temporary_file.close()

    def create_failing_context(*args, **kwargs):
        temporary_file = real_temporary_file(*args, **kwargs)
        temporary_paths.append(temporary_file.name)
        return FailingContext(temporary_file)

    monkeypatch.setattr(
        'modules.web_config.tempfile.NamedTemporaryFile',
        create_failing_context,
    )

    try:
        update_ini(str(config_file), {'Network': {'proxy': 'new'}})
    except OSError:
        pass
    else:
        raise AssertionError('进入临时文件上下文失败应抛出 OSError')

    assert temporary_paths
    assert not os.path.exists(temporary_paths[0])


def test_update_ini_passes_actual_paths_to_atomic_replace(tmp_path, monkeypatch):
    from modules.web_config import update_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[Network]\nproxy = old\n', encoding='utf-8')
    replace_calls = []
    monkeypatch.setattr('modules.web_config.os.replace', lambda *args: replace_calls.append(args))

    update_ini(str(config_file), {'Network': {'proxy': 'new'}})

    assert replace_calls
    temporary_path, target_path = replace_calls[0]
    assert os.path.dirname(temporary_path) == str(tmp_path)
    assert target_path == str(config_file)


def test_update_ini_serializes_concurrent_updates(tmp_path):
    """同一配置文件的并发更新不应丢失彼此的修改"""
    from modules.web_config import update_ini, read_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[Network]\nproxy = old\n\n[Push]\nretry_interval = 3s\n', encoding='utf-8')
    barrier = threading.Barrier(2)
    errors = []

    def update(values):
        try:
            barrier.wait()
            update_ini(str(config_file), values)
        except Exception as error:
            errors.append(error)

    first = threading.Thread(target=update, args=({'Network': {'proxy': 'new'}},))
    second = threading.Thread(target=update, args=({'Push': {'retry_interval': '5s'}},))
    first.start()
    second.start()
    first.join()
    second.join()

    assert errors == []
    loaded = read_ini(str(config_file))
    assert loaded['Network']['proxy'] == 'new'
    assert loaded['Push']['retry_interval'] == '5s'


def test_update_ini_rejects_invalid_values_before_writing(tmp_path):
    """更新前校验失败时不应改写配置文件"""
    from modules.web_config import update_ini

    config_file = tmp_path / 'config.ini'
    original = '[Push]\nretry_interval = 3s\n'
    config_file.write_text(original, encoding='utf-8')

    try:
        update_ini(str(config_file), {'Push': {'retry_interval': '-5s'}})
    except ValueError as error:
        assert 'Push.retry_interval' in str(error)
    else:
        raise AssertionError('非法配置值应抛出 ValueError')

    assert config_file.read_text(encoding='utf-8') == original


def test_update_ini_missing_or_malformed_file_has_explicit_error(tmp_path):
    """更新不存在或解析失败的文件时应抛出明确异常"""
    from modules.web_config import update_ini

    missing = tmp_path / 'missing.ini'
    try:
        update_ini(str(missing), {'Network': {'proxy': 'new'}})
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('缺失配置文件应抛出 FileNotFoundError')

    malformed = tmp_path / 'malformed.ini'
    malformed.write_text('[Network\nproxy = old\n', encoding='utf-8')
    try:
        update_ini(str(malformed), {'Network': {'proxy': 'new'}})
    except configparser.Error:
        pass
    else:
        raise AssertionError('解析失败应抛出 configparser.Error')


def test_update_ini_rejects_unknown_sections_and_keys(tmp_path):
    """更新配置时应拒绝未知节和键，而不是静默忽略。"""
    from modules.web_config import update_ini

    config_file = tmp_path / 'config.ini'
    config_file.write_text('[Network]\nproxy = old\n', encoding='utf-8')

    for values, expected in [
        ({'Unknown': {'key': 'value'}}, 'Unknown'),
        ({'Network': {'unknown': 'value'}}, 'Network.unknown'),
    ]:
        try:
            update_ini(str(config_file), values)
        except ValueError as error:
            assert expected in str(error)
        else:
            raise AssertionError('未知配置项应抛出 ValueError')


def test_validate_ini_values_reports_unknown_fields():
    """配置校验应为未知节键返回字段级错误。"""
    from modules.web_config import validate_ini_values

    errors = validate_ini_values({'Unknown': {'key': 'value'}, 'Network': {'unknown': 'value'}})

    assert errors['Unknown.key'] == '不是允许更新的配置项'
    assert errors['Network.unknown'] == '不是允许更新的配置项'
