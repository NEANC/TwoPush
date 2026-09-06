#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""FastAPI Web 服务测试。"""

from pathlib import Path

from fastapi.testclient import TestClient

from modules.web_server import create_app


class FakeProcessManager:
    """测试用推送控制器。"""

    def get_status(self, cursor=0):
        """返回空闲状态。"""
        return {'task_id': None, 'status': 'idle', 'exit_code': None, 'outputs': []}

    def shutdown(self):
        """记录服务退出。"""
        return None


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
    assert client.post('/api/push', json={'action': 'direct', 'path': 'payload.json'}).json()['task_id'] == 'file-task'
    assert client.post('/api/push', json={'action': 'save', 'path': 'payload.json'}).json()['task_id'] == 'payload-task'
    assert client.post('/api/push', json={'action': 'save_and_push', 'path': 'payload.json'}).json()['task_id'] == 'payload-task'
    assert client.post('/api/push/stop').status_code == 200
    assert client.post('/api/service/stop').status_code == 200
    assert client.post('/api/json/validate', json={'content': '{"title":"t","content":"c","channels":[{}]}'}).status_code == 200


def test_json_and_ini_select_temp_routes(tmp_path):
    """选择、临时文件和路径错误响应应符合契约。"""
    resource_dir = tmp_path / 'web'
    resource_dir.mkdir()
    (resource_dir / 'index.html').write_text('home', encoding='utf-8')
    config_path = tmp_path / 'config.ini'
    config_path.write_text('[Web]\naccess_token = \n', encoding='utf-8')
    client = TestClient(create_app(tmp_path, config_path, resource_dir, FakeProcessManager()))
    assert client.post('/api/json/select', json={'path': 'missing.json'}).status_code == 400
    assert client.post('/api/ini/select', json={'path': '../bad.ini'}).json()['error']['code'] == 'invalid_path'
    assert client.get('/api/temp').status_code == 200
    assert client.get('/api/temp/missing.json').status_code == 404
    assert client.post('/api/temp/delete', json={'name': 'missing.json', 'confirmed': True}).status_code == 404
