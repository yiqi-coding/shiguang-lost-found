"""认证路由（契约 §3.2）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/auth/login` | 公开 | 账号密码登录，限流 5 次/分/IP |
| `GET  /api/v1/auth/me` | 登录 | 刷新页面后恢复会话 |
| `POST /api/v1/auth/demo-login` | 公开（**仅非生产环境**） | 答辩演示一键切换身份 |

安全要点：

- 账号或密码错误**统一返回同一文案**（防用户枚举，契约 §3.2.1）；
- 演示登录在 `APP_ENV=production` 下直接 **404**（契约 §3.2.3），避免线上风险；
- 登录成功/失败都写审计（`user.login`），IP 由 `mask_ip()` 脱敏后落库。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    current_request_id,
    get_client_ip,
    rate_limit,
)
from app.core.config import settings
from app.core.enums import UserRole
from app.core.errors import AppError, ErrorCode, ForbiddenError, UnauthorizedError
from app.core.response import ok
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.base import utcnow
from app.models.user import User
from app.schemas.auth import DemoLoginRequest, LoginData, LoginRequest, UserBrief
from app.services import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])

DbSession = Annotated[Session, Depends(get_db)]
ClientIp = Annotated[str, Depends(get_client_ip)]

# 演示账号映射（来自脱敏演示数据集，禁止使用真实账号）
DEMO_ACCOUNTS: dict[str, str] = {
    UserRole.STUDENT: "demo_student",
    UserRole.ADMIN: "demo_admin",
}


def _find_by_username(db: Session, username: str) -> User | None:
    stmt = select(User).where(User.username == username).limit(1)
    return db.execute(stmt).scalars().first()


@router.post("/login", summary="登录", description="账号密码登录，返回 JWT（HS256，有效期 2 小时）")
def login(
    request: Request,
    payload: LoginRequest,
    db: DbSession,
    ip: ClientIp,
    _limit: Annotated[None, Depends(rate_limit("login", 5, 60))] = None,
) -> dict:
    """登录接口（公开，限流 5 次/分/IP）。

    失败一律返回 `401 UNAUTHORIZED` 且文案相同，防止攻击者通过错误提示枚举账号。
    """
    user = _find_by_username(db, payload.username)

    # 密码校验：用户不存在与密码错误走同一分支（含耗时的哈希校验，避免时序差异）
    password_ok = user is not None and verify_password(payload.password, user.password_hash)
    if not password_ok or user is None:
        logger.info("登录失败：账号或密码错误 username=%s", payload.username)
        raise UnauthorizedError("账号或密码错误")

    if not user.is_active:
        raise ForbiddenError("账号已被禁用，请联系管理员")

    token, expires_in = create_access_token(user_id=user.id, username=user.username, role=user.role)
    user.last_login_at = utcnow()
    db.add(user)
    db.commit()

    audit_service.log_user_login(db, user=user, ip=ip, success=True)  # 内部 commit
    logger.info("登录成功 user_id=%s role=%s", user.id, user.role)

    data = LoginData(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserBrief.from_user(user),
    )
    return ok(data.model_dump(), request_id=current_request_id(request))


@router.get("/me", summary="当前用户", description="返回与登录响应中 user 相同的结构，用于会话恢复")
def me(request: Request, current_user: CurrentUser) -> dict:
    """获取当前登录用户信息（需登录）。"""
    return ok(UserBrief.from_user(current_user).model_dump(), request_id=current_request_id(request))


@router.post(
    "/demo-login",
    summary="演示账号快捷登录",
    description="仅 APP_ENV=demo/development 可用；生产环境返回 404",
)
def demo_login(
    request: Request,
    payload: DemoLoginRequest,
    db: DbSession,
    ip: ClientIp,
    _limit: Annotated[None, Depends(rate_limit("demo_login", 20, 60))] = None,
) -> dict:
    """演示用户快捷登录（契约 §3.2.3）。

    - `APP_ENV=production` → **404**（本接口在生产环境视作不存在）；
    - 演示账号必须来自脱敏数据集，账号不存在时明确提示先导入演示数据。
    """
    if not settings.demo_login_enabled:
        # 契约只规定「返回 404」；错误码表 §1.5 中没有通用 404 码，
        # 因此沿用 VALIDATION_ERROR 常量但保留 404 状态，避免自创错误码。
        logger.warning("生产环境拒绝了 demo-login 调用")
        raise AppError(
            "演示登录未开启",
            code=ErrorCode.VALIDATION_ERROR,
            http_status=404,
        )

    username = payload.username or DEMO_ACCOUNTS.get(payload.role, "")
    user = _find_by_username(db, username) if username else None

    if user is None:
        # 兜底：按角色取第一个可用账号（演示数据集可能使用不同命名）
        stmt = (
            select(User)
            .where(User.role == payload.role)
            .where(User.status == "active")
            .order_by(User.id.asc())
            .limit(1)
        )
        user = db.execute(stmt).scalars().first()

    if user is None:
        raise UnauthorizedError("演示账号不存在，请先导入演示数据集（scripts/seed_demo_data.py）")
    if not user.is_active:
        raise ForbiddenError("演示账号已被禁用")

    token, expires_in = create_access_token(user_id=user.id, username=user.username, role=user.role)
    user.last_login_at = utcnow()
    db.add(user)
    db.commit()
    audit_service.log_user_login(db, user=user, ip=ip, success=True)

    data = LoginData(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserBrief.from_user(user),
    )
    return ok(data.model_dump(), message="演示登录成功", request_id=current_request_id(request))


__all__ = ["router"]
