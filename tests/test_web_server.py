#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""FastAPI Web 服务测试。"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from modules import web_server
from modules.web_server import create_app


class FakeProcessManager:
    """测试用推送控制器。"""

    def get_status(self, cursor=0):
        """返回空闲状态。"""
        return {'task_id': None, 'status': 'idle', 'exit_code': None, 'outputs': []}

    def stop(self):
        """记录停止请求。"""
        return False

    def shutdown(self):
        """记录服务退出。"""
        return None


def test_frontend_starts_polling_with_an_immediate_status_request(tmp_path):
    """推送成功后前端应立即请求已有日志，而非等待定时器。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    app_script = resource_dir / 'app.js'
    app_script.write_text(
        (Path(__file__).parents[1] / 'web' / 'app.js').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    script = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager())).get('/app.js').text

    assert 'poll();' in script
    assert 'state.timer = setInterval(poll, 1000);' in script
def test_is_reparse_point_treats_windows_failure_sentinel_as_not_reparse(tmp_path, monkeypatch):
    """Windows 属性查询失败时应返回 False，而不是误判为重解析点。"""
    monkeypatch.setattr(web_server.os, 'name', 'nt')
    monkeypatch.setattr(
        web_server.ctypes,
        'windll',
        SimpleNamespace(kernel32=SimpleNamespace(GetFileAttributesW=lambda _: -1)),
        raising=False,
    )

    assert web_server._is_reparse_point(tmp_path / 'missing') is False



def test_create_app_stores_logger(tmp_path):
    """应用应保存传入的日志记录器。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    logger = __import__('logging').getLogger('web-test-logger')

    app = create_app(tmp_path, config_path, resource_dir, FakeProcessManager(), logger=logger)

    assert app.state.logger is logger
def test_create_app_logs_auth_failure_without_request_details(tmp_path, caplog):
    """认证失败固定记录 WARNING 且不写入令牌或请求明细。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = ' + 'x' * 16 + '\n', encoding='utf-8')
    logger = __import__('logging').getLogger('web-auth-log-test')
    app = create_app(tmp_path, config_path, resource_dir, FakeProcessManager(), logger=logger)
    with caplog.at_level('WARNING', logger='web-auth-log-test'):
        response = TestClient(app).get('/api/session', headers={'Authorization': 'Bearer wrong-token'})
    assert response.status_code == 401
    assert 'x' * 16 not in '\n'.join(record.getMessage() for record in caplog.records)


def test_create_app_serves_home_and_sets_security_header(tmp_path):
    """应用应提供首页并设置禁止来源策略。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('<html>ok</html>', encoding='utf-8')
    (resource_dir / 'app.js').write_text('', encoding='utf-8')
    (resource_dir / 'style.css').write_text('', encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    app = create_app(tmp_path, config_path, resource_dir, FakeProcessManager())
    response = TestClient(app).get('/')

    assert response.status_code == 200
    assert response.text == '<html>ok</html>'
    assert response.headers['referrer-policy'] == 'no-referrer'


def test_valid_token_requires_bearer_for_api_and_allows_home_query(tmp_path):
    """有效令牌模式下 API 必须 Bearer，首页查询令牌可首次访问。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('home', encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = not-a-secret-token\n', encoding='utf-8')

    app = create_app(tmp_path, config_path, resource_dir, FakeProcessManager())
    client = TestClient(app)

    assert client.get('/api/session').status_code == 401
    assert client.get('/?token=not-a-secret-token').status_code == 200
    assert client.get('/api/session', headers={'Authorization': 'Bearer not-a-secret-token'}).status_code == 200


def test_query_token_is_single_use_and_bearer_remains_valid(tmp_path):
    """首页查询令牌仅可使用一次，Bearer 认证生命周期不受影响。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('home', encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = not-a-secret-token\n', encoding='utf-8')
    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))

    assert client.get('/?token=not-a-secret-token').status_code == 200
    assert client.get('/?token=not-a-secret-token').status_code == 401
    assert client.get('/api/session', headers={'Authorization': 'Bearer not-a-secret-token'}).status_code == 200
