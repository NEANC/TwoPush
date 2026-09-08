"""验证测试依赖与 Starlette 测试客户端的兼容性。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_requirements_do_not_include_test_client_dependencies():
    """生产依赖不应安装仅供测试使用的客户端依赖。"""
    production = (ROOT / 'requirements.txt').read_text(encoding='utf-8').lower()

    assert 'pytest' not in production
    assert 'httpx' not in production
    assert 'anyio' not in production


def test_development_requirements_use_starlette_supported_test_client():
    """开发依赖应使用 Starlette 支持的 httpx2 测试客户端。"""
    development = (ROOT / 'requirements-dev.txt').read_text(encoding='utf-8').lower()

    assert 'httpx2>=' in development
    assert 'anyio>=4.4,<4.6' in development


def test_fastapi_test_client_imports_without_deprecation_warning():
    """在警告升级为错误时 TestClient 仍应能导入。"""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        from fastapi.testclient import TestClient

    assert TestClient is not None
