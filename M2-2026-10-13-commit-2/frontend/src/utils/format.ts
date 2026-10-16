/**
 * 通用格式化工具：全站时间与文本展示格式统一在这里定义。
 * （需求未要求该目录，但它避免 6 个以上页面各写一套日期格式。）
 */

/** 把 ISO 8601 字符串解析为 Date；无法解析时返回 null */
function parseDate(value?: string | null): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function pad(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}

/** 日期时间：2026-09-30 18:43 */
export function formatDateTime(value?: string | null): string {
  const date = parseDate(value)
  if (!date) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 仅日期：2026-09-30 */
export function formatDate(value?: string | null): string {
  const date = parseDate(value)
  if (!date) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 相对时间：刚刚 / 12 分钟前 / 3 小时前 / 2 天前 / 具体日期 */
export function formatRelativeTime(value?: string | null): string {
  const date = parseDate(value)
  if (!date) return '—'

  const diff = Date.now() - date.getTime()
  if (diff < 60 * 1000) return '刚刚'
  if (diff < 60 * 60 * 1000) return `${Math.floor(diff / (60 * 1000))} 分钟前`
  if (diff < 24 * 60 * 60 * 1000) return `${Math.floor(diff / (60 * 60 * 1000))} 小时前`
  if (diff < 7 * 24 * 60 * 60 * 1000) return `${Math.floor(diff / (24 * 60 * 60 * 1000))} 天前`
  return formatDate(value)
}

/** 空值兜底：null / undefined / 空串统一显示 — */
export function safeText(value?: string | number | null, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  const text = String(value).trim()
  return text ? text : fallback
}

/**
 * 前端兜底脱敏（后端已脱敏时不会再触发）：
 * 手机号 13812345678 → 138****5678；其余文本保留首尾字符。
 */
export function maskMiddle(value?: string | null): string {
  const text = (value ?? '').trim()
  if (!text) return '—'
  if (/^1\d{10}$/.test(text)) return `${text.slice(0, 3)}****${text.slice(7)}`
  if (text.length <= 4) return `${text.slice(0, 1)}***`
  return `${text.slice(0, 3)}****${text.slice(-2)}`
}

/** JSON 详情折叠展示（审计日志用），保证可读且不抛异常 */
export function stringifyDetail(detail?: Record<string, unknown> | null): string {
  if (!detail || Object.keys(detail).length === 0) return '（无详情）'
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return '（详情无法序列化）'
  }
}