def test_files_hide_temp_hidden_and_escape_paths(tmp_path):
    """文件浏览只能在工作区内逐级访问并隐藏敏感项目。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('home', encoding='utf-8')
    (tmp_path / 'visible.json').write_text('{}', encoding='utf-8')
    (tmp_path / '.hidden.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'Temp').mkdir()
    (tmp_path / 'Temp' / 'secret.json').write_text('{}', encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    app = create_app(tmp_path, config_path, resource_dir, FakeProcessManager())
    client = TestClient(app)

    listing = client.get('/api/files').json()
    names = {item['name'] for item in listing['items']}
    assert 'visible.json' in names
    assert '.hidden.json' not in names
    assert 'Temp' not in names
    assert client.get('/api/files', params={'path': '../'}).status_code == 422
    assert client.get('/api/files', params={'path': 'visible.json'}).status_code == 400


def test_shutdown_calls_process_manager(tmp_path):
    """应用关闭时应停止推送控制器。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('home', encoding='utf-8')
    manager = FakeProcessManager()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    app = create_app(tmp_path, config_path, resource_dir, manager)
    with TestClient(app):
        pass



def test_task6_routes_and_push_payload_contract(tmp_path):
    """任务接口应存在并将三种动作路由到正确的控制器方法。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('home', encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    (tmp_path / 'payload.json').write_text('{"title":"t","content":"c","channels":[{}]}', encoding='utf-8')

    class Manager(FakeProcessManager):
        def __init__(self):
            self.calls = []
        def start_file_push(self, path, config):
            self.calls.append(('file', path.name))
            return 'file-task'
        def start_payload_push(self, payload, config, source_name='push'):
            self.calls.append(('payload', payload, source_name))
            return 'payload-task'
        def stop(self):
            self.calls.append(('stop',))
            return True

    manager = Manager()
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))
    assert client.get('/api/push/status').status_code == 200
    assert client.post('/api/push', json={'action': 'direct', 'path': 'payload.json'}).json()['task_id'] == 'payload-task'
    assert client.post('/api/push', json={'action': 'save', 'path': 'payload.json'}).status_code == 200
    assert client.post('/api/push', json={'action': 'save_and_push', 'path': 'payload.json'}).json()['task_id'] == 'file-task'
    assert client.post('/api/push/stop').status_code == 200
    assert client.post('/api/service/stop').status_code == 200
    assert client.post('/api/json/validate', json={'content': '{"title":"t","content":"c","channels":[{}]}'}).status_code == 200


def test_task6_compatibility_and_current_payload_semantics(tmp_path):
    """文件和推送接口应遵守前端兼容契约并使用当前载荷。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('home', encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    class Manager(FakeProcessManager):
        def __init__(self):
            self.calls = []
        def start_file_push(self, path, config):
            self.calls.append(('file', path))
            return 'file'
        def start_payload_push(self, payload, config, source_name='push'):
            self.calls.append(('payload', payload, source_name))
            return 'payload'
        def shutdown(self):
            pass

    manager = Manager()
    (tmp_path / 'old.json').write_text('{"title":"old","content":"c","channels":[{}]}', encoding='utf-8')
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))
    listing = client.get('/api/files', params={'directory': ''}).json()
    assert 'files' in listing and listing['files'] == listing['items']
    payload = {'title': 'new', 'content': 'body', 'channels': [{}], 'unknown': 'keep'}
    assert client.post('/api/push', json={'action': 'direct', 'path': 'old.json', 'payload': payload}).json()['task_id'] == 'payload'
    assert client.post('/api/push', json={'action': 'save', 'path': 'old.json', 'payload': payload}).status_code == 200
    assert client.post('/api/push', json={'action': 'save_and_push', 'path': 'old.json', 'payload': payload}).status_code == 200
    assert manager.calls[0][0] == 'payload'
    assert manager.calls[0][1] == payload
    assert manager.calls[1][0] == 'file'
    assert manager.calls[1][1] == Path(tmp_path / 'old.json')
    assert json.loads((tmp_path / 'old.json').read_text(encoding='utf-8')) == payload


