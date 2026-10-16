/**
 * 匹配接口（契约 §3.6、§3.7）：
 *   GET  /items/{item_id}/matches?top_k=5   读取候选匹配（总分 + 6 维分 + 依据 + 不确定因素）
 *   POST /items/{item_id}/match             重新计算并落库（需鉴权，发布者本人或管理员）
 *
 * 归一化说明：后端 data 形如
 *   { query_item_id, model_version, computed_at, cached, degraded, results: [...], disclaimer }
 * 其中候选内部字段为 candidate_item，前端统一归一化为 matched_item，页面只依赖归一化后的结构。
 */
import { createIdempotencyKey, httpGet, httpPost } from './http'
import type { MatchCandidate, MatchListResult } from '@/types/match'
import { DEFAULT_TOP_K, MAX_TOP_K } from '@/types/match'
import { normalizeItemSummary } from './items'

/** top_k 收敛到 [1, MAX_TOP_K]，默认 5 */
export function normalizeTopK(topK?: number): number {
  const value = Number(topK ?? DEFAULT_TOP_K)
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_TOP_K
  return Math.min(MAX_TOP_K, Math.floor(value))
}

/** 解析对方物品 ID：优先取显式字段，其次取 candidate_item.id */
function resolveMatchedItemId(source: Record<string, unknown>, candidateItem: unknown): number {
  const direct = Number(source.matched_item_id)
  if (Number.isFinite(direct) && direct > 0) return direct
  if (candidateItem && typeof candidateItem === 'object') {
    const entry = candidateItem as Record<string, unknown>
    const id = Number(entry.item_id ?? entry.id)
    if (Number.isFinite(id) && id > 0) return id
  }
  return 0
}

/** 单个候选归一化：candidate_item → matched_item，分数字段缺失时补 0 */
export function normalizeCandidate(raw: unknown, fallbackItemId = 0): MatchCandidate {
  const source = (raw ?? {}) as Record<string, unknown>
  const candidateItem = source.matched_item ?? source.candidate_item
  const scoreDetail = (source.score_detail ?? {}) as Record<string, unknown>

  return {
    match_id: Number(source.match_id ?? 0),
    item_id: Number(source.item_id ?? fallbackItemId),
    matched_item_id: resolveMatchedItemId(source, candidateItem),
    matched_item: candidateItem ? normalizeItemSummary(candidateItem) : null,
    total_score: Number(source.total_score ?? 0),
    score_detail: {
      category_score: Number(scoreDetail.category_score ?? 0),
      attribute_score: Number(scoreDetail.attribute_score ?? 0),
      image_score: Number(scoreDetail.image_score ?? 0),
      time_score: Number(scoreDetail.time_score ?? 0),
      location_score: Number(scoreDetail.location_score ?? 0),
      brand_model_score: Number(scoreDetail.brand_model_score ?? 0),
    },
    evidence: Array.isArray(source.evidence) ? (source.evidence as string[]) : [],
    uncertainties: Array.isArray(source.uncertainties) ? (source.uncertainties as string[]) : [],
    risk_tips: Array.isArray(source.risk_tips) ? (source.risk_tips as string[]) : [],
    match_level:
      source.match_level === 'high' || source.match_level === 'medium' || source.match_level === 'low'
        ? source.match_level
        : null,
    created_at: typeof source.created_at === 'string' ? source.created_at : null,
  }
}

/** 把后端返回的匹配数据整理成统一结构（兼容裸数组与 results 字段） */
export function normalizeMatchList(raw: unknown, itemId: number, topK: number): MatchListResult {
  if (Array.isArray(raw)) {
    return {
      item_id: itemId,
      top_k: topK,
      candidates: raw.map((item) => normalizeCandidate(item, itemId)),
      generated_at: null,
      model_version: null,
      cached: false,
      degraded: false,
      disclaimer: null,
    }
  }

  const source = (raw ?? {}) as Record<string, unknown>
  const rawCandidates = source.results ?? source.candidates ?? source.matches ?? source.items

  return {
    item_id: Number(source.query_item_id ?? source.item_id ?? itemId) || itemId,
    top_k: normalizeTopK(Number(source.top_k ?? topK)),
    candidates: Array.isArray(rawCandidates)
      ? rawCandidates.map((item) => normalizeCandidate(item, itemId))
      : [],
    generated_at:
      typeof source.computed_at === 'string'
        ? source.computed_at
        : typeof source.generated_at === 'string'
          ? source.generated_at
          : null,
    model_version: typeof source.model_version === 'string' ? source.model_version : null,
    cached: source.cached === true,
    degraded: source.degraded === true,
    disclaimer: typeof source.disclaimer === 'string' ? source.disclaimer : null,
  }
}

/** 读取候选匹配结果 */
export async function fetchMatches(
  itemId: number,
  topK: number = DEFAULT_TOP_K,
): Promise<MatchListResult> {
  const safeTopK = normalizeTopK(topK)
  const data = await httpGet<unknown>(`/items/${itemId}/matches`, { top_k: safeTopK })
  return normalizeMatchList(data, itemId, safeTopK)
}

/**
 * 重新计算匹配（需鉴权）。
 * 契约允许 202 异步返回（data 为 task_id/status），此时直接重新拉取一次结果列表。
 */
export async function recalculateMatches(
  itemId: number,
  topK: number = DEFAULT_TOP_K,
): Promise<MatchListResult> {
  const safeTopK = normalizeTopK(topK)
  const data = await httpPost<unknown>(
    `/items/${itemId}/match`,
    { force: true, top_k: safeTopK },
    { timeout: 60000, idempotencyKey: createIdempotencyKey() },
  )

  const source = (data ?? {}) as Record<string, unknown>
  const isAsyncTask = typeof source.task_id === 'string' || source.status === 'running'
  const hasResults = Array.isArray(source.results) || Array.isArray(source.candidates)

  if (isAsyncTask && !hasResults) {
    // 异步任务：稍等片刻后读取最新结果（缓存 TTL 10min，后端已写入 matches 表）
    return fetchMatches(itemId, safeTopK)
  }

  return normalizeMatchList(data, itemId, safeTopK)
}
