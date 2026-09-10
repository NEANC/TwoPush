#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""源码模式 FastAPI Web 服务。"""

import ctypes
import json
import os
import threading
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from fastapi.exceptions import RequestValidationError

from modules.server_auth import ServerAuthStore
from modules.web_models import (
    FileOperationRequest,
    JsonTemplatePayload,
    PushRequest,
    TempDeleteRequest,
    LaunchAuthPayload,
    resolve_web_host,
)
from modules.logger_manager import sanitize_log_message
from modules.web_resources import get_web_resource


class WebSessionState:
    """保存 Web 会话的一次性查询令牌状态。"""

    def __init__(self):
        """初始化会话状态。"""
        self.query_token_used = False
        self.lock = threading.Lock()


class WebServerControl:
    """控制 Web 服务停止请求。"""

    def __init__(self):
        """初始化服务控制状态。"""
        self.stop_requested = False
        self.lock = threading.Lock()
        self.first_error = None

    def stop(self):
        """记录首个停止原因并标记服务应停止。"""
        with self.lock:
            if not self.stop_requested:
                self.first_error = 'service_stop'
            self.stop_requested = True


def _is_reparse_point(path: Path) -> bool:
    """判断 Windows 路径是否为重解析点。"""
    if os.name != 'nt':
        return path.is_symlink()
    try:
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attributes not in (-1, 0xFFFFFFFF) and bool(attributes & 0x400)
    except (AttributeError, OSError):
        return path.is_symlink()


def _error(code: str, message: str, details: dict[str, Any] | None = None,
           status_code: int = 400) -> JSONResponse:
    """创建统一错误响应。"""
    return JSONResponse(
        status_code=status_code,
        content={'error': {'code': code, 'message': message, 'details': details or {}}},
    )


def _safe_error_details(error: Exception) -> dict[str, str]:
    """返回不包含底层异常文本的稳定错误摘要，并记录异常类型。"""
    return {'reason': type(error).__name__}


def _inside(root: Path, candidate: Path) -> Path:
    """解析并确认路径位于固定工作区内。"""
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError('路径越出工作区或包含越界链接') from error
    return resolved


def _is_hidden(path: Path) -> bool:
    """判断文件名或 Windows 隐藏属性。"""
    if path.name.startswith('.') or path.name.lower() == 'temp':
        return True
    try:
        return bool(getattr(path.stat(), 'st_file_attributes', 0) & 2)
    except OSError:
        return True


