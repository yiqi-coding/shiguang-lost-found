"""序列化与字段级权限控制（需求 §4.1.2 红线 1 的**唯一收口点**）。

**所有**面向接口的物品/认领输出必须经过这里的函数，禁止在路由里手写 dict 拼装。
这样「普通用户不能读取 hidden_features」这条红线只需要在一个地方保证。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.enums import ItemStatus, UserFacingText
from app.models.claim import Claim
from app.models.item import Item, ItemImage
from app.models.match import Match
from app.models.user import User


def _signed_urls(item: Item) -> list[dict[str, Any]]:
    """生成受控访问的图片 URL 列表。

    ⚠ Baseline 阶段签名 URL 由存储客户端生成；此处只暴露 `url` 与 `is_primary`，
    **不暴露原始对象 Key**（除发布者本人与管理员）。
    """
    from app.services.storage import get_signed_url  # 延迟导入避免循环依赖

    result: list[dict[str, Any]] = []
    for img in item.images or []:
        url = get_signed_url(img.object_key)
        if url:
            result.append({"url": url, "is_primary": bool(img.is_primary)})
    return result


def _image_keys(item: Item) -> list[dict[str, Any]]:
    return [
        {
            "key": img.object_key,
            "mime_type": img.mime_type,
            "size": img.size_bytes,
            "is_primary": bool(img.is_primary),
        }
        for img in item.images or []
    ]


def serialize_item(
    item: Item,
    *,
    viewer: User | None = None,
    detail: bool = True,
) -> dict[str, Any]:
    """物品序列化。

    :param viewer: 当前访问者（None 表示游客）
    :param detail: True=详情视图，False=列表视图（列表更精简且一律不含敏感字段）

    权限矩阵见 docs/api-contract.md §3.5。
    """
    is_admin = bool(viewer and viewer.is_admin)
    is_owner = bool(viewer and item.owner_user_id == viewer.id)
    can_see_private = is_admin or is_owner

    data: dict[str, Any] = {
        "id": item.id,
        "post_type": item.post_type,
        "post_type_text": UserFacingText.POST_TYPE.get(item.post_type, item.post_type),
        "category": item.category,
        "title": item.title,
        "description": item.description,
        "color": item.color,
        "brand": item.brand,
        "location": item.location,
        "event_time": _iso(item.event_time),
        "public_features": item.public_features,
        "ai_status": item.ai_status,
        "status": item.status,
        "status_text": UserFacingText.ITEM_STATUS.get(item.status, item.status),
        "created_at": _iso(item.created_at),
    }

    # ---- 图片：列表只给缩略图；详情给全部；Key 仅私有视角可见 ----
    if detail:
        data["images"] = _signed_urls(item)
        if can_see_private:
            data["image_keys"] = _image_keys(item)
    else:
        primary = item.primary_image
        data["thumbnail_url"] = _signed_url(primary) if primary else None

    # ---- AI 属性：游客只看到公开部分 ----
    if item.ai_attributes:
        if can_see_private:
            data["ai_attributes"] = item.ai_attributes
        else:
            data["ai_attributes"] = {
                "visible_features": item.ai_attributes.get("visible_features") or [],
                "keywords": item.ai_attributes.get("keywords") or [],
                "uncertainties": item.ai_attributes.get("uncertainties") or [],
            }
    else:
        data["ai_attributes"] = None

    # ---- 🔒 敏感字段：仅发布者本人与管理员 ----
    if can_see_private:
        data["hidden_features"] = item.hidden_features
        data["contact_id"] = item.contact_id
        data["owner_user_id"] = item.owner_user_id
        data["attributes_edited_by_user"] = bool(item.attributes_edited_by_user)
        if detail:
            data["ai_model_version"] = item.ai_model_version
            data["ai_prompt_version"] = item.ai_prompt_version
            data["ai_analyzed_at"] = _iso(item.ai_analyzed_at)

    # ---- 管理员额外字段 ----
    if is_admin and detail:
        data["owner"] = item.owner.to_public_dict() if item.owner else None
        data["updated_at"] = _iso(item.updated_at)
        data["allowed_next_statuses"] = _allowed_next(item.status)

    return data


def serialize_item_for_admin(item: Item) -> dict[str, Any]:
    """管理员视角的完整物品信息（审核台用），**不含**数据库内部无关字段。"""
    data = serialize_item(item, viewer=None, detail=True)
    data["hidden_features"] = item.hidden_features
    data["contact_id"] = item.contact_id
    data["owner_user_id"] = item.owner_user_id
    data["owner"] = item.owner.to_public_dict() if item.owner else None
    data["image_keys"] = _image_keys(item)
    data["ai_attributes"] = item.ai_attributes
    data["ai_model_version"] = item.ai_model_version
    data["ai_prompt_version"] = item.ai_prompt_version
    data["ai_analyzed_at"] = _iso(item.ai_analyzed_at)
    data["attributes_edited_by_user"] = bool(item.attributes_edited_by_user)
    data["updated_at"] = _iso(item.updated_at)
    return data


def serialize_match(match: Match, *, viewer: User | None = None, include_match_id: bool = True) -> dict[str, Any]:
    """匹配结果序列化（含可解释性与降级标识）。"""
    from app.services.scoring import match_level_for

    data: dict[str, Any] = {
        "total_score": float(match.total_score or 0),
        "score_detail": match.score_detail(),
        "evidence": match.evidence or [],
        "uncertainties": match.uncertainties or [],
        "risk_tips": match.risk_tips or [],
        "match_level": match_level_for(float(match.total_score or 0)),
        "model_version": match.model_version,
        "degraded": bool(match.is_degraded),
        "computed_at": _iso(match.updated_at or match.created_at),
    }
    if include_match_id:
        data["match_id"] = match.id
    return data


def serialize_match_candidate(match: Match, *, viewer: User | None = None) -> dict[str, Any]:
    """匹配候选（用于 GET /items/{id}/matches 的 results 元素）。"""
    data = serialize_match(match, viewer=viewer)
    data["candidate_item"] = serialize_item(match.found_item, viewer=viewer, detail=False)
    return data


def serialize_claim_for_applicant(claim: Claim, *, match: Match | None = None) -> dict[str, Any]:
    """认领者视角。

    红线：**绝不包含** answers_hash、answer_summary 中的答案线索、标准答案。
    """
    data: dict[str, Any] = {
        "claim_id": claim.id,
        "match_id": claim.match_id,
        "status": claim.status,
        "questions": claim.questions_for_applicant(),
        "attempts_used": claim.attempts_used,
        "max_attempts": claim.max_attempts,
        "attempts_left": claim.attempts_left,
        "created_at": _iso(claim.created_at),
    }
    if claim.verification_score is not None:
        data["verification_score"] = float(claim.verification_score)
    if claim.credibility_score is not None:
        data["credibility_score"] = float(claim.credibility_score)
    if claim.risk_level:
        data["risk_level"] = claim.risk_level
        data["risk_level_text"] = UserFacingText.RISK_LEVEL.get(claim.risk_level, claim.risk_level)
    if claim.risk_tips:
        data["risk_tips"] = claim.risk_tips
    if claim.answer_summary:
        # 只保留「是否命中 + 分数 + 理由」，理由由后端生成且不含答案原文
        data["per_question"] = [
            {
                "question_id": item.get("question_id"),
                "matched": bool(item.get("matched")),
                "score": float(item.get("score") or 0),
                "reason": item.get("reason") or "",
            }
            for item in claim.answer_summary
        ]
    if claim.admin_comment:
        data["admin_comment"] = claim.admin_comment
    if claim.reviewed_at:
        data["reviewed_at"] = _iso(claim.reviewed_at)
    return data


def serialize_claim_for_admin(claim: Claim) -> dict[str, Any]:
    """管理员审核台视角：含题目与命中情况，**仍不含答案原文**。"""
    data = serialize_claim_for_applicant(claim)
    data["questions"] = [
        {
            "question_id": q.get("question_id"),
            "question_text": q.get("question_text"),
            "source": q.get("source"),
        }
        for q in (claim.questions or [])
    ]
    if claim.answer_summary:
        data["per_question"] = [
            {
                "question_id": item.get("question_id"),
                "question_text": _question_text(claim, str(item.get("question_id"))),
                "matched": bool(item.get("matched")),
                "score": float(item.get("score") or 0),
                "reason": item.get("reason") or "",
            }
            for item in claim.answer_summary
        ]
    data["applicant"] = claim.applicant.to_public_dict() if claim.applicant else None
    data["reviewer"] = claim.reviewer.to_public_dict() if claim.reviewer else None
    return data


def serialize_audit_log(log: Any, *, operator_username: str | None = None) -> dict[str, Any]:
    return log.to_public_dict(operator_username=operator_username)


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------


def _question_text(claim: Claim, question_id: str) -> str:
    for q in claim.questions or []:
        if str(q.get("question_id")) == question_id:
            return str(q.get("question_text", ""))
    return ""


def _signed_url(image: ItemImage | None) -> str | None:
    if image is None:
        return None
    from app.services.storage import get_signed_url

    return get_signed_url(image.object_key)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _allowed_next(status: str) -> list[str]:
    from app.services.state_machine import allowed_next_statuses

    if status == ItemStatus.CLOSED:
        return []
    return allowed_next_statuses(status)


# ----------------------------------------------------------------------
# 自检（供测试调用）
# ----------------------------------------------------------------------

FORBIDDEN_KEYS_FOR_PUBLIC = {
    "hidden_features",
    "contact_id",
    "owner_user_id",
    "image_keys",
    "password_hash",
}

FORBIDDEN_KEYS_FOR_APPLICANT = {
    "answers_hash",
    "answers_plain",
    "standard_answer",
    "standard_answers",
}


def assert_no_sensitive_leak(payload: dict[str, Any], *, allow_private: bool = False) -> None:
    """测试断言辅助：确保公共响应体中没有敏感字段。"""
    leaked = FORBIDDEN_KEYS_FOR_PUBLIC & set(payload.keys())
    if leaked and not allow_private:
        raise AssertionError(f"响应体泄露敏感字段：{sorted(leaked)}")


def assert_no_answer_leak(payload: dict[str, Any]) -> None:
    """测试断言辅助：确保认领响应体中没有答案字段。"""
    leaked = FORBIDDEN_KEYS_FOR_APPLICANT & set(payload.keys())
    if leaked:
        raise AssertionError(f"认领响应体泄露答案字段：{sorted(leaked)}")
