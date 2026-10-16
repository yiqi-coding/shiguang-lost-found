"""数据库会话管理（SQLAlchemy 2.x）。

- 引擎参数针对 MySQL/RDS 调整（`pool_pre_ping` + `pool_recycle` 避免 stale connection）；
- `get_db` 作为 FastAPI 依赖，事务边界由路由/服务层显式 `commit()` 控制；
- 单元测试可覆盖 `get_db` 使用 SQLite 内存库（见 backend/tests/conftest.py）。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    url = settings.DATABASE_URL
    kwargs: dict = {
        "echo": settings.DB_ECHO,
        "future": True,
        "pool_pre_ping": True,   # 取连接前 ping，避免 RDS 主动断开导致的 stale connection
        "pool_recycle": settings.DB_POOL_RECYCLE,
    }
    # SQLite（测试）不支持这些池参数
    if not url.startswith("sqlite"):
        kwargs.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout=30,
        )
    else:
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级会话。"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """测试用：重置引擎与会话工厂（切换数据库 URL 后调用）。"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def check_database() -> dict[str, object]:
    """健康检查：数据库连通性与延迟。返回结构**不含连接串与密码**。"""
    import time

    start = time.perf_counter()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "ok",
            "driver": settings.DATABASE_URL.split("://", 1)[0],
            "latency_ms": latency_ms,
            "host_masked": _mask_host(settings.DATABASE_URL),
        }
    except Exception as exc:  # noqa: BLE001 - 健康检查必须吞掉异常并报告状态
        return {
            "status": "down",
            "driver": settings.DATABASE_URL.split("://", 1)[0],
            "error": type(exc).__name__,
        }


def _mask_host(url: str) -> str:
    """从连接串中提取主机名并打码，避免泄露完整地址。"""
    try:
        after_at = url.split("@", 1)[1]
        host_port = after_at.split("/", 1)[0]
        host = host_port.split(":", 1)[0]
        if len(host) <= 6:
            return "***"
        return f"{host[:3]}****{host[-4:]}"
    except Exception:  # noqa: BLE001
        return "***"
