/**
 * 管理员接口（契约 §3.11 - §3.13）：
 *   POST /admin/claims/{claim_id}/review   审核操作：approve / reject / request_more_info
 *   GET  /admin/claims/{claim_id}          审核详情聚合（认领 + 匹配 + 双方物品 + 验证结果 + 历史）
 *   GET  /admin/audit-logs                 审计日志查询（target_type / target_id / 分页）
 *
 * 说明：契约附录 A 未列出「待审核认领列表」接口，listAdminClaims 属于预留实现；
 * 若后端返回 404，审核台会自动降级为「按认领 ID 直接查询」（见 AdminReviewView）。
 *
 * 权限：以上接口均需管理员身份，后端必须二次鉴权，前端路由守卫只是体验层拦截。
 */
import { createIdempotencyKey, httpGet, httpPost } from './http'
import type { PageResult } from '@/types/api'
import { clampPage, clampPageSize, toPageResult } from '@/types/api'
import type {
  AdminClaimDetail,
  AdminClaimListItem,
  AdminClaimParty,
  AdminClaimQuery,
  AdminClaimSummary,
  AdminHistoryEntry,
  AdminVerificationView,
  ClaimReviewPayload,
  ClaimReviewResult,
  ClaimStatus,
  RiskLevel,
} from '@/types/claim'
import { normalizePerQuestion } from './claims'
import { normalizeCandidate } from './matches'
import { normalizeItemSummary } from './items'
import type { AuditLog, AuditLogQuery, AuditTargetType } from '@/types/user'

/** 审核台·物品与发布者归一化 */
function normalizeParty(raw: unknown): AdminClaimParty | null {
  if (!raw || typeof raw !== 'object') return null
  const source = raw as Record<string, unknown>
  const owner = (source.owner ?? null) as Record<string, unknown> | null

  return {
    id: Number(source.id ?? source.item_id ?? 0),
    title: String(source.title ?? '未命名物品'),
    hidden_features: (source.hidden_features as string | string[] | null) ?? null,
    owner: owner
      ? {
          id: Number(owner.id ?? 0),
          username: String(owner.username ?? owner.nickname ?? '未知用户'),
          contact_id: typeof owner.contact_id === 'string' ? owner.contact_id : null,
        }
      : null,
  }
}

/** 风险等级取值收敛（后端字段异常时返回 null，避免页面出现未知标签） */
function toRiskLevel(value: unknown): RiskLevel | null {
  return value === 'low' || value === 'medium' || value === 'high' ? value : null
}

/** 审核台·认领概要归一化 */
function normalizeClaimSummary(raw: unknown): AdminClaimSummary {
  const source = (raw ?? {}) as Record<string, unknown>

  return {
    id: Number(source.id ?? source.claim_id ?? 0),
    status: (typeof source.status === 'string' ? source.status : 'pending_review') as ClaimStatus,
    verification_score:
      source.verification_score === undefined || source.verification_score === null
        ? null
        : Number(source.verification_score),
    credibility_score:
      source.credibility_score === undefined || source.credibility_score === null
        ? null
        : Number(source.credibility_score),
    risk_level: toRiskLevel(source.risk_level),
    created_at: typeof source.created_at === 'string' ? source.created_at : '',
  }
}

/** 审核台·验证结果归一化（只含命中情况与理由） */
function normalizeVerification(raw: unknown): AdminVerificationView {
  const source = (raw ?? {}) as Record<string, unknown>
  return {
    per_question: normalizePerQuestion(source.per_question),
    attempts_used:
      source.attempts_used === undefined || source.attempts_used === null ? null : Number(source.attempts_used),
    risk_tips: Array.isArray(source.risk_tips) ? (source.risk_tips as string[]) : [],
  }
}

/** 审核台·历史日志归一化（兼容 {action, operator} 与审计日志结构） */
function normalizeHistory(raw: unknown): AdminHistoryEntry[] {
  if (!Array.isArray(raw)) return []
  return (raw as Array<Record<string, unknown>>).map((entry) => ({
    action: String(entry.action ?? ''),
    operator:
      typeof entry.operator === 'string'
        ? entry.operator
        : typeof entry.operator_username === 'string'
          ? entry.operator_username
          : null,
    created_at: typeof entry.created_at === 'string' ? entry.created_at : '',
    detail: (entry.detail as Record<string, unknown> | null) ?? null,
  }))
}

