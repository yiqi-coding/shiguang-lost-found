/**
 * 认领领域类型（契约 §2.4、§3.8、§3.9、§3.11、§3.12）。
 *
 * 合规要点（契约 §1.6 权限硬性要求）：
 * 1) 发起认领的响应绝不包含标准答案、答案摘要、答案哈希 —— 本文件也不为它们预留任何字段；
 * 2) 验证结果只展示「是否命中 + 得分 + 理由」，理由文案不得回显标准答案原文；
 * 3) 联系方式一律使用后端脱敏后的字符串（contact_id）。
 */
import type { ItemDetail, PostType, TagType } from './item'
import type { MatchCandidate, RiskLevelSource } from './match'
import type { AuditLog } from './user'

/** 认领状态（契约 §2.4：claiming → pending_review → approved / rejected，管理员可置 request_more_info） */
export type ClaimStatus =
  | 'claiming'
  | 'pending_review'
  | 'approved'
  | 'claimed'
  | 'rejected'
  | 'request_more_info'

export const CLAIM_STATUS_LABELS: Record<ClaimStatus, string> = {
  claiming: '认领中',
  pending_review: '待审核',
  approved: '已通过',
  claimed: '已认领',
  rejected: '已驳回',
  request_more_info: '待补充信息',
}

export const CLAIM_STATUS_TAG: Record<ClaimStatus, TagType> = {
  claiming: 'primary',
  pending_review: 'warning',
  approved: 'success',
  claimed: 'success',
  rejected: 'danger',
  request_more_info: 'warning',
}

/**
 * 风险等级（契约 §2.6）：low ≥0.75、medium 0.45~0.75、high <0.45。
 * 阈值来自后端配置 config/matching.yaml，前端不重新计算，只展示后端返回值。
 */
export type RiskLevel = RiskLevelSource

export const RISK_LEVEL_LABELS: Record<RiskLevel, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}

export const RISK_LEVEL_TAG: Record<RiskLevel, TagType> = {
  low: 'success',
  medium: 'warning',
  high: 'danger',
}

/** 风险等级对应的后续说明（认领验证页展示，避免用户误解为最终结论） */
export const RISK_LEVEL_ADVICE: Record<RiskLevel, string> = {
  low: '回答与隐藏特征的匹配程度较好，请等待管理员审核确认。',
  medium: '部分回答与隐藏特征不完全一致，管理员可能要求补充信息。',
  high: '回答与隐藏特征差异较大，建议主动补充可核验的细节，或等待管理员联系。',
}

/** 验证问题（后端只给问题文本，不含答案） */
export interface ClaimQuestion {
  question_id: string
  question_text: string
}

/** 发起认领入参 */
export interface CreateClaimPayload {
  match_id: number
}

/** 发起认领结果（契约 §3.8 的 201 响应） */
export interface CreateClaimResult {
  claim_id: number
  match_id?: number
  status?: ClaimStatus
  questions: ClaimQuestion[]
  /** 允许的验证尝试次数 */
  max_attempts?: number
  /** 认领会话过期时间（ISO 8601） */
  expires_at?: string | null
}

/** 单条作答 */
export interface ClaimAnswer {
  question_id: string
  answer: string
}

/** 提交验证入参 */
export interface VerifyClaimPayload {
  answers: ClaimAnswer[]
}

/** 逐题验证结果（reason 由后端保证不回显标准答案原文） */
export interface PerQuestionResult {
  question_id: string
  question_text?: string | null
  matched: boolean
  score: number
  reason: string
}

/** 验证结果（契约 §3.9 的 200 响应） */
export interface VerifyClaimResult {
  claim_id?: number
  /** 隐藏特征答案匹配分（0~1） */
  verification_score: number
  /** 综合可信度分（0~1）：0.6×答案匹配 + 0.2×候选匹配 + 0.1×信息完整度 + 0.1×行为风险 */
  credibility_score?: number | null
  risk_level: RiskLevel
  status: ClaimStatus
  per_question?: PerQuestionResult[]
  risk_tips?: string[]
  attempts_left?: number | null
  next_step?: string | null
  disclaimer?: string | null
}