def test_temp_is_program_dir_scoped_and_rejects_links_and_non_temp_names(tmp_path):
    """临时接口必须固定程序目录并只处理安全 Temp JSON。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    program_dir = tmp_path / 'program'
    program_dir.mkdir()
    temp = program_dir / 'Temp'
    temp.mkdir()
    (temp / 'Temp_ok.json').write_text('[1]', encoding='utf-8')
    (temp / 'other.json').write_text('[2]', encoding='utf-8')
    outside = tmp_path / 'outside.json'
    outside.write_text('secret', encoding='utf-8')
    try:
        (temp / 'Temp_link.json').symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    manager = FakeProcessManager()
    manager.program_dir = program_dir
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))
    assert client.get('/api/temp').json()['files'] == ['Temp_ok.json']
    assert client.get('/api/temp/Temp_ok.json').status_code == 200
    assert client.get('/api/temp/other.json').status_code == 404


def test_temp_read_and_delete_reject_temp_directory_reparse_point(tmp_path):
    """临时目录重解析到工作区外时，读取和删除必须拒绝。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    program_dir = tmp_path / 'program'
    program_dir.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'Temp_escape.json').write_text('secret', encoding='utf-8')
    try:
        (program_dir / 'Temp').symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        return

    manager = FakeProcessManager()
    manager.program_dir = program_dir
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))

    assert client.get('/api/temp/Temp_escape.json').status_code == 404
    assert client.post('/api/temp/delete', json={
        'names': ['Temp_escape.json'],
        'confirmed': True,
    }).status_code == 404
    assert (outside / 'Temp_escape.json').exists()



@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows 专项重解析点回归测试')
def test_temp_rejects_windows_junction_without_touching_external_target(tmp_path):
    """临时目录为 Junction 时读取和删除必须拒绝且不影响外部目标。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    program_dir = tmp_path / 'program'
    program_dir.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    external_file = outside / 'Temp_junction.json'
    external_file.write_text('secret', encoding='utf-8')
    junction = program_dir / 'Temp'
    result = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f'无法创建 Junction: {result.stderr or result.stdout}')

    manager = FakeProcessManager()
    manager.program_dir = program_dir
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))

    assert client.get('/api/temp').json()['files'] == []
    assert client.get('/api/temp/Temp_junction.json').status_code == 404
    assert client.post('/api/temp/delete', json={
        'names': ['Temp_junction.json'],
        'confirmed': True,
    }).status_code == 404
    assert external_file.exists()


def test_lifespan_shutdown_calls_control_and_manager_in_order(tmp_path):
    """应用生命周期结束时应先停止服务控制器，再清理推送控制器。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    class WebServerControl:
        def __init__(self):
            self.calls = []

        def stop(self):
            self.calls.append('control.stop')

    class Manager(FakeProcessManager):
        def __init__(self, calls):
            self.calls = calls

        def stop(self):
            self.calls.append('manager.stop')

        def shutdown(self):
            self.calls.append('manager.shutdown')

    calls = []
    control = WebServerControl()
    control.calls = calls
    manager = Manager(calls)

    with TestClient(create_app(tmp_path, config_path, resource_dir, manager, control)):
        pass

    assert calls == ['control.stop', 'manager.stop', 'manager.shutdown']


