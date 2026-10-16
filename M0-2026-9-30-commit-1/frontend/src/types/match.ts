/**
 * 匹配领域类型：总分 + 6 个维度分 + 依据 + 不确定因素 + 风险提示。
 * 对应接口：GET /items/{item_id}/matches?top_k=5、POST /items/{item_id}/match。
 *
 * 合规要点：匹配结果只是候选，不构成归属认定，页面必须展示 MATCH_DISCLAIMER_TEXT。
 */
import type { ItemSummary, TagType } from './item'

/** 6 个维度的分值（后端返回 0~1 的小数，统一用 toScorePercent 归一化展示） */
export interface MatchScoreDetail {
  category_score: number
  attribute_score: number
  image_score: number
  time_score: number
  location_score: number
  brand_model_score: number
}

export type MatchScoreKey = keyof MatchScoreDetail

export const MATCH_SCORE_KEYS: readonly MatchScoreKey[] = [
  'category_score',
  'attribute_score',
  'image_score',
  'time_score',
  'location_score',
  'brand_model_score',
]

/** 维度中文名（UI 必须展示，便于管理员理解分数来源） */
export const MATCH_SCORE_LABELS: Record<MatchScoreKey, string> = {
  category_score: '类别一致性',
  attribute_score: '属性一致性',
  image_score: '图像相似度',
  time_score: '时间接近度',
  location_score: '地点接近度',
  brand_model_score: '品牌型号一致性',
}

/** 维度释义：解释该维度比对了什么，供审核人判断可信程度 */
export const MATCH_SCORE_HINTS: Record<MatchScoreKey, string> = {
  category_score: '双方物品分类是否一致（如均为「无线耳机」）',
  attribute_score: '颜色、公开特征、描述文本等属性的重合程度',
  image_score: '图片视觉特征相似度；未上传图片时该维度为 0',
  time_score: '丢失时间与拾获时间的接近程度，跨天会明显衰减',
  location_score: '丢失地点与拾获地点的空间接近程度',
  brand_model_score: '品牌 / 型号文本是否一致或高度相近',
}

/**
 * 风险等级（契约 §2.6）：low ≥0.75、medium 0.45~0.75、high <0.45。
 * 该类型同时被匹配与认领两个领域使用，因此定义在这里，由 claim.ts 复用。
 */
export type RiskLevelSource = 'low' | 'medium' | 'high'

/** 匹配档位：high ≥0.75 / medium 0.5~0.75 / low <0.5（阈值来自后端配置，前端只做展示） */
export type MatchLevel = 'high' | 'medium' | 'low'

export const MATCH_LEVEL_LABELS: Record<MatchLevel, string> = {
  high: '高匹配',
  medium: '中等匹配',
  low: '低匹配',
}

export const MATCH_LEVEL_TAG: Record<MatchLevel, TagType> = {
  high: 'success',
  medium: 'warning',
  low: 'info',
}

/** 单条候选匹配 */
export interface MatchCandidate {
  /** 认领流程必须携带该 ID：POST /claims { match_id } */
  match_id: number
  /** 发起匹配的物品 ID */
  item_id: number
  /** 被匹配到的对方物品 ID */
  matched_item_id: number
  /** 对方物品摘要（后端字段 candidate_item，归一化后放入该字段） */
  matched_item?: ItemSummary | null
  total_score: number
  score_detail: MatchScoreDetail
  /** 支持该候选成立的依据（自然语言，直接展示给用户与管理员） */
  evidence: string[]
  /** 不确定因素：必须与依据一起展示，避免用户过度解读分数 */
  uncertainties: string[]
  /** 风险提示：如时间跨度过大、图片缺失、高仿冒风险类别等 */
  risk_tips: string[]
  /** 匹配档位（后端 match_level），low 不得展示为「高匹配」 */
  match_level?: MatchLevel | null
  created_at?: string | null
}

/** 匹配列表结果（契约 §3.6 的 data 归一化） */
export interface MatchListResult {
  item_id: number
  top_k: number
  candidates: MatchCandidate[]
  /** 匹配计算时间（后端 computed_at） */
  generated_at?: string | null
  /** 算法服务版本，便于复现问题 */
  model_version?: string | null
  /** 结果是否来自缓存 */
  cached?: boolean
  /** true 表示算法服务不可用、已降级为规则/Mock 匹配，前端必须显式标注「降级结果」 */
  degraded?: boolean
  /** 后端返回的免责声明原文（前端仍固定展示 MATCH_DISCLAIMER_TEXT） */
  disclaimer?: string | null
}

/** 匹配默认与最大候选数量（契约 top_k 默认 5、上限 20，前端保守限制为 10） */
export const DEFAULT_TOP_K = 5
export const MAX_TOP_K = 10

/** 降级提示：degraded=true 时必须展示 */
export const MATCH_DEGRADED_TEXT = '当前结果由规则降级计算产生（算法服务不可用），仅供参考'

/** 关键合规文案：所有展示候选匹配的位置都必须出现 */
export const MATCH_DISCLAIMER_TEXT = '候选匹配不等于确认归属'

/** 匹配机制说明（放在结果页顶部，帮助用户理解分数含义） */
export const MATCH_ALGO_NOTE =
  '匹配分数由类别、属性、图像、时间、地点、品牌型号六个维度加权计算，仅作为人工审核的辅助依据，不作为物品归属的判定结论。'

/**
 * 分数归一化为 0~100 的整数：
 * 后端若返回 0~1 的相似度会自动放大，避免进度条显示成 1%。
 */
export function toScorePercent(score: number | null | undefined): number {
  const value = Number(score)
  if (!Number.isFinite(value) || value <= 0) return 0
  const percent = value <= 1 ? value * 100 : value
  return Math.min(100, Math.round(percent))
}
