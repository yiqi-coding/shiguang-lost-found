/**
 * 通用接口契约：统一响应包装、业务错误码、分页、健康检查与平台级文案常量。
 * 字段命名严格对齐《拾光 API 契约（Baseline v1.0 · Frozen）》（docs/api-contract.md）第 1 章；
 * 该文档与后端 OpenAPI 是唯一事实来源，本文件不得单方面变更字段名与枚举值。
 */

/** 业务错误码白名单（与后端约定，前端不得自行新增） */
export const BUSINESS_ERROR_CODES = [
  'VALIDATION_ERROR',
  'UNAUTHORIZED',
  'FORBIDDEN',
  'ITEM_NOT_FOUND',
  'CLAIM_NOT_FOUND',
  'MATCH_NOT_FOUND',
  'STATE_CONFLICT',
  'ALGORITHM_SERVICE_ERROR',
  'STORAGE_SERVICE_ERROR',
  'AI_SERVICE_ERROR',
  'RATE_LIMITED',
  'INTERNAL_ERROR',
] as const

export type BusinessErrorCode = (typeof BUSINESS_ERROR_CODES)[number]

/** 成功码 + 业务错误码 */
export type ApiCode = 'OK' | BusinessErrorCode

/** 统一成功响应 */
export interface ApiSuccessBody<T> {
  code: 'OK'
  message: string
  data: T
  request_id: string
}

/** 校验失败的字段级错误（detail 仅在 VALIDATION_ERROR 时非空） */
export interface ApiFieldError {
  field: string
  reason: string
}

/** 统一错误响应 */
export interface ApiErrorBody {
  code: BusinessErrorCode
  message: string
  detail: ApiFieldError[] | null
  request_id: string
}

/** 统一响应包装（成功或失败） */
export type ApiEnvelope<T> = ApiSuccessBody<T> | ApiErrorBody

/** 错误码 → 中文兜底文案（后端 message 为空时使用） */
export const ERROR_CODE_TEXT: Record<BusinessErrorCode, string> = {
  VALIDATION_ERROR: '提交的参数不合法，请检查后重试',
  UNAUTHORIZED: '登录状态已失效，请重新登录',
  FORBIDDEN: '当前账号没有该操作权限',
  ITEM_NOT_FOUND: '物品记录不存在',
  CLAIM_NOT_FOUND: '认领记录不存在',
  MATCH_NOT_FOUND: '匹配记录不存在，请先执行匹配计算',
  STATE_CONFLICT: '当前状态不允许该操作，请刷新页面后重试',
  ALGORITHM_SERVICE_ERROR: '匹配算法服务暂时不可用，请稍后重试',
  STORAGE_SERVICE_ERROR: '对象存储服务异常，可去掉图片后重试',
  AI_SERVICE_ERROR: 'AI 分析服务异常，可继续发布并手动填写物品属性',
  RATE_LIMITED: '请求过于频繁，请稍后重试',
  INTERNAL_ERROR: '服务器内部错误，请稍后重试',
}

/** 分页请求参数：page 从 1 开始；page_size 默认 20、最大 50 */
export interface PageQuery {
  page?: number
  page_size?: number
}

export const DEFAULT_PAGE_SIZE = 20
export const MAX_PAGE_SIZE = 50

/** 分页返回结构（后端形态为 data.list + data.pagination，这里归一化为扁平结构） */
export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

/**
 * 分页结果归一化。兼容两种后端形态：
 * 1) 契约形态：{ list: [...], pagination: { page, page_size, total, total_pages } }
 * 2) 扁平形态：{ items / records / results: [...], total, page, page_size }
 */
export function toPageResult<T>(raw: unknown, fallbackPage = 1, fallbackSize = DEFAULT_PAGE_SIZE): PageResult<T> {
  const source = (raw ?? {}) as Record<string, unknown>
  const rawItems = source.items ?? source.list ?? source.records ?? source.results
  const items = Array.isArray(rawItems) ? (rawItems as T[]) : []
  const pagination = (source.pagination ?? {}) as Record<string, unknown>

  const totalValue = Number(pagination.total ?? source.total ?? source.count ?? items.length)
  const pageValue = Number(pagination.page ?? source.page ?? source.current ?? fallbackPage)
  const sizeValue = Number(pagination.page_size ?? source.page_size ?? source.pageSize ?? source.size ?? fallbackSize)
  const pagesValue = Number(pagination.total_pages ?? source.total_pages ?? 0)

  const total = Number.isFinite(totalValue) ? totalValue : items.length
  const pageSize = Number.isFinite(sizeValue) && sizeValue > 0 ? sizeValue : fallbackSize

  return {
    items,
    total,
    page: Number.isFinite(pageValue) && pageValue > 0 ? pageValue : fallbackPage,
    page_size: pageSize,
    total_pages: Number.isFinite(pagesValue) && pagesValue > 0 ? pagesValue : Math.max(1, Math.ceil(total / pageSize)),
  }
}

/** 把 page_size 收敛到 [1, MAX_PAGE_SIZE]，避免前端传出超过后端上限的值 */
export function clampPageSize(size?: number): number {
  const value = Number(size ?? DEFAULT_PAGE_SIZE)
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_PAGE_SIZE
  return Math.min(MAX_PAGE_SIZE, Math.floor(value))
}

/** 把 page 收敛为 >= 1 的整数 */
export function clampPage(page?: number): number {
  const value = Number(page ?? 1)
  if (!Number.isFinite(value) || value < 1) return 1
  return Math.floor(value)
}

/** 依赖健康状态（契约 §3.1：ok / degraded / down / not_configured） */
export type HealthState = 'ok' | 'degraded' | 'down' | 'not_configured'

/** 归一化后的依赖项（后端原文是对象映射，前端统一转成数组渲染） */
export interface HealthComponent {
  /** 原始键名，例如 database / redis / obs / algorithm_service / maas */
  name: string
  /** 中文展示名 */
  label: string
  status: HealthState
  message?: string | null
  latency_ms?: number | null
}

/** 归一化后的健康检查结果 */
export interface HealthStatus {
  status: HealthState
  env?: string | null
  version?: string | null
  uptime_seconds?: number | null
  components: HealthComponent[]
}

/** 依赖项中文名 */
export const HEALTH_COMPONENT_LABELS: Record<string, string> = {
  app: '应用进程',
  database: '数据库',
  redis: 'Redis 缓存',
  obs: '对象存储 OBS',
  algorithm_service: '算法服务',
  maas: 'MaaS 模型服务',
}

/** 状态 → 中文文案 */
export const HEALTH_STATE_LABELS: Record<HealthState, string> = {
  ok: '正常',
  degraded: '降级',
  down: '不可用',
  not_configured: '未配置',
}

/**
 * 平台级免责声明（首页与全局页脚共用，任何页面不得自行改写措辞）。
 * 与契约 §3.6 的 disclaimer 语义一致：系统只提供候选匹配与辅助建议，不做所有权自动认定。
 */
export const OWNERSHIP_DISCLAIMER =
  '本系统提供候选匹配和辅助审核建议，不构成物品所有权的自动认定。最终认领结果由平台管理员根据匹配依据、验证结果和实际交接情况确认。'