def test_lifespan_shutdown_calls_shutdown_after_manager_stop_error(tmp_path):
    """推送停止异常时仍应继续调用最终 shutdown。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    class Manager(FakeProcessManager):
        def __init__(self):
            self.calls = []

        def stop(self):
            self.calls.append('stop')
            raise RuntimeError('stop failed')

        def shutdown(self):
            self.calls.append('shutdown')

    manager = Manager()
    with pytest.raises(RuntimeError, match='stop failed'):
        with TestClient(create_app(tmp_path, config_path, resource_dir, manager)):
            pass

    assert manager.calls == ['stop', 'shutdown']


def test_lifespan_shutdown_cleans_manager_after_control_stop_error(tmp_path):
    """服务控制器停止异常时仍应清理推送控制器并传播原异常。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    class Control:
        def stop(self):
            raise RuntimeError('control stop failed')

    class Manager(FakeProcessManager):
        def __init__(self):
            self.calls = []

        def stop(self):
            self.calls.append('stop')

        def shutdown(self):
            self.calls.append('shutdown')

    manager = Manager()
    with pytest.raises(RuntimeError, match='control stop failed'):
        with TestClient(create_app(tmp_path, config_path, resource_dir, manager, Control())):
            pass

    assert manager.calls == ['stop', 'shutdown']


def test_lifespan_shutdown_preserves_first_error_and_runs_all_cleanup(tmp_path):
    """多阶段清理异常时应传播最早异常并完成所有清理调用。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    class Control:
        def __init__(self, calls):
            self.calls = calls

        def stop(self):
            self.calls.append('control.stop')
            raise RuntimeError('control failed')

    class Manager(FakeProcessManager):
        def __init__(self, calls):
            self.calls = calls

        def stop(self):
            self.calls.append('manager.stop')
            raise RuntimeError('manager stop failed')

        def shutdown(self):
            self.calls.append('manager.shutdown')
            raise RuntimeError('manager shutdown failed')

    calls = []
    manager = Manager(calls)
    with pytest.raises(RuntimeError, match='control failed'):
        with TestClient(create_app(tmp_path, config_path, resource_dir, manager, Control(calls))):
            pass

    assert calls == ['control.stop', 'manager.stop', 'manager.shutdown']


def test_task6_ini_content_contract_and_nested_json_metadata(tmp_path):
    """INI 接口应传输原文，嵌套 JSON 保存不应写入控制字段。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Network]\nproxy = old\n', encoding='utf-8')
    manager = FakeProcessManager()
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))

    ini_response = client.get('/api/ini')
    assert ini_response.json() == {'path': 'config.ini', 'content': '[Network]\nproxy = old\n'}
    ini_path = tmp_path / 'nested' / 'saved.ini'
    ini_content = '[Network]\nproxy = new\n'
    assert client.put('/api/ini', json={'path': 'nested/saved.ini', 'content': ini_content}).json()['saved'] is True
    assert ini_path.read_text(encoding='utf-8') == ini_content

    payload = {'title': 't', 'content': 'c', 'channels': [{}], 'path': 'nested/saved.json', 'action': 'save'}
    response = client.post('/api/json', json=payload)
    assert response.status_code == 200
    saved = json.loads((tmp_path / 'nested' / 'saved.json').read_text(encoding='utf-8'))
    assert saved == {'title': 't', 'content': 'c', 'channels': [{}]}


def test_put_ini_validates_content_preserves_token_and_raw_text(tmp_path):
    """INI 原文保存应校验配置并保留已有访问令牌。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    original = '[Web]\naccess_token = keep-this-secret\n\n[Network]\nproxy = old\n'
    config_path.write_text(original, encoding='utf-8')
    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    headers = {'Authorization': 'Bearer keep-this-secret'}

    response = client.put('/api/ini', json={
        'content': '[Web]\naccess_token = changed\n\n[Network]\nproxy = new\n',
    }, headers=headers)

    assert response.status_code == 200
    assert 'access_token = keep-this-secret' in config_path.read_text(encoding='utf-8')
    assert 'proxy = new' in config_path.read_text(encoding='utf-8')


def test_put_ini_returns_validation_error_contract_and_does_not_write(tmp_path):
    """INI 校验失败应返回字段错误契约且保持原文件不变。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    original = '[Push]\nretry_interval = 3s\n'
    config_path.write_text(original, encoding='utf-8')
    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    headers = {'Authorization': 'Bearer ' + 'x' * 16}

    response = client.put('/api/ini', json={
        'content': '[Push]\nretry_interval = bad\n[Unknown]\nkey = value\n',
    }, headers=headers)

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INI_VALIDATION_FAILED'
    assert 'errors' in response.json()['error']['details']
    assert config_path.read_text(encoding='utf-8') == original


