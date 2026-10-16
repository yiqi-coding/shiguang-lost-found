"""应用配置。

所有配置项来自环境变量（或 .env 文件），**严禁**把密钥写死在代码里。
生产环境启动时会执行严格校验（见 `validate_runtime`）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDERS = {"", "replace_me", "changeme", "todo", "none", "null"}


class Settings(BaseSettings):
    """全局配置单例。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    APP_ENV: Literal["development", "demo", "production"] = "development"
    APP_NAME: str = "shiguang"
    APP_VERSION: str = "1.0.0"
    APP_SECRET_KEY: str = "dev_only_secret_key_change_me_32b"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173"

    # ---------- 数据库 ----------
    DATABASE_URL: str = "mysql+pymysql://root:root@localhost:3306/shiguang?charset=utf8mb4"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False
    DB_POOL_RECYCLE: int = 3600  # 小于 RDS 的 wait_timeout，避免 stale connection

    # ---------- Redis ----------
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = True
    REDIS_TTL_AI_ATTRIBUTES: int = 86_400
    REDIS_TTL_MATCH: int = 600
    REDIS_TTL_CLAIM_SESSION: int = 1_800

    # ---------- 对象存储 ----------
    STORAGE_BACKEND: Literal["local", "obs"] = "local"
    LOCAL_STORAGE_DIR: str = "./storage"
    OBS_ENDPOINT: str = ""
    OBS_REGION: str = ""
    OBS_BUCKET: str = ""
    OBS_ACCESS_KEY: str = ""
    OBS_SECRET_KEY: str = ""
    OBS_SIGNED_URL_EXPIRE: int = 900
    OBS_KEY_PREFIX: str = "items"

    # ---------- MaaS ----------
    MAAS_ENDPOINT: str = ""
    MAAS_API_KEY: str = ""
    MAAS_MODEL: str = ""
    MAAS_TIMEOUT: int = 30
    MAAS_ENABLED: bool = False

    # ---------- 算法服务 ----------
    ALGORITHM_SERVICE_URL: str = "http://localhost:9000"
    ALGORITHM_TIMEOUT: int = 30
    ALGORITHM_FALLBACK_ENABLED: bool = True

    # ---------- 业务参数 ----------
    MATCH_TOP_K: int = 5
    MATCH_MIN_DISPLAY_SCORE: float = 0.35
    MATCH_CACHE_ENABLED: bool = True
    CLAIM_MAX_ATTEMPTS: int = 3
    UPLOAD_MAX_SIZE_MB: int = 10
    UPLOAD_MAX_COUNT: int = 3
    PAGE_SIZE_MAX: int = 50

    # ---------- 匹配权重（可由 config/matching.yaml 覆盖，此处为兜底默认值） ----------
    MATCH_WEIGHT_CATEGORY: float = 0.20
    MATCH_WEIGHT_ATTRIBUTE: float = 0.25
    MATCH_WEIGHT_IMAGE: float = 0.20
    MATCH_WEIGHT_TIME: float = 0.15
    MATCH_WEIGHT_LOCATION: float = 0.15
    MATCH_WEIGHT_BRAND_MODEL: float = 0.05

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def demo_login_enabled(self) -> bool:
        """演示账号快捷登录仅在非生产环境开放。"""
        return self.APP_ENV in {"development", "demo"}

    @property
    def upload_max_bytes(self) -> int:
        return self.UPLOAD_MAX_SIZE_MB * 1024 * 1024

    @property
    def match_weights(self) -> dict[str, float]:
        """六维权重字典；总和必须为 1.0（有单元测试断言）。"""
        return {
            "category": self.MATCH_WEIGHT_CATEGORY,
            "attribute": self.MATCH_WEIGHT_ATTRIBUTE,
            "image": self.MATCH_WEIGHT_IMAGE,
            "time": self.MATCH_WEIGHT_TIME,
            "location": self.MATCH_WEIGHT_LOCATION,
            "brand_model": self.MATCH_WEIGHT_BRAND_MODEL,
        }

    # ------------------------------------------------------------------
    # 启动期校验
    # ------------------------------------------------------------------
    def missing_required_in_production(self) -> list[str]:
        """返回生产环境缺失或仍为占位符的必填项名称（**只返回变量名，不返回值**）。"""
        required = [
            "APP_SECRET_KEY",
            "DATABASE_URL",
            "REDIS_URL",
            "OBS_ENDPOINT",
            "OBS_BUCKET",
            "OBS_ACCESS_KEY",
            "OBS_SECRET_KEY",
        ]
        missing: list[str] = []
        for name in required:
            value = str(getattr(self, name, "") or "").strip().lower()
            if value in _PLACEHOLDERS:
                missing.append(name)
        if len(self.APP_SECRET_KEY) < 32:
            missing.append("APP_SECRET_KEY(长度需≥32)")
        return missing

    def health_snapshot(self) -> dict[str, object]:
        """用于 /health 的非敏感配置快照。**绝不包含密钥**。"""
        return {
            "env": self.APP_ENV,
            "version": self.APP_VERSION,
            "storage_backend": self.STORAGE_BACKEND,
            "redis_enabled": self.REDIS_ENABLED,
            "maas_configured": bool(self.MAAS_ENDPOINT and self.MAAS_API_KEY and self.MAAS_ENABLED),
            "database_driver": self.DATABASE_URL.split("://", 1)[0] if "://" in self.DATABASE_URL else "unknown",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
