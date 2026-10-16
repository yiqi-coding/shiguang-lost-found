"""对象存储抽象层（本地目录 / 华为云 OBS）。

设计要点：
- 后端**只依赖本模块的接口**，切换存储后端只改 `STORAGE_BACKEND` 配置，不改业务代码；
- 图片 URL 一律使用**签名 URL**（默认 15 分钟），私有桶不对外开放（需求 §4.2.4）；
- 对象 Key 规则：`items/{yyyy}/{mm}/{uuid}.{ext}`，**禁止使用原始文件名**。
"""

from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings
from app.core.errors import StorageServiceError
from app.core.security import generate_object_key

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """存储后端接口。新增后端（如 S3、MinIO）只需实现本类。"""

    @abstractmethod
    def put(self, object_key: str, data: bytes, content_type: str) -> str:
        """上传对象，返回 object_key。"""

    @abstractmethod
    def get_signed_url(self, object_key: str, expires: int | None = None) -> str | None:
        """获取受控访问 URL。"""

    @abstractmethod
    def delete(self, object_key: str) -> bool:
        ...

    @abstractmethod
    def exists(self, object_key: str) -> bool:
        ...

    @abstractmethod
    def check_health(self) -> dict:
        ...

    # ------------------------------------------------------------------
    def build_key(self, ext: str) -> str:
        return generate_object_key(settings.OBS_KEY_PREFIX, ext)


class LocalStorageBackend(StorageBackend):
    """本地文件系统后端（开发与联调使用）。

    ⚠ 生产环境必须切换为 OBS，否则不满足需求 §13.1 F03 的验收要求。
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.LOCAL_STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.public_prefix = "/static/objects"

    def _path(self, object_key: str) -> Path:
        # 防路径穿越：解析后必须仍位于 base_dir 内
        target = (self.base_dir / object_key).resolve()
        if not str(target).startswith(str(self.base_dir)):
            raise StorageServiceError("非法的对象路径")
        return target

    def put(self, object_key: str, data: bytes, content_type: str) -> str:
        try:
            path = self._path(object_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return object_key
        except StorageServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("本地存储写入失败：%s", type(exc).__name__)
            raise StorageServiceError("图片保存失败，请稍后重试") from exc

    def get_signed_url(self, object_key: str, expires: int | None = None) -> str | None:
        if not object_key:
            return None
        # 本地开发直接暴露静态路径；生产环境 OBS 后端走签名 URL
        return f"{self.public_prefix}/{object_key}"

    def delete(self, object_key: str) -> bool:
        try:
            self._path(object_key).unlink(missing_ok=True)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("本地存储删除失败：%s", type(exc).__name__)
            return False

    def exists(self, object_key: str) -> bool:
        try:
            return self._path(object_key).is_file()
        except Exception:  # noqa: BLE001
            return False

    def check_health(self) -> dict:
        try:
            probe = self.base_dir / ".health"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return {"status": "ok", "backend": "local", "writable": True}
        except Exception as exc:  # noqa: BLE001
            return {"status": "down", "backend": "local", "error": type(exc).__name__}


class ObsStorageBackend(StorageBackend):
    """华为云 OBS 后端。

    依赖 `esdk-obs-python`。桶必须为**私有**，所有访问走签名 URL。
    """

    def __init__(self) -> None:
        try:
            from obs import ObsClient  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 依赖未装时给出清晰指引
            raise StorageServiceError("未安装华为云 OBS SDK，请执行 pip install esdk-obs-python") from exc

        missing = [
            name
            for name in ("OBS_ENDPOINT", "OBS_BUCKET", "OBS_ACCESS_KEY", "OBS_SECRET_KEY")
            if not getattr(settings, name)
        ]
        if missing:
            raise StorageServiceError(f"OBS 配置缺失：{', '.join(missing)}")

        self._client = ObsClient(
            access_key_id=settings.OBS_ACCESS_KEY,
            secret_access_key=settings.OBS_SECRET_KEY,
            server=settings.OBS_ENDPOINT,
        )
        self.bucket = settings.OBS_BUCKET

    def put(self, object_key: str, data: bytes, content_type: str) -> str:
        try:
            resp = self._client.putContent(
                self.bucket, object_key, content=data, headers={"Content-Type": content_type}
            )
            if resp.status >= 300:
                raise StorageServiceError(f"OBS 上传失败（HTTP {resp.status}）")
            return object_key
        except StorageServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("OBS 上传异常：%s", type(exc).__name__)
            raise StorageServiceError("图片上传到云端失败，请稍后重试") from exc

    def get_signed_url(self, object_key: str, expires: int | None = None) -> str | None:
        if not object_key:
            return None
        try:
            ttl = expires or settings.OBS_SIGNED_URL_EXPIRE
            return self._client.createSignedUrl("GET", self.bucket, object_key, expires=ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OBS 签名 URL 生成失败：%s", type(exc).__name__)
            return None

    def delete(self, object_key: str) -> bool:
        try:
            resp = self._client.deleteObject(self.bucket, object_key)
            return resp.status < 300
        except Exception as exc:  # noqa: BLE001
            logger.warning("OBS 删除失败：%s", type(exc).__name__)
            return False

    def exists(self, object_key: str) -> bool:
        try:
            resp = self._client.headObject(self.bucket, object_key)
            return resp.status < 300
        except Exception:  # noqa: BLE001
            return False

    def check_health(self) -> dict:
        def _mask(value: str) -> str:
            return f"{value[:4]}****" if value else "***"

        try:
            resp = self._client.headBucket(self.bucket)
            ok = resp.status < 300
            return {
                "status": "ok" if ok else "down",
                "backend": "obs",
                "bucket": _mask(self.bucket),
                "endpoint_masked": _mask(settings.OBS_ENDPOINT),
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "down", "backend": "obs", "error": type(exc).__name__}


# ----------------------------------------------------------------------
# 单例与便捷函数
# ----------------------------------------------------------------------

_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        if settings.STORAGE_BACKEND == "obs":
            _backend = ObsStorageBackend()
        else:
            _backend = LocalStorageBackend()
    return _backend


def reset_storage() -> None:
    """测试用：重置后端单例。"""
    global _backend
    _backend = None


def get_signed_url(object_key: str | None, expires: int | None = None) -> str | None:
    """全局便捷函数：由 object_key 生成受控访问 URL。

    存储不可用时返回 None（页面降级为占位图），**不抛异常**，避免详情页整体失败。
    """
    if not object_key:
        return None
    try:
        return get_storage().get_signed_url(object_key, expires)
    except Exception as exc:  # noqa: BLE001
        logger.warning("生成签名 URL 失败，降级为无图：%s", type(exc).__name__)
        return None


def save_image(data: bytes, ext: str, content_type: str) -> str:
    """保存图片，返回 object_key。失败抛 StorageServiceError（由路由决定是否阻断发布）。"""
    backend = get_storage()
    key = backend.build_key(ext)
    return backend.put(key, data, content_type)


def copy_static_assets(src: str, dst: str) -> None:
    """辅助函数：演示环境初始化时拷贝静态占位图。"""
    shutil.copytree(src, dst, dirs_exist_ok=True)


def local_static_dir() -> str:
    return os.path.abspath(settings.LOCAL_STORAGE_DIR)