/**
 * 认领流程详情（GET /claims/{claim_id}）。
 * 该路径未列入契约附录 A 的接口清单，但认领验证页刷新后需要恢复问题列表，
 * 因此前端做了「接口优先 + sessionStorage 暂存降级」的双通道处理（见 stores/session.ts）。
 */
export interface ClaimDetail {
  claim_id: number
  item_id: number
  match_id: number
  status: ClaimStatus
  questions: ClaimQuestion[]
  verification_score?: number | null
  risk_level?: RiskLevel | null
}

/** 我的认领记录（GET /me/claims，契约 §3.10） */
export interface MyClaimRecord {
  claim_id: number
  item_id: number
  item_title: string
  match_id: number
  status: ClaimStatus
  verification_score?: number | null
  risk_level?: RiskLevel | null
  admin_comment?: string | null
  created_at: string
  updated_at?: string | null
  item_cover_image_url?: string | null
}

/** 管理员审核动作（契约 §2.7） */
export type ReviewAction = 'approve' | 'reject' | 'request_more_info'

export const REVIEW_ACTION_LABELS: Record<ReviewAction, string> = {
  approve: '通过认领',
  reject: '驳回认领',
  request_more_info: '要求补充信息',
}

/** 审核请求体（POST /admin/claims/{claim_id}/review；reject 与 request_more_info 时 comment 必填 10~500 字） */
export interface ClaimReviewPayload {
  action: ReviewAction
  comment?: string
}

/** 审核结果（契约 §3.11 的 200 响应） */
export interface ClaimReviewResult {
  claim_id: number
  action?: ReviewAction
  /** 认领新状态，例如 approved */
  claim_status?: ClaimStatus
  /** 物品新状态，例如 claimed */
  item_status?: string | null
  item_id?: number | null
  reviewed_at?: string | null
  audit_log_id?: number | null
}

/** 审核台列表项（待审核列表接口未列入契约，字段以后端实现为准） */
export interface AdminClaimListItem {
  claim_id: number
  item_id: number
  item_title: string
  post_type: PostType
  category: string
  claimant_nickname: string
  status: ClaimStatus
  verification_score?: number | null
  risk_level?: RiskLevel | null
  match_total_score?: number | null
  created_at: string
}

/** 审核台列表查询参数 */
export interface AdminClaimQuery {
  page?: number
  page_size?: number
  status?: ClaimStatus | ''
  keyword?: string
}

/** 审核台·认领概要（契约 §3.12 的 data.claim） */
export interface AdminClaimSummary {
  id: number
  status: ClaimStatus
  verification_score?: number | null
  credibility_score?: number | null
  risk_level?: RiskLevel | null
  created_at: string
}

/** 审核台·物品与发布者（契约 §3.12 的 lost_item / found_item） */
export interface AdminClaimParty {
  id: number
  title: string
  /** 隐藏特征：管理员可见；后端为 JSON 数组，展示时统一转换 */
  hidden_features?: string | string[] | null
  owner?: {
    id: number
    username: string
    /** 已脱敏联系方式，例如 138****8888 */
    contact_id?: string | null
  } | null
}

/** 审核台·验证结果（只含命中情况与理由，绝不含标准答案） */
export interface AdminVerificationView {
  per_question: PerQuestionResult[]
  attempts_used?: number | null
  risk_tips: string[]
}

/** 审核台·历史操作条目（与审计日志同源） */
export interface AdminHistoryEntry {
  action: string
  operator?: string | null
  created_at: string
  detail?: Record<string, unknown> | null
}

/** 审核台详情聚合（契约 §3.12） */
export interface AdminClaimDetail {
  claim: AdminClaimSummary
  /** 归一化后的匹配摘要（candidate 信息由 lost/found item 补齐） */
  match: MatchCandidate
  lost_item: AdminClaimParty | null
  found_item: AdminClaimParty | null
  verification: AdminVerificationView
  history: AdminHistoryEntry[]
  /** 兼容字段：部分实现会把物品详情一并返回 */
  item?: ItemDetail | null
  /** 兼容字段：审计日志明细（若后端直接返回 audit_logs 结构） */
  audit_logs?: AuditLog[]
}
