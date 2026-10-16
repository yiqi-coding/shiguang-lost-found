"""业务枚举字典。

**本文件是 API 契约 §2「枚举字典」在代码中的唯一落点。**
任何枚举变更必须同步更新 docs/api-contract.md 并登记变更记录。
"""

from __future__ import annotations

from enum import StrEnum


class PostType(StrEnum):
    """物品发布类型。"""

    LOST = "lost"
    FOUND = "found"


class Category(StrEnum):
    """Baseline 支持的物品类别。"""

    EARPHONE = "无线耳机"
    CUP = "水杯"
    BOOK = "书籍"
    CARD = "校园卡"
    UMBRELLA = "雨伞"
    BAG = "背包"
    OTHER = "其他"


class ItemStatus(StrEnum):
    """物品业务状态（API 契约 §2.3）。"""

    PUBLISHED = "published"
    MATCHED = "matched"
    CLAIMING = "claiming"
    PENDING_REVIEW = "pending_review"
    REQUEST_MORE_INFO = "request_more_info"
    CLAIMED = "claimed"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    DISPUTED = "disputed"
    CLOSED = "closed"


class ClaimStatus(StrEnum):
    """认领状态（API 契约 §2.4）。"""

    CLAIMING = "claiming"
    PENDING_REVIEW = "pending_review"
    REQUEST_MORE_INFO = "request_more_info"
    APPROVED = "approved"
    REJECTED = "rejected"


class AIStatus(StrEnum):
    """AI 属性提取状态。"""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class RiskLevel(StrEnum):
    """风险等级；阈值来自配置，不得硬编码。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UserRole(StrEnum):
    STUDENT = "student"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ReviewAction(StrEnum):
    """管理员审核动作（API 契约 §2.7）。"""

    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_MORE_INFO = "request_more_info"


class MatchLevel(StrEnum):
    """匹配等级；阈值来自配置。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AuditAction(StrEnum):
    """审计日志动作（API 契约 §2.8）。"""

    ITEM_CREATE = "item.create"
    ITEM_UPDATE = "item.update"
    ITEM_STATUS_CHANGE = "item.status_change"
    MATCH_COMPUTE = "match.compute"
    CLAIM_CREATE = "claim.create"
    CLAIM_VERIFY = "claim.verify"
    CLAIM_VERIFY_FAILED = "claim.verify_failed"
    CLAIM_REVIEW = "claim.review"
    USER_LOGIN = "user.login"
    USER_DISABLE = "user.disable"
    CONFIG_UPDATE = "config.update"


class AuditTargetType(StrEnum):
    ITEM = "item"
    CLAIM = "claim"
    USER = "user"
    CONFIG = "config"


class HiddenFeatureType(StrEnum):
    """隐藏特征类型（原型 §4 下拉选项）。"""

    APPEARANCE_MARK = "外观痕迹"
    STICKER = "个性贴纸"
    DEVICE_NAME = "设备名称"
    INNER_CONTENT = "内部内容"
    WEAR = "特殊磨损"
    MISSING_ACCESSORY = "配件缺失"


class UserFacingText:
    """前端展示用中文文案映射（与 frontend StatusTag.vue 保持一致）。"""

    ITEM_STATUS: dict[str, str] = {
        ItemStatus.PUBLISHED: "已发布",
        ItemStatus.MATCHED: "已匹配",
        ItemStatus.CLAIMING: "认领中",
        ItemStatus.PENDING_REVIEW: "待审核",
        ItemStatus.REQUEST_MORE_INFO: "待补充信息",
        ItemStatus.CLAIMED: "已认领",
        ItemStatus.REJECTED: "已驳回",
        ItemStatus.SUSPENDED: "已暂停",
        ItemStatus.DISPUTED: "争议中",
        ItemStatus.CLOSED: "已关闭",
    }

    CLAIM_STATUS: dict[str, str] = {
        ClaimStatus.CLAIMING: "认领中",
        ClaimStatus.PENDING_REVIEW: "待审核",
        ClaimStatus.REQUEST_MORE_INFO: "待补充信息",
        ClaimStatus.APPROVED: "已通过",
        ClaimStatus.REJECTED: "已驳回",
    }

    POST_TYPE: dict[str, str] = {
        PostType.LOST: "失物",
        PostType.FOUND: "拾物",
    }

    RISK_LEVEL: dict[str, str] = {
        RiskLevel.LOW: "低风险",
        RiskLevel.MEDIUM: "中风险",
        RiskLevel.HIGH: "高风险",
    }
