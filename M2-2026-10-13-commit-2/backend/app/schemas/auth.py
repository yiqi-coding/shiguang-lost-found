"""认证相关 Schema（契约 §3.2）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import UserRole

USERNAME_MAX = 64
PASSWORD_MIN = 6
PASSWORD_MAX = 128


class LoginRequest(BaseModel):
    """登录请求。

    契约 §3.2.1：`{ "username": "demo_student", "password": "******" }`。
    账号或密码错误一律返回 `401 UNAUTHORIZED` 且**不区分提示**（防用户枚举）。
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    username: str = Field(..., min_length=1, max_length=USERNAME_MAX, description="账号/昵称")
    password: str = Field(..., min_length=PASSWORD_MIN, max_length=PASSWORD_MAX, description="密码")

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        if not value:
            raise ValueError("账号不能为空")
        return value

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        # 密码不做 strip（前后空格可能是密码的一部分），只校验长度
        if len(value) < PASSWORD_MIN:
            raise ValueError(f"密码长度不能少于 {PASSWORD_MIN} 位")
        return value


class DemoLoginRequest(BaseModel):
    """演示账号快捷登录请求（契约 §3.2.3，仅 `APP_ENV=demo` 可用）。

    `role=student` 可对应「失主 / 拾到者」两类演示账号，可用 `username` 精确指定。
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    role: Literal["student", "admin"] = Field("student", description="演示身份")
    username: str | None = Field(
        None, max_length=USERNAME_MAX, description="可选：指定演示账号（如 demo_finder）"
    )


class UserBrief(BaseModel):
    """对外用户摘要（契约 §3.2.1 的 `user` 字段）。**绝不包含 `password_hash`。**"""

    model_config = ConfigDict(extra="ignore")

    id: int
    username: str
    role: str
    campus_id: str | None = None
    contact_id: str | None = Field(None, description="**已脱敏**联系方式，如 138****8888")

    @classmethod
    def from_user(cls, user: Any) -> "UserBrief":
        """由 `User` 模型构造（复用 `User.to_public_dict()`，避免手写字段）。"""
        return cls(**user.to_public_dict())


class LoginData(BaseModel):
    """登录成功后的 `data` 结构（契约 §3.2.1）。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="有效期（秒），契约规定 2 小时")
    user: UserBrief


__all__ = [
    "USERNAME_MAX",
    "PASSWORD_MIN",
    "PASSWORD_MAX",
    "LoginRequest",
    "DemoLoginRequest",
    "UserBrief",
    "LoginData",
    "UserRole",
]
