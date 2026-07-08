#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""TwoPush 主流程与通道解析测试"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import TwoPush
from modules.utils import parse_push_channels


def test_parse_args_uses_config_ini_by_default(monkeypatch):
    """未指定配置路径时应默认使用 config.ini"""
    monkeypatch.setattr(sys, 'argv', ['TwoPush.py'])

    args = TwoPush.parse_args()

    assert args.config == 'config.ini'


class FakeConfig:
    """用于测试代理解析的配置对象"""

    def __init__(self, enable_proxy=False, proxy=''):
        self.enable_proxy = enable_proxy
        self.proxy = proxy

    def get_attr_bool(self, key, default=False):
        """返回布尔配置值"""
        if key == 'enable_proxy_for_push':
            return self.enable_proxy
        return default

    def get_attr(self, key, default=''):
        """返回字符串配置值"""
        if key == 'proxy':
            return self.proxy
        if key == 'retry_interval':
            return '3s'
        return default

    def get_attr_int(self, key, default=0):
        """返回整数配置值"""
        if key == 'retry_max_count':
            return 3
        return default


def test_parse_push_channels_aliases_serverchan_key():
    """serverchan 的 key 别名应自动转换为 sckey"""
    channels = parse_push_channels([
        {'provider': 'serverchan', 'key': 'SCTxxxx'},
    ])
    assert channels == [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}]


def test_resolve_proxy_json_overrides_ini():
    """JSON proxy 应优先于 INI 代理控制"""
    config = FakeConfig(enable_proxy=False, proxy='http://ini.proxy')
    assert TwoPush.resolve_proxy({'proxy': 'http://json.proxy'}, config) == 'http://json.proxy'


def test_resolve_proxy_ini_requires_enable_flag():
    """INI 代理只有 enable_proxy_for_push=true 时才用于推送"""
    disabled = FakeConfig(enable_proxy=False, proxy='http://ini.proxy')
    enabled = FakeConfig(enable_proxy=True, proxy='http://ini.proxy')
    assert TwoPush.resolve_proxy({}, disabled) is None
    assert TwoPush.resolve_proxy({}, enabled) == 'http://ini.proxy'


def test_execute_push_restores_proxy_environment(monkeypatch):
    """执行推送后应恢复原 HTTP_PROXY/HTTPS_PROXY 环境变量"""
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    os.environ['HTTP_PROXY'] = 'http://old-http.proxy'
    os.environ['HTTPS_PROXY'] = 'http://old-https.proxy'

    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {host_name}',
        'content': '内容 {current_time}',
        'proxy': 'http://secret:token@new.proxy',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })
    monkeypatch.setattr(TwoPush, 'send_notification', lambda **kwargs: [('serverchan', True)])
    logger = logging.getLogger('test_execute_push_restores_proxy_environment')

    try:
        assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 0
        assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
        assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
    finally:
        if old_http_proxy is None:
            os.environ.pop('HTTP_PROXY', None)
        else:
            os.environ['HTTP_PROXY'] = old_http_proxy
        if old_https_proxy is None:
            os.environ.pop('HTTPS_PROXY', None)
        else:
            os.environ['HTTPS_PROXY'] = old_https_proxy


def test_execute_push_does_not_set_proxy_when_template_invalid(monkeypatch):
    """模板变量错误时不应设置新的推送代理环境变量"""
    old_http_proxy = os.environ.get('HTTP_PROXY')
    old_https_proxy = os.environ.get('HTTPS_PROXY')
    os.environ['HTTP_PROXY'] = 'http://old-http.proxy'
    os.environ['HTTPS_PROXY'] = 'http://old-https.proxy'

    monkeypatch.setattr(TwoPush, 'load_json_template', lambda path, logger: {
        'title': '标题 {missing_var}',
        'content': '内容 {current_time}',
        'proxy': 'http://secret:token@new.proxy',
        'channels': [{'provider': 'serverchan', 'sckey': 'SCTxxxx'}],
    })
    logger = logging.getLogger('test_execute_push_does_not_set_proxy_when_template_invalid')

    try:
        assert TwoPush.execute_push('unused.json', FakeConfig(), logger) == 2
        assert os.environ.get('HTTP_PROXY') == 'http://old-http.proxy'
        assert os.environ.get('HTTPS_PROXY') == 'http://old-https.proxy'
    finally:
        if old_http_proxy is None:
            os.environ.pop('HTTP_PROXY', None)
        else:
            os.environ['HTTP_PROXY'] = old_http_proxy
        if old_https_proxy is None:
            os.environ.pop('HTTPS_PROXY', None)
        else:
            os.environ['HTTPS_PROXY'] = old_https_proxy
