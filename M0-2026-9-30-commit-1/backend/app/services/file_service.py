"""上传文件校验（需求 §4.2.4）。

校验顺序（**全部在服务端执行，不可仅靠前端**）：
  1. 扩展名白名单
  2. 声明 Content-Type
  3. 魔数（MIME sniffing）
  4. 文件大小
  5. 图片可解析性（Pillow verify）
  6. 拒绝 HTML / SVG / 可执行文件

任何一步失败都抛 `ValidationError`（HTTP 400/413），**绝不返回 500**。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from app.core.config import settings
from app.core.errors import ValidationError

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}

# 文件头魔数（防止改扩展名绕过）
MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),  # WEBP 需要进一步校验第 8-12 字节为 WEBP
]

# 明确拒绝的内容特征
DANGEROUS_SIGNATURES: list[tuple[bytes, str]] = [
    (b"<svg", "SVG 文件"),
    (b"<?xml", "XML/SVG 文件"),
    (b"<html", "HTML 文件"),
    (b"<!DOCTYPE", "HTML 文件"),
    (b"<script", "脚本内容"),
    (b"MZ", "Windows 可执行文件"),
    (b"\x7fELF", "Linux 可执行文件"),
    (b"PK\x03\x04", "压缩包（可能是 Office 文档）"),
    (b"%PDF", "PDF 文件"),
]


@dataclass
class ValidatedImage:
    """校验通过的图片。"""

    data: bytes
    ext: str
    mime_type: str
    size: int
    width: int | None = None
    height: int | None = None


def _detect_mime(data: bytes) -> str | None:
    """通过魔数嗅探真实 MIME 类型。"""
    for sig, mime in MAGIC_SIGNATURES:
        if data.startswith(sig):
            if mime == "image/webp":
                # RIFF....WEBP
                if len(data) >= 12 and data[8:12] == b"WEBP":
                    return "image/webp"
                return None
            return mime
    return None


def validate_image(
    data: bytes,
    *,
    filename: str | None = None,
    declared_mime: str | None = None,
    max_bytes: int | None = None,
) -> ValidatedImage:
    """完整校验一张上传图片。校验失败抛 ValidationError。"""
    max_bytes = max_bytes or settings.upload_max_bytes

    # 1) 非空
    if not data:
        raise ValidationError("上传的图片内容为空")

    # 2) 大小（先查大小，避免对大文件做解析）
    if len(data) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise ValidationError(
            f"图片大小超过限制（最大 {limit_mb}MB）",
            detail=[{"field": "image", "reason": f"实际 {len(data) / 1024 / 1024:.1f}MB"}],
        )

    # 3) 危险内容直接拒绝
    head = data[:32].lower()
    for sig, label in DANGEROUS_SIGNATURES:
        if head.startswith(sig.lower()):
            raise ValidationError(f"不允许上传{label}")

    # 4) 扩展名白名单
    ext = ""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower().strip()
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "仅支持 JPG / PNG / WEBP 格式",
            detail=[{"field": "image", "reason": f"不支持的扩展名：.{ext}"}],
        )

    # 5) 声明 MIME 校验（客户端可伪造，仅作辅助判断）
    if declared_mime and declared_mime.lower() not in ALLOWED_MIME_TYPES:
        raise ValidationError(
            "仅支持 JPG / PNG / WEBP 格式",
            detail=[{"field": "image", "reason": f"不支持的类型：{declared_mime}"}],
        )

    # 6) 魔数嗅探（真实类型）
    detected = _detect_mime(data)
    if detected is None:
        raise ValidationError("无法识别的图片格式，请上传 JPG / PNG / WEBP 图片")

    # 7) 扩展名与真实类型一致性
    if ext:
        normalized_ext = "jpg" if ext == "jpeg" else ext
        expected_mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[normalized_ext]
        if expected_mime != detected:
            raise ValidationError(
                "文件扩展名与实际内容不符",
                detail=[{"field": "image", "reason": f"扩展名 .{ext} 但内容是 {detected}"}],
            )
    else:
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[detected]

    # 8) 可解析性校验（剥离 EXIF、确认不是伪装文件）
    width = height = None
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(io.BytesIO(data)) as img:
            img.verify()  # 校验完整性
        # verify() 后对象不可复用，重新打开读取尺寸
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width < 32 or height < 32:
                raise ValidationError("图片尺寸过小（至少 32×32 像素）")
            if width * height > 40_000_000:
                raise ValidationError("图片分辨率过大，请压缩后重试")
    except ValidationError:
        raise
    except UnidentifiedImageError as exc:
        raise ValidationError("图片内容已损坏或不是有效图片") from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("图片解析失败：%s", type(exc).__name__)
        raise ValidationError("图片内容已损坏，无法解析") from exc

    return ValidatedImage(
        data=data, ext=ext, mime_type=detected, size=len(data), width=width, height=height
    )


def validate_image_count(count: int) -> None:
    """校验单条记录的图片数量。"""
    if count > settings.UPLOAD_MAX_COUNT:
        raise ValidationError(
            f"每条记录最多上传 {settings.UPLOAD_MAX_COUNT} 张图片",
            detail=[{"field": "image", "reason": f"当前 {count} 张"}],
        )


def sniff_is_image(data: bytes) -> bool:
    """轻量判断：仅用于快速过滤，不作为最终校验。"""
    return _detect_mime(data) is not None
