/**
 * 认领接口（契约 §3.8 - §3.10）：
 *   POST /claims                     发起认领（body: { match_id }），返回 claim_id + 2~3 个验证问题（不含答案）
 *   POST /claims/{claim_id}/verify   提交作答，返回 verification_score / credibility_score / risk_level / status
 *   GET  /claims/{claim_id}          读取认领详情（未列入契约，用于刷新后恢复问题列表，页面有降级方案）
 *   GET  /me/claims                  我的认领记录
 *
 * 合规约束：本文件不定义、不请求任何「标准答案」字段。
 */
import { createIdempotencyKey, httpGet, httpPost } from './http'
import type { PageQuery, PageResult } from '@/types/api'
import { clampPage, clampPageSize, toPageResult } from '@/types/api'
import type {
  ClaimDetail,
  ClaimQuestion,
  ClaimStatus,
  CreateClaimPayload,
  CreateClaimResult,
  MyClaimRecord,
  PerQuestionResult,
  RiskLevel,
  VerifyClaimPayload,
  VerifyClaimResult,
} from '@/types/claim'

/** 发起认领结果归一化 */
export function normalizeCreateClaim(raw: unknown): CreateClaimResult {
  const source = (raw ?? {}) as Record<string, unknown>
  const rawQuestions = Array.isArray(source.questions) ? (source.questions as Array<Record<string, unknown>>) : []

  const questions: ClaimQuestion[] = rawQuestions.map((question, index) => ({
    question_id: String(question.question_id ?? `q${index + 1}`),
    question_text: String(question.question_text ?? ''),
  }))

  return {
    claim_id: Number(source.claim_id ?? source.id ?? 0),
    match_id: source.match_id === undefined ? undefined : Number(source.match_id),
    status: (typeof source.status === 'string' ? source.status : 'claiming') as ClaimStatus,
    questions: questions.filter((question) => Boolean(question.question_text)),
    max_attempts: source.max_attempts === undefined ? undefined : Number(source.max_attempts),
    expires_at: typeof source.expires_at === 'string' ? source.expires_at : null,
  }
}

/** 逐题结果归一化（只保留「是否命中 + 得分 + 理由」，不涉及答案原文） */
export function normalizePerQuestion(raw: unknown): PerQuestionResult[] {
  if (!Array.isArray(raw)) return []
  return (raw as Array<Record<string, unknown>>).map((item, index) => ({
    question_id: String(item.question_id ?? `q${index + 1}`),
    question_text: typeof item.question_text === 'string' ? item.question_text : null,
    matched: item.matched === true,
    score: Number(item.score ?? 0),
    reason: typeof item.reason === 'string' ? item.reason : '',
  }))
}

/** 验证结果归一化 */
export function normalizeVerifyResult(raw: unknown): VerifyClaimResult {
  const source = (raw ?? {}) as Record<string, unknown>
  const riskLevel = source.risk_level

  return {
    claim_id: source.claim_id === undefined ? undefined : Number(source.claim_id),
    verification_score: Number(source.verification_score ?? 0),
    credibility_score:
      source.credibility_score === undefined || source.credibility_score === null
        ? null
        : Number(source.credibility_score),
    risk_level: (riskLevel === 'low' || riskLevel === 'medium' || riskLevel === 'high'
      ? riskLevel
      : 'medium') as RiskLevel,
    status: (typeof source.status === 'string' ? source.status : 'pending_review') as ClaimStatus,
    per_question: normalizePerQuestion(source.per_question),
    risk_tips: Array.isArray(source.risk_tips) ? (source.risk_tips as string[]) : [],
    attempts_left: source.attempts_left === undefined || source.attempts_left === null ? null : Number(source.attempts_left),
    next_step: typeof source.next_step === 'string' ? source.next_step : null,
    disclaimer: typeof source.disclaimer === 'string' ? source.disclaimer : null,
  }
}

/** 我的认领记录归一化（后端可能把物品摘要内联在 item 字段） */
export function normalizeMyClaim(raw: unknown): MyClaimRecord {
  const source = (raw ?? {}) as Record<string, unknown>
  const item = (source.item ?? {}) as Record<string, unknown>
  const riskLevel = source.risk_level

  return {
    claim_id: Number(source.claim_id ?? source.id ?? 0),
    item_id: Number(source.item_id ?? item.item_id ?? item.id ?? 0),
    item_title: String(source.item_title ?? item.title ?? '未命名物品'),
    match_id: Number(source.match_id ?? 0),
    status: (typeof source.status === 'string' ? source.status : 'claiming') as ClaimStatus,
    verification_score:
      source.verification_score === undefined || source.verification_score === null
        ? null
        : Number(source.verification_score),
    risk_level:
      riskLevel === 'low' || riskLevel === 'medium' || riskLevel === 'high' ? riskLevel : null,
    admin_comment: typeof source.admin_comment === 'string' ? source.admin_comment : null,
    created_at: typeof source.created_at === 'string' ? source.created_at : '',
    updated_at: typeof source.updated_at === 'string' ? source.updated_at : null,
    item_cover_image_url:
      typeof source.item_cover_image_url === 'string'
        ? source.item_cover_image_url
        : typeof item.thumbnail_url === 'string'
          ? item.thumbnail_url
          : null,
  }
}

/** 基于某条候选匹配发起认领（携带幂等键，避免重复发起） */
export async function createClaim(payload: CreateClaimPayload): Promise<CreateClaimResult> {
  const data = await httpPost<unknown>(
    '/claims',
    { match_id: payload.match_id },
    { idempotencyKey: createIdempotencyKey() },
  )
  return normalizeCreateClaim(data)
}

/** 提交验证作答 */
export async function verifyClaim(
  claimId: number,
  payload: VerifyClaimPayload,
): Promise<VerifyClaimResult> {
  const data = await httpPost<unknown>(
    `/claims/${claimId}/verify`,
    {
      answers: payload.answers.map((item) => ({
        question_id: item.question_id,
        answer: item.answer.trim(),
      })),
    },
    { idempotencyKey: createIdempotencyKey() },
  )
  return normalizeVerifyResult(data)
}

/**
 * 读取认领详情（含问题列表，用于页面刷新后恢复）。
 * 后端未实现时返回 404 / CLAIM_NOT_FOUND，页面会回退到 sessionStorage 暂存的问题列表。
 */
export function fetchClaimDetail(claimId: number): Promise<ClaimDetail> {
  return httpGet<ClaimDetail>(`/claims/${claimId}`, undefined, { silent: true })
}

/** 我的认领记录 */
export async function getMyClaims(query: PageQuery = {}): Promise<PageResult<MyClaimRecord>> {
  const page = clampPage(query.page)
  const pageSize = clampPageSize(query.page_size)
  const data = await httpGet<unknown>('/me/claims', { page, page_size: pageSize })
  const result = toPageResult<unknown>(data, page, pageSize)
  return { ...result, items: result.items.map(normalizeMyClaim) }
}
