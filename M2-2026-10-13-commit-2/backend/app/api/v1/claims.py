"""认领路由（契约 §3.8 / §3.9）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/claims` | 登录（拾物方/管理员） | 201，返回 2~3 个验证问题，**绝不返回答案** |
| `POST /api/v1/claims/{claim_id}/verify` | 登录（申请人本人/管理员） | 200，返回得分与命中情况，**reason 不回显答案** |

红线：

- 响应体一律由 `serializers.serialize_claim_for_applicant()` 产出，
  该函数只暴露 `question_id` / `question_text` / `matched` / `score` / `reason`；
- 验证通过后状态只能进入 `pending_review`，**绝不自动 `claimed`**（`claimed` 只能由管理员审核产生）。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUser, DbSession, current_request_id, get_client_ip, rate_limit
from app.core.response import ok
from app.schemas.claim import ClaimCreateRequest, ClaimVerifyRequest
from app.services import claim_service, serializers
from app.services.claim_service import get_claim_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/claims", tags=["认领"])

ClientIp = Annotated[str, Depends(get_client_ip)]


@router.post(
    "",
    status_code=201,
    summary="发起认领",
    description="仅匹配中拾物方（found 记录发布者）或管理员可发起；限流 10 次/时/用户",
)
def create_claim(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    payload: ClaimCreateRequest,
    _limit: Annotated[None, Depends(rate_limit("claim_create", 10, 3600))] = None,
) -> dict:
    """发起认领（需登录；契约 §3.8）。

    - 问题由 MaaS 生成，**必须**通过 `validate_questions()` 安全校验，
      不通过则回退 `algorithm/config/questions.yaml` 模板库；
    - 响应**绝不包含**标准答案、答案摘要、答案哈希；
    - 重复发起返回 `409 STATE_CONFLICT`。
    """
    claim, expires_at = claim_service.create_claim(
        db, applicant=current_user, match_id=payload.match_id
    )
    data = serializers.serialize_claim_for_applicant(claim)
    data["expires_at"] = expires_at.isoformat()
    data["next_step"] = "请在有效期内完成验证问答"
    return ok(data, message="认领已发起，请回答验证问题", request_id=current_request_id(request))


@router.get(
    "/{claim_id}",
    summary="认领详情（认领人视角）",
    description="申请人本人或管理员；未提交验证前不返回分数与风险字段",
)
def get_claim_detail(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    claim_id: int,
) -> dict:
    """认领详情（需登录；契约 §3.10.1）。

    用途：认领验证页刷新/重新进入时恢复问题与作答状态。

    - 权限：申请人本人或管理员，他人访问 `403 FORBIDDEN`；
    - 未提交验证前 `verification_score` / `credibility_score` / `risk_level`
      **不出现**（而非返回 null）；
    - 响应绝不含标准答案 / `answers_hash` / `answers_plain`。
    """
    claim = get_claim_or_404(db, claim_id)
    data = claim_service.claim_detail_for_applicant(claim, viewer=current_user)
    return ok(data, request_id=current_request_id(request))


@router.post(
    "/{claim_id}/verify",
    summary="提交认领验证",
    description="提交隐藏特征验证答案；验证通过后进入 pending_review，绝不自动确权",
)
def verify_claim(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    claim_id: int,
    payload: ClaimVerifyRequest,
    ip: ClientIp,
    _limit: Annotated[None, Depends(rate_limit("claim_verify", 10, 60))] = None,
) -> dict:
    """提交认领验证（需登录；契约 §3.9）。

    可信度分 = 0.60×答案匹配 + 0.20×候选匹配分 + 0.10×完整度 + 0.10×行为规则分；
    风险等级阈值 0.75 / 0.45。`credibility_score` 仅作辅助依据，响应带免责声明。
    """
    claim = get_claim_or_404(db, claim_id)
    outcome = claim_service.verify_claim(
        db,
        claim=claim,
        applicant=current_user,
        answers=payload.answer_map(),
        ip=ip,
    )

    # 统一走序列化层（含 per_question / risk_tips / attempts_left / 分数）
    data = serializers.serialize_claim_for_applicant(claim)
    data["next_step"] = outcome.next_step
    data["disclaimer"] = "可信度分为算法辅助依据，最终归属由管理员审核确认。"

    message = "验证完成，已进入人工审核" if outcome.passed else "验证未通过"
    logger.info(
        "验证提交 claim_id=%s passed=%s credibility=%.4f status=%s",
        claim.id,
        outcome.passed,
        outcome.credibility_score,
        claim.status,
    )
    return ok(data, message=message, request_id=current_request_id(request))


__all__ = ["router"]