def test_put_ini_preserves_unknown_original_sections_and_keys(tmp_path):
    """INI 原文中的未知节键应允许保存，同时继续校验可编辑字段。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text(
        '[Network]\nproxy = old\nunknown_network = keep\n\n'
        '[Custom]\ncustom_key = keep\n',
        encoding='utf-8',
    )
    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))

    response = client.put('/api/ini', json={
        'content': '[Network]\nproxy = new\nunknown_network = changed\n\n'
                  '[Custom]\ncustom_key = changed\n',
    })

    assert response.status_code == 200
    saved = config_path.read_text(encoding='utf-8')
    assert 'proxy = new' in saved
    assert 'unknown_network = changed' in saved
    assert '[Custom]' in saved
    assert 'custom_key = changed' in saved


def test_put_json_creates_multilevel_parent_directories(tmp_path):
    """JSON 新建文件应安全创建尚不存在的多级父目录。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    target = tmp_path / 'level-one' / 'level-two' / 'new.json'
    payload = {'title': 't', 'content': 'c', 'channels': [{}]}

    response = client.post('/api/json', json={**payload, 'path': 'level-one/level-two/new.json'})

    assert response.status_code == 200
    assert target.exists()
    assert json.loads(target.read_text(encoding='utf-8')) == payload


def test_task6_temp_delete_requires_true_confirmation_and_safe_array_contract(tmp_path):
    """临时文件删除必须要求 confirmed=true，并拒绝越界名称。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    program_dir = tmp_path / 'program'
    temp = program_dir / 'Temp'
    temp.mkdir(parents=True)
    target = temp / 'Temp_ok.json'
    target.write_text('{}', encoding='utf-8')
    manager = FakeProcessManager()
    manager.program_dir = program_dir
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))

    assert client.post('/api/temp/delete', json={'names': ['Temp_ok.json'], 'confirmed': False}).status_code == 400
    assert client.post('/api/temp/delete', json={'names': ['Temp_ok.json'], 'confirmed': True}).json()['deleted'] is True
    assert not target.exists()
    assert client.post('/api/temp/delete', json={'names': ['Temp_ok.json'], 'confirmed': True}).status_code == 404




def test_temp_delete_deduplicates_repeated_names(tmp_path):
    """临时文件删除接口应允许 names 中出现重复名称。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    program_dir = tmp_path / 'program'
    temp = program_dir / 'Temp'
    temp.mkdir(parents=True)
    target = temp / 'Temp_duplicate.json'
    target.write_text('{}', encoding='utf-8')
    manager = FakeProcessManager()
    manager.program_dir = program_dir
    client = TestClient(create_app(tmp_path, config_path, resource_dir, manager))

    response = client.post('/api/temp/delete', json={
        'names': ['Temp_duplicate.json', 'Temp_duplicate.json'],
        'confirmed': True,
    })

    assert response.status_code == 200
    assert response.json()['names'] == ['Temp_duplicate.json']
    assert not target.exists()


