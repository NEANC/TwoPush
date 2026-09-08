"""验证测试依赖与 Starlette 测试客户端的兼容性。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_requirements_do_not_include_test_client_dependencies():
    """生产依赖不应安装仅供测试使用的客户端依赖。"""
    production = (ROOT / 'requirements.txt').read_text(encoding='utf-8').lower()

    assert 'pytest' not in production
    assert 'httpx' not in production
    assert 'anyio' not in production


def test_runtime_requirements_use_a_fastapi_starlette_compatible_intersection():
    """运行时 FastAPI 与 Starlette 约束应存在实际兼容交集。"""
    production = (ROOT / 'requirements.txt').read_text(encoding='utf-8').lower()

    assert 'fastapi>=0.115,<0.116' in production
    assert 'starlette>=0.40,<0.46' in production


def test_development_requirements_use_supported_test_client_dependencies():
    """开发依赖应使用 Starlette 支持的 httpx 与 anyio 范围。"""
    development = (ROOT / 'requirements-dev.txt').read_text(encoding='utf-8').lower()

    assert 'httpx>=0.28,<0.29' in development
    assert 'httpx2' not in development
    assert 'anyio>=4.4,<5' in development


def test_fastapi_test_client_imports_without_deprecation_warning():
    """真实导入 httpx 并用 TestClient 请求简单 FastAPI 应用。"""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        import httpx
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get('/health')
    def health_check():
        return {'status': 'ok'}

    with TestClient(app) as client:
        response = client.get('/health')

    assert httpx.__version__
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