/** 审核详情归一化：把双方物品中「对方物品」补成候选匹配卡片所需的摘要 */
export function normalizeAdminClaimDetail(raw: unknown): AdminClaimDetail {
  const source = (raw ?? {}) as Record<string, unknown>
  const claim = normalizeClaimSummary(source.claim)
  const lostItem = normalizeParty(source.lost_item)
  const foundItem = normalizeParty(source.found_item)
  const matchRaw = (source.match ?? {}) as Record<string, unknown>

  const candidate = normalizeCandidate(matchRaw, claim.id)
  const counterpart = foundItem ?? lostItem
  if (!candidate.matched_item && counterpart) {
    candidate.matched_item = normalizeItemSummary({
      id: counterpart.id,
      title: counterpart.title,
      post_type: foundItem ? 'found' : 'lost',
    })
    if (!candidate.matched_item_id) candidate.matched_item_id = counterpart.id
  }

  return {
    claim,
    match: candidate,
    lost_item: lostItem,
    found_item: foundItem,
    verification: normalizeVerification(source.verification),
    history: normalizeHistory(source.history),
    item: null,
  }
}

/** 审计日志归一化（契约字段为 id / operator_username） */
export function normalizeAuditLog(raw: unknown): AuditLog {
  const source = (raw ?? {}) as Record<string, unknown>
  const targetType = source.target_type

  return {
    id: Number(source.id ?? source.log_id ?? 0),
    operator_id: source.operator_id === undefined || source.operator_id === null ? null : Number(source.operator_id),
    operator_username:
      typeof source.operator_username === 'string'
        ? source.operator_username
        : typeof source.operator_name === 'string'
          ? source.operator_name
          : null,
    target_type: (typeof targetType === 'string' ? targetType : 'item') as AuditTargetType,
    target_id: Number(source.target_id ?? 0),
    action: String(source.action ?? ''),
    detail: (source.detail as Record<string, unknown> | null) ?? null,
    request_id: typeof source.request_id === 'string' ? source.request_id : null,
    created_at: typeof source.created_at === 'string' ? source.created_at : '',
  }
}

/**
 * 待审核 / 历史认领列表（契约未提供的预留接口）。
 * 后端返回 404 时抛 ApiError，调用方应降级为「按认领 ID 查询」。
 */
export async function listAdminClaims(
  query: AdminClaimQuery = {},
): Promise<PageResult<AdminClaimListItem>> {
  const page = clampPage(query.page)
  const pageSize = clampPageSize(query.page_size)
  const params: Record<string, unknown> = { page, page_size: pageSize }
  if (query.status) params.status = query.status
  if (query.keyword && query.keyword.trim()) params.keyword = query.keyword.trim()

  const data = await httpGet<unknown>('/admin/claims', params)
  return toPageResult<AdminClaimListItem>(data, page, pageSize)
}

/** 审核台详情：一次拿到审核所需的全部依据 */
export async function getAdminClaimDetail(claimId: number): Promise<AdminClaimDetail> {
  const data = await httpGet<unknown>(`/admin/claims/${claimId}`)
  return normalizeAdminClaimDetail(data)
}

/** 提交审核结论（携带幂等键，避免重复审核写多条日志） */
export async function reviewClaim(
  claimId: number,
  payload: ClaimReviewPayload,
): Promise<ClaimReviewResult> {
  const data = await httpPost<unknown>(
    `/admin/claims/${claimId}/review`,
    {
      action: payload.action,
      comment: payload.comment?.trim() || undefined,
    },
    { idempotencyKey: createIdempotencyKey() },
  )

  const source = (data ?? {}) as Record<string, unknown>
  return {
    claim_id: Number(source.claim_id ?? claimId),
    action: payload.action,
    claim_status:
      typeof source.claim_status === 'string' ? (source.claim_status as ClaimStatus) : undefined,
    item_status: typeof source.item_status === 'string' ? source.item_status : null,
    item_id: source.item_id === undefined || source.item_id === null ? null : Number(source.item_id),
    reviewed_at: typeof source.reviewed_at === 'string' ? source.reviewed_at : null,
    audit_log_id:
      source.audit_log_id === undefined || source.audit_log_id === null ? null : Number(source.audit_log_id),
  }
}

/** 审计日志查询：按 target_type / target_id 过滤 */
export async function fetchAuditLogs(query: AuditLogQuery = {}): Promise<PageResult<AuditLog>> {
  const page = clampPage(query.page)
  const pageSize = clampPageSize(query.page_size)
  const params: Record<string, unknown> = { page, page_size: pageSize }
  if (query.target_type) params.target_type = query.target_type
  if (query.target_id !== null && query.target_id !== undefined && Number(query.target_id) > 0) {
    params.target_id = Number(query.target_id)
  }

  const data = await httpGet<unknown>('/admin/audit-logs', params)
  const result = toPageResult<unknown>(data, page, pageSize)
  return { ...result, items: result.items.map(normalizeAuditLog) }
}