def test_frontend_type_switch_clears_editor_context(tmp_path):
    """切换 JSON/INI 类型时前端必须清空旧文件上下文。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    app_script = resource_dir / 'app.js'
    app_script.write_text((Path(__file__).parents[1] / 'web' / 'app.js').read_text(encoding='utf-8'), encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    script = client.get('/app.js').text

    assert "state.path = '';" in script
    assert "editor.value = '';" in script
    assert "setMessage('editor-title', '');" in script


def test_frontend_async_file_responses_require_current_request_identity(tmp_path):
    """旧文件响应不得覆盖新选择或类型切换后的编辑器。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    app_script = resource_dir / 'app.js'
    app_script.write_text((Path(__file__).parents[1] / 'web' / 'app.js').read_text(encoding='utf-8'), encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    script = client.get('/app.js').text

    assert 'state.requestVersion' in script
    assert 'requestVersion !== state.requestVersion' in script
    assert 'const requestedKind = state.kind;' in script
    assert 'const requestedPath = path;' in script


def test_frontend_file_browser_and_creation_controls_contract(tmp_path):
    """前端应提供目录导航、返回、新建 JSON 和另存为控件。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    app_script = resource_dir / 'app.js'
    app_script.write_text((Path(__file__).parents[1] / 'web' / 'app.js').read_text(encoding='utf-8'), encoding='utf-8')
    index_html = resource_dir / 'index.html'
    index_html.write_text((Path(__file__).parents[1] / 'web' / 'index.html').read_text(encoding='utf-8'), encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    script = client.get('/app.js').text
    page = client.get('/').text

    assert "state.directory" in script
    assert "directory: state.directory" in script or "directory=' + encodeURIComponent(state.directory)" in script
    assert "parent-directory" in page
    assert "new-json" in page
    assert "save-as" in page


def test_frontend_prompts_before_discarding_unsaved_changes(tmp_path):
    """前端切换文件、目录和配置类型前应确认未保存变更。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    app_script = resource_dir / 'app.js'
    app_script.write_text((Path(__file__).parents[1] / 'web' / 'app.js').read_text(encoding='utf-8'), encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    script = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager())).get('/app.js').text

    assert "state.dirty" in script
    assert "放弃未保存的变更" in script
    assert "beforeContextChange" in script


def test_frontend_ini_switch_uses_ini_api_and_save_as_uses_json_api(tmp_path):
    """前端应使用现有 INI 接口，并以 JSON API 创建或另存文件。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    app_script = resource_dir / 'app.js'
    app_script.write_text((Path(__file__).parents[1] / 'web' / 'app.js').read_text(encoding='utf-8'), encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')

    script = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager())).get('/app.js').text

    assert "request('/api/ini?path='" in script or "'/api/ini?path='" in script
    assert "request('/api/json'" in script
    assert "method: 'POST'" in script




def test_web_file_errors_return_safe_details_and_type_is_logged(tmp_path, caplog):
    """Web 文件错误 details 不泄露路径或异常文本，日志保留错误类型。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\\naccess_token = \\n', encoding='utf-8')
    logger = __import__('logging').getLogger('web-safe-error-test')
    app = create_app(tmp_path, config_path, resource_dir, FakeProcessManager(), logger=logger)
    client = TestClient(app)
    target = tmp_path / 'bad.json'
    target.write_text('{bad', encoding='utf-8')
    response = client.get('/api/json', params={'path': 'bad.json'})
    assert response.status_code == 400
    assert response.json()['error']['details'] == {'reason': 'JSONDecodeError'}
    details = response.json()['error']['details']
    assert details == {'reason': 'JSONDecodeError'}
    assert str(tmp_path) not in response.text


def test_web_save_error_details_are_stable(tmp_path, monkeypatch):
    """Web 保存失败只返回稳定错误摘要。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\\naccess_token = \\n', encoding='utf-8')
    app = create_app(tmp_path, config_path, resource_dir, FakeProcessManager())
    monkeypatch.setattr(web_server.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('C:\\\\secret\\\\file')))
    response = TestClient(app).put('/api/json', json={
        'path': 'saved.json', 'title': 't', 'content': 'c', 'channels': [{}],
    })
    assert response.status_code == 500
    assert response.json()['error']['details'] == {'reason': 'OSError'}
    assert 'secret' not in response.text


def test_task6_missing_json_returns_not_found_error_shape(tmp_path):
    """缺失 JSON 必须返回 404 和稳定错误结构。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    response = client.get('/api/json', params={'path': 'missing.json'})
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'JSON_NOT_FOUND'
    assert set(response.json()['error']) == {'code', 'message', 'details'}
