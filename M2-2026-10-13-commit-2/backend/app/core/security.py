"""安全工具：密码哈希、JWT、数据脱敏、隐藏特征哈希。

红线：
- 密码使用 bcrypt（禁止 MD5/SHA1 裸哈希）；
- 密钥只从环境变量读取；
- 答案哈希必须**加盐**（盐由 APP_SECRET_KEY 派生），防止彩虹表反查。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import settings
from app.core.errors import UnauthorizedError

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_SECONDS = 7200  # 2 小时，与 API 契约 §1.1 一致

# ----------------------------------------------------------------------
# 密码
# ----------------------------------------------------------------------
#
# 为什么直接使用 `bcrypt` 库而不是 `passlib`：
#   passlib 1.7.4（当前最新版，2020 年后未再更新）通过读取
#   `bcrypt.__about__.__version__` 探测后端版本，而 bcrypt 4.1 起移除了
#   `__about__`。结果是 passlib 在 bcrypt>=4.1 环境下**无法工作**：
#     - 哈希时先报 `(trapped) error reading bcrypt version`，随后 ValueError；
#     - 新版 bcrypt 对 >72 字节口令直接抛错，passlib 也不做截断。
#   实测（bcrypt 5.0.0 / passlib 1.7.4）哈希与校验全部抛异常，会让登录接口
#   直接 500 —— 属于使系统不可用的真实缺陷，而非风格问题。
#
#   因此改为直接调用 bcrypt：它自带 `$2b$` 盐、恒定时间比较，满足
#   「密码哈希禁止 MD5/SHA1」的要求（需求 §10），且少一层不可靠的封装。
#
# 兼容性：库里既有的 `$2a$` / `$2b$` / `$2y$` 哈希仍可正常校验，
#         本改动不会使已注册用户的密码失效。
#
# bcrypt 算法上限为 72 字节，超出部分会被静默忽略；这里显式截断，避免
# 「超长口令等价于其前 72 字节」这种隐式行为。
_BCRYPT_MAX_BYTES = 72
_BCRYPT_ROUNDS = 12

try:
    import bcrypt as _bcrypt
except ImportError:  # pragma: no cover - 依赖缺失时给出明确指引
    _bcrypt = None  # type: ignore[assignment]


def _require_bcrypt() -> Any:
    if _bcrypt is None:  # pragma: no cover
        raise RuntimeError(
            "未安装 bcrypt，无法进行密码哈希。请执行 `pip install bcrypt`（见 backend/requirements.txt）。"
        )
    return _bcrypt


def _to_bcrypt_bytes(plain: str) -> bytes:
    """把口令编码为 bcrypt 可接受的字节串（超出 72 字节时显式截断）。"""
    raw = plain.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX_BYTES:
        return raw
    # 按字节截断可能切裂多字节字符，逐字节回退保证不产生非法 UTF-8 序列
    truncated = raw[:_BCRYPT_MAX_BYTES]
    while truncated:
        try:
            truncated.decode("utf-8")
            break
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return truncated


def hash_password(plain: str) -> str:
    """生成 bcrypt 哈希（`$2b$...`），可直接存入 `users.password_hash`。"""
    bcrypt = _require_bcrypt()
    return bcrypt.hashpw(_to_bcrypt_bytes(plain), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str | None) -> bool:
    """校验口令。哈希缺失或格式异常一律返回 False（绝不抛异常）。"""
    if not hashed:
        return False
    bcrypt = _require_bcrypt()
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(plain), hashed.encode("ascii"))
    except Exception:  # noqa: BLE001 - 哈希格式异常一律视为验证失败
        return False


# ----------------------------------------------------------------------
# JWT
# ----------------------------------------------------------------------


def create_access_token(*, user_id: int, username: str, role: str) -> tuple[str, int]:
    """返回 (token, expires_in_seconds)。"""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_EXPIRE_SECONDS)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, JWT_EXPIRE_SECONDS


def decode_access_token(token: str) -> dict[str, Any]:
    """解码并校验 JWT；失败一律抛 UnauthorizedError（不暴露具体原因）。"""
    try:
        return jwt.decode(token, settings.APP_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("登录凭证无效") from exc


# ----------------------------------------------------------------------
# 脱敏
# ----------------------------------------------------------------------

_PHONE_RE = re.compile(r"\b(1[3-9]\d)(\d{4})(\d{4})\b")
_EMAIL_RE = re.compile(r"\b([\w.+-]{1,3})[\w.+-]*(@[\w-]+\.[\w.]+)\b")
_ID_CARD_RE = re.compile(r"\b(\d{4})\d{10}(\w{4})\b")


def mask_contact(value: str | None) -> str | None:
    """联系方式脱敏：手机号保留前 3 后 4，邮箱保留前 3 位与域名。"""
    if not value:
        return None
    masked = _PHONE_RE.sub(r"\1****\3", value)
    masked = _EMAIL_RE.sub(r"\1***\2", masked)
    masked = _ID_CARD_RE.sub(r"\1**********\2", masked)
    return masked


def mask_ip(ip: str | None) -> str | None:
    """IP 脱敏：IPv4 保留前两段。"""
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return "***"


# ----------------------------------------------------------------------
# 隐藏特征答案哈希（加盐）
# ----------------------------------------------------------------------

_SALT_CONTEXT = b"shiguang:claim-answer:v1"


def _salt() -> bytes:
    """从应用密钥派生盐值。密钥变更会导致旧哈希失效（属于预期行为，需公告）。"""
    return hmac.new(settings.APP_SECRET_KEY.encode("utf-8"), _SALT_CONTEXT, hashlib.sha256).digest()


def hash_answer(answer: str) -> str:
    """对答案做加盐哈希。返回 `sha256:<hex>` 格式，便于未来算法升级时区分版本。"""
    normalized = normalize_answer(answer)
    digest = hmac.new(_salt(), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256:{digest}"


def verify_answer(answer: str, stored_hash: str | None) -> bool:
    """常量时间比对，防时序侧信道。"""
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_answer(answer), stored_hash)


def normalize_answer(text: str) -> str:
    """答案规范化：去首尾空白、全角转半角、统一小写、压缩连续空白。

    用于「关键字段精确匹配」（如蓝牙设备名称 `YiQi-AirPods` 与 `yiqi airpods`）。
    ⚠ 本函数**不得**写入日志。
    """
    if not text:
        return ""
    # 全角 → 半角
    result_chars: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII
            code -= 0xFEE0
        result_chars.append(chr(code))
    normalized = "".join(result_chars).strip().lower()
    normalized = re.sub(r"[\s\-_·．.]+", "", normalized)
    return normalized


# ----------------------------------------------------------------------
# 其他
# ----------------------------------------------------------------------


def generate_object_key(prefix: str, ext: str, *, now: datetime | None = None) -> str:
    """生成对象存储 Key：`{prefix}/{yyyy}/{mm}/{uuid}.{ext}`。

    **禁止使用原始文件名**（需求 §4.2.4），避免路径穿越与隐私泄露。
    """
    now = now or datetime.now(timezone.utc)
    safe_ext = re.sub(r"[^a-z0-9]", "", (ext or "").lower().lstrip(".")) or "bin"
    return f"{prefix}/{now.year:04d}/{now.month:02d}/{secrets.token_hex(16)}.{safe_ext}"


def safe_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