def create_app(root_dir, config_path, resource_dir, process_manager,
               server_control=None, session_state=None, logger=None,
               root_path='', base_path='', public_url_is_https=False,
               temp_dir=None, auth_store=None):
    """创建固定工作区的 FastAPI 应用。"""
    root = Path(root_dir).resolve()
    config_file = Path(config_path).resolve()
    resources = Path(resource_dir).resolve()
    base_path = '/' + base_path.strip('/') if base_path else ''
    control = server_control or WebServerControl()
    session = session_state or WebSessionState()
    auth = auth_store or ServerAuthStore('', clock=None)
    access_token = ''
    try:
        import configparser
        parser = configparser.ConfigParser()
        parser.read(config_file, encoding='utf-8')
        access_token = parser.get('Web', 'access_token', fallback='')
    except (OSError, configparser.Error):
        access_token = ''

    auth_cleared = False

    def clear_auth_once():
        """清理认证状态且避免停止流程重复清理。"""
        nonlocal auth_cleared
        if auth_cleared:
            return
        auth.clear()
        auth_cleared = True

    @asynccontextmanager
    async def lifespan(_app):
        """管理服务生命周期并在退出时清理推送任务。"""
        yield
        first_error = None
        try:
            clear_auth_once()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            control.stop()
        except Exception as error:
            first_error = error
        try:
            process_manager.stop()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            process_manager.shutdown()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    app = FastAPI(lifespan=lifespan, root_path=root_path)
    app.state.logger = logger or logging.getLogger('TwoPush.GUI')
    app.state.process_manager = process_manager
    app.state.config_file = config_file
    app.state.program_dir = Path(getattr(process_manager, 'program_dir', root)).resolve()
    app.state.temp_dir = Path(temp_dir).resolve() if temp_dir is not None else app.state.program_dir / 'Temp'
    app.state.server_control = control
    app.state.workspace_root = root
    app.state.root_path = root_path
    app.state.base_path = base_path
    app.state.host = resolve_web_host(access_token)

    def log_event(event, level=logging.INFO, **fields):
        """记录不包含请求明细的业务事件。"""
        safe_fields = {
            key: sanitize_log_message(value, root=root) if isinstance(value, str) else value
            for key, value in fields.items()
        }
        summary = ' '.join(f'{key}={value}' for key, value in safe_fields.items())
        app.state.logger.log(level, 'Web %s%s', event, f' {summary}' if summary else '')

    @app.middleware('http')
    async def base_path_router(request: Request, call_next):
        """在基础路径部署时将请求映射到现有路由。"""
        if base_path and (request.scope['path'] == base_path or request.scope['path'].startswith(base_path + '/')):
            request.scope['path'] = request.scope['path'][len(base_path):] or '/'
            request.scope['root_path'] = root_path + base_path
        return await call_next(request)

    @app.middleware('http')
    async def security_headers(request: Request, call_next):
        """为所有响应设置安全响应头。"""
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        """将 HTTP 异常转换为统一错误结构。"""
        log_event('请求非法', logging.WARNING)
        detail = exc.detail
        if isinstance(detail, dict):
            return _error(detail.get('code', 'HTTP_ERROR'), detail.get('message', '请求失败'), detail.get('details'), exc.status_code)
        return _error('HTTP_ERROR', str(detail), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        """将请求校验异常转换为统一错误结构。"""
        log_event('参数校验失败', logging.WARNING)
        errors = [{**item, 'ctx': {key: str(value) for key, value in item.get('ctx', {}).items()}}
                  for item in exc.errors()]
        if errors and errors[0].get('loc', ())[-1] == 'path':
            return _error('invalid_path', errors[0].get('msg', '路径无效'), status_code=422)
        return _error('VALIDATION_ERROR', '请求参数校验失败', {'errors': errors}, 422)

    def authorized(request: Request, allow_query=False):
        """校验 Bearer、会话 Cookie 或兼容查询认证。"""
        if len(access_token) < 16:
            return True
        authorization = request.headers.get('authorization', '')
        if authorization == f'Bearer {access_token}':
            return True
        if auth.is_session_valid(request.cookies.get('twopush_session')):
            return True
        if allow_query and request.query_params.get('token') == access_token:
            with session.lock:
                if not session.query_token_used:
                    session.query_token_used = True
                    return True
        return False

    async def require_auth(request: Request):
        """验证受保护 API 的访问令牌。"""
        if not authorized(request):
            raise HTTPException(401, '需要有效的 Bearer 令牌')

    def safe_path(value: str) -> Path:
        """校验相对路径并拒绝越界链接。"""
        try:
            checked = FileOperationRequest(path=value).path
        except ValueError as error:
            log_event('path invalid', logging.WARNING, path=value)
            raise HTTPException(422, detail={'code': 'invalid_path', 'message': str(error)}) from error
        try:
            return _inside(root, root.joinpath(*checked.replace('\\', '/').split('/')))
        except (OSError, ValueError) as error:
            log_event('path invalid', logging.WARNING, path=sanitize_log_message(checked), error_type=type(error).__name__)
            raise HTTPException(422, detail={'code': 'invalid_path', 'message': '路径越出工作区或包含越界链接'}) from error

    def safe_temp_name(value: str) -> str:
        """校验临时文件名并统一返回不存在错误。"""
        try:
            return TempDeleteRequest(names=[value], confirmed=True).names[0]
        except ValidationError as error:
            log_event('temp path invalid', logging.WARNING, name=sanitize_log_message(value))
            raise HTTPException(404, detail={'code': 'TEMP_NOT_FOUND', 'message': '临时文件不存在'}) from error

    def safe_temp_path(name: str) -> Path:
        """校验临时目录和文件均未越过程序目录。"""
        temp = app.state.temp_dir
        checked_name = safe_temp_name(name)
        target = temp / checked_name
        try:
            temp_resolved = temp.resolve(strict=False)
            target_resolved = target.resolve(strict=False)
            if _is_reparse_point(temp) or _is_reparse_point(target) or target_resolved.parent != temp_resolved:
                raise OSError('临时路径包含重解析点')
        except (OSError, ValueError) as error:
            log_event('temp path invalid', logging.WARNING, name=sanitize_log_message(checked_name), error_type=type(error).__name__)
            raise HTTPException(422, detail={'code': 'invalid_path', 'message': '临时路径无效'}) from error
        return target

    def json_payload(value: dict[str, Any]) -> dict[str, Any]:
        """复用公共 JSON 模板模型校验载荷。"""
        try:
            return JsonTemplatePayload.model_validate(value).model_dump()
        except ValidationError as error:
            raise HTTPException(422, detail={'code': 'invalid_json', 'message': 'JSON 模板校验失败', 'details': error.errors()}) from error

    def _save_json(target: Path, data: dict[str, Any]) -> None:
        """将 JSON 载荷原子保存到指定文件。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f'.{target.name}.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, target)

    def _save_ini_text(target: Path, content: str) -> None:
        """将 INI 原文保存到指定文件。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f'.{target.name}.tmp')
        temporary.write_text(content, encoding='utf-8')
        os.replace(temporary, target)

    @app.post('/api/auth/launch')
    async def launch_auth(payload: LaunchAuthPayload):
        """兑换启动令牌并写入进程内会话 Cookie。"""
        session_token = auth.consume_launch_token(payload.launch_token)
        if session_token is None:
            return _error('UNAUTHORIZED', '启动令牌无效', status_code=401)
        response = Response(status_code=204)
        response.set_cookie('twopush_session', session_token, httponly=True,
                            samesite='strict', secure=public_url_is_https,
                            path=base_path or '/')
        return response

    @app.get('/api/health')
    @app.head('/api/health')
    async def health():
        """返回不认证且不缓存的服务健康状态。"""
        stopping = control.stop_requested
        response = JSONResponse({'ready': not stopping}, status_code=503 if stopping else 200)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/', response_class=HTMLResponse)
    async def index(request: Request):
        """返回首页并兑换 URL 中的一次性启动令牌。"""
        launch_token = request.query_params.get('launch_token')
        if launch_token:
            if not auth.has_launch_token(launch_token):
                raise HTTPException(401, '需要有效的访问令牌')
            return HTMLResponse(get_web_resource('index.html', resources.parent).replace(
                b'__BASE_PATH__', base_path.encode('utf-8')))
        if not authorized(request, allow_query=True):
            raise HTTPException(401, '需要有效的访问令牌')
        return HTMLResponse(get_web_resource('index.html', resources.parent).replace(
            b'__BASE_PATH__', base_path.encode('utf-8')))

    @app.get('/app.js')
    async def app_js():
        """返回 JavaScript 静态资源。"""
        return Response(get_web_resource('app.js', resources.parent), media_type='text/javascript')

    @app.get('/style.css')
    async def style_css():
        """返回 CSS 静态资源。"""
        return Response(get_web_resource('style.css', resources.parent), media_type='text/css')

    @app.get('/api/session', dependencies=[__import__('fastapi').Depends(require_auth)])
    async def session_info():
        """返回不含令牌的会话信息。"""
        log_event('session')
        return {'host': app.state.host, 'authenticated': len(access_token) >= 16}

    @app.get('/api/files', dependencies=[__import__('fastapi').Depends(require_auth)])
    async def list_files(directory: str = Query('', description='工作区内相对目录'), path: str | None = None):
        """逐级列出工作区中的非隐藏项目。"""
        path = directory if path is None else path
        directory = root if not path else safe_path(path)
        if not directory.is_dir():
            log_event('files not_found', logging.WARNING, path=path)
            return _error('NOT_DIRECTORY', '目标不是目录')
        items = []
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
        except OSError as error:
            log_event('files read_failed', logging.ERROR, path=path, error_type=type(error).__name__)
            return _error('FILES_READ_FAILED', '目录无法读取', _safe_error_details(error), status_code=500)
        for item in entries:
            if _is_hidden(item):
                continue
            try:
                relative = item.relative_to(root).as_posix()
                _inside(root, item)
            except (OSError, ValueError):
                continue
            items.append({'name': item.name, 'path': relative, 'type': 'directory' if item.is_dir() else 'file'})
        log_event('files success', path='' if directory == root else directory.relative_to(root).as_posix())
        return {'path': '' if directory == root else directory.relative_to(root).as_posix(), 'items': items, 'files': items}

    @app.get('/api/json', dependencies=[__import__('fastapi').Depends(require_auth)])
    async def read_json(path: str):
        """读取 JSON 文件并保留未知字段。"""
        target = safe_path(path)
        if target.suffix.lower() != '.json' or not target.is_file():
            return _error('JSON_NOT_FOUND', 'JSON 文件不存在', status_code=404)
        try:
            log_event('json read')
            return json.loads(target.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            log_event('json read failed', logging.ERROR, error_type=type(error).__name__)
            return _error('JSON_INVALID', 'JSON 文件无法读取', _safe_error_details(error))

    @app.put('/api/json', dependencies=[Depends(require_auth)])
    @app.post('/api/json', dependencies=[Depends(require_auth)])
    async def write_json(request: Request, path: str | None = None, payload: JsonTemplatePayload | None = None):
        """原子保存 JSON 模板。"""
        body = await request.json()
        target_path = path or body.pop('path', None)
        body.pop('action', None)
        if not target_path:
            return _error('JSON_PATH_INVALID', '缺少 JSON 文件路径')
        target = safe_path(target_path)
        if target.suffix.lower() != '.json':
            return _error('JSON_PATH_INVALID', '目标必须是 JSON 文件')
        data = json_payload(body)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f'.{target.name}.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, target)
        except OSError as error:
            log_event('json write failed', logging.ERROR, error_type=type(error).__name__)
            return _error('SAVE_FAILED', 'JSON 保存失败', _safe_error_details(error), 500)
        log_event('json write')
        return {'saved': True, 'path': target.relative_to(root).as_posix(), 'message': '已保存'}

    @app.post('/api/json/select', dependencies=[Depends(require_auth)])
    async def select_json(body: FileOperationRequest):
        """选择 JSON 文件并返回内容。"""
        target = safe_path(body.path)
        if target.suffix.lower() != '.json' or not target.is_file():
            log_event('json select not_found', logging.WARNING, path=body.path)
            return _error('JSON_NOT_FOUND', 'JSON 文件不存在', status_code=404)
        try:
            result = json.loads(target.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            log_event('json select read_failed', logging.ERROR, path=body.path, error_type=type(error).__name__)
            return _error('JSON_INVALID', 'JSON 文件无法读取', _safe_error_details(error), 500)
        log_event('json select success', path=body.path)
        return result

    @app.post('/api/json/validate', dependencies=[Depends(require_auth)])
    async def validate_json(request: Request):
        """验证 JSON 文本或对象。"""
        body = await request.json()
        value = body.get('content', body) if isinstance(body, dict) else body
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            json_payload(parsed)
        except (json.JSONDecodeError, TypeError):
            return _error('invalid_json', 'JSON 格式无效')
        except HTTPException as error:
            return _error(error.detail.get('code', 'invalid_json'), error.detail.get('message', 'JSON 模板校验失败'))
        log_event('json validate')
        return {'valid': True, 'message': 'JSON 有效'}

    @app.get('/api/ini', dependencies=[__import__('fastapi').Depends(require_auth)])
    async def read_ini_api(path: str | None = None):
        """读取指定 INI 配置原文。"""
        selected = config_file if path is None else safe_path(path)
        if selected.suffix.lower() != '.ini' or not selected.is_file():
            return _error('INI_NOT_FOUND', 'INI 配置不存在', status_code=404)
        try:
            log_event('ini read')
            return {'path': selected.relative_to(root).as_posix(), 'content': selected.read_text(encoding='utf-8')}
        except OSError as error:
            log_event('ini read failed', logging.ERROR, error_type=type(error).__name__)
            return _error('INI_READ_FAILED', 'INI 配置读取失败', _safe_error_details(error), 500)

    @app.put('/api/ini', dependencies=[Depends(require_auth)])
    async def write_ini_api(request: Request):
        """接收并保存指定 INI 原文，并执行配置校验。"""
        body = await request.json()
        selected = config_file if not body.get('path') else safe_path(body['path'])
        if selected.suffix.lower() != '.ini':
            return _error('INI_PATH_INVALID', '目标必须是 INI 文件')
        content = body.get('content')
        if not isinstance(content, str):
            return _error('INI_INVALID', 'INI content 必须是字符串')
        try:
            from modules.web_config import parse_ini_content, protect_access_token
            errors, _ = parse_ini_content(content)
            if errors:
                return _error('INI_VALIDATION_FAILED', 'INI 配置校验失败', {'errors': errors}, 422)
            original = selected.read_text(encoding='utf-8') if selected.is_file() else ''
            content = protect_access_token(content, original)
            _save_ini_text(selected, content)
        except ValueError as error:
            return _error('INI_INVALID', str(error), status_code=422)
        except OSError as error:
            log_event('ini write failed', logging.ERROR, error_type=type(error).__name__)
            return _error('INI_SAVE_FAILED', 'INI 配置保存失败', _safe_error_details(error), 500)
        log_event('ini write')
        return {'saved': True, 'path': selected.relative_to(root).as_posix()}

    @app.post('/api/ini/select', dependencies=[Depends(require_auth)])
    async def select_ini(body: FileOperationRequest):
        """选择工作区内 INI 配置。"""
        target = safe_path(body.path)
        if target.suffix.lower() != '.ini' or not target.is_file():
            log_event('ini select not_found', logging.WARNING, path=body.path)
            return _error('INI_NOT_FOUND', 'INI 配置不存在', status_code=404)
        try:
            content = target.read_text(encoding='utf-8')
        except OSError as error:
            log_event('ini select read_failed', logging.ERROR, path=body.path, error_type=type(error).__name__)
            return _error('INI_READ_FAILED', 'INI 配置读取失败', _safe_error_details(error), 500)
        log_event('ini select success', path=body.path)
        return {'path': target.relative_to(root).as_posix(), 'content': content}

    @app.get('/api/temp', dependencies=[Depends(require_auth)])
    async def list_temp():
        """列出程序目录中临时 JSON 文件。"""
        temp = app.state.temp_dir
        try:
            temp_resolved = temp.resolve(strict=False)
        except OSError:
            return {'files': []}
        files = []
        if temp.is_dir() and not _is_reparse_point(temp) and temp_resolved == temp:
            for item in temp.glob('Temp_*.json'):
                try:
                    if item.is_file() and not _is_reparse_point(item) and item.resolve().parent == temp_resolved:
                        files.append(item.name)
                except OSError:
                    continue
        log_event('temp list')
        return {'files': sorted(files)}

    @app.get('/api/temp/{name}', dependencies=[Depends(require_auth)])
    async def read_temp(name: str):
        """读取程序目录中安全的临时 JSON 文件。"""
        temp = app.state.temp_dir
        checked_name = safe_temp_name(name)
        target = safe_temp_path(checked_name)
        if not checked_name.startswith('Temp_') or not checked_name.endswith('.json') or not target.is_file() or _is_reparse_point(target):
            log_event('temp read not_found', logging.WARNING, name=checked_name)
            return _error('TEMP_NOT_FOUND', '临时文件不存在', status_code=404)
        try:
            content = target.read_bytes()
        except OSError as error:
            log_event('temp read read_failed', logging.ERROR, name=checked_name, error_type=type(error).__name__)
            return _error('TEMP_READ_FAILED', '临时文件无法读取', _safe_error_details(error), 500)
        log_event('temp read success', name=checked_name)
        return Response(content, media_type='application/json')

    @app.post('/api/temp/delete', dependencies=[Depends(require_auth)])
    async def delete_temp(payload: TempDeleteRequest):
        """删除已确认的临时 JSON 文件。"""
        if not payload.confirmed:
            log_event('temp delete invalid', logging.WARNING)
            return _error('TEMP_CONFIRM_REQUIRED', '删除临时文件必须确认', status_code=400)
        temp = app.state.temp_dir
        deleted = []
        for name in dict.fromkeys(payload.names):
            target = safe_temp_path(name)
            if not name.startswith('Temp_') or not name.endswith('.json') or not target.is_file() or _is_reparse_point(target):
                log_event('temp delete not_found', logging.WARNING, name=name)
                return _error('TEMP_NOT_FOUND', '临时文件不存在', status_code=404)
            deleted.append(name)
        try:
            for name in deleted:
                (temp / name).unlink()
        except OSError as error:
            log_event('temp delete failed', logging.ERROR, name=deleted[0], error_type=type(error).__name__)
            return _error('TEMP_DELETE_FAILED', '临时文件删除失败', _safe_error_details(error), 500)
        log_event('temp delete success', name=deleted[0] if deleted else '')
        return {'deleted': True, 'names': deleted, 'message': '已删除'}

    @app.post('/api/push', dependencies=[Depends(require_auth)])
    async def push(request: PushRequest):
        """执行保存、直接推送或保存并推送操作。"""
        target = safe_path(request.path)
        try:
            payload = (request.payload if request.payload is not None
                       else json.loads(target.read_text(encoding='utf-8')))
            payload = json_payload(payload)
            if request.action == 'direct':
                task_id = process_manager.start_payload_push(payload, config_file, target.stem)
            elif request.action == 'save':
                _save_json(target, payload)
                return {'saved': True, 'path': target.relative_to(root).as_posix(), 'action': request.action, 'message': '已保存'}
            else:
                _save_json(target, payload)
                task_id = process_manager.start_file_push(target, config_file)
        except RuntimeError as error:
            log_event('push busy', logging.WARNING, error_type=type(error).__name__)
            return _error('PUSH_BUSY', '推送任务正忙，请稍后重试', _safe_error_details(error), 409)
        except (OSError, json.JSONDecodeError) as error:
            log_event('push failed', logging.ERROR, error_type=type(error).__name__)
            return _error('PUSH_FAILED', '推送文件无法读取', _safe_error_details(error))
        log_event('push', action=request.action)
        return {'task_id': task_id, 'action': request.action, 'message': '已提交'}

    @app.get('/api/push/status', dependencies=[Depends(require_auth)])
    @app.get('/api/status', dependencies=[Depends(require_auth)])
    async def status(cursor: int = 0):
        """返回推送状态和增量日志。"""
        log_event('status')
        return process_manager.get_status(cursor)

    @app.post('/api/push/stop', dependencies=[Depends(require_auth)])
    @app.post('/api/stop', dependencies=[Depends(require_auth)])
    async def stop():
        """停止当前推送任务。"""
        result = {'stopped': process_manager.stop(), 'message': '已停止'}
        log_event('push stop')
        return result

    @app.post('/api/service/stop', dependencies=[Depends(require_auth)])
    @app.post('/api/server/stop', dependencies=[Depends(require_auth)])
    async def stop_server():
        """只记录服务停止请求并立即返回。"""
        control.stop()
        clear_auth_once()
        return JSONResponse({'stopping': True, 'message': '服务正在停止'}, status_code=200)

    return app
