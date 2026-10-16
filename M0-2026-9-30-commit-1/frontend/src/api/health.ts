/**
 * 健康检查接口（契约 §3.1）：GET /health
 * 返回各依赖（app / database / redis / obs / algorithm_service / maas）的配置状态与连通性，
 * 不得返回任何密钥；任一依赖 down 时后端仍返回 200/503 但业务体为 OK，由 http.ts 统一放行。
 *
 * 前端把「对象映射」形态归一化为数组，便于首页直接渲染标签。
 */
import { httpGet } from './http'
import type { HealthComponent, HealthState, HealthStatus } from '@/types/api'
import { HEALTH_COMPONENT_LABELS } from '@/types/api'

/** 原始状态值归一化：兼容 unconfigured 这种写法 */
function toHealthState(value: unknown): HealthState {
  if (value === 'ok' || value === 'degraded' || value === 'down' || value === 'not_configured') {
    return value
  }
  if (value === 'unconfigured') return 'not_configured'
  return 'down'
}

/** 把后端返回的 health data 归一化为 { status, components[] } */
export function normalizeHealth(raw: unknown): HealthStatus {
  const source = (raw ?? {}) as Record<string, unknown>

  let env: string | null = null
  let version: string | null = null
  let uptime: number | null = null
  const components: HealthComponent[] = []

  Object.keys(source).forEach((key) => {
    const value = source[key]
    if (!value || typeof value !== 'object' || Array.isArray(value)) return

    const entry = value as Record<string, unknown>
    const status = toHealthState(entry.status)

    if (key === 'app') {
      env = typeof entry.env === 'string' ? entry.env : null
      version = typeof entry.version === 'string' ? entry.version : null
      uptime = typeof entry.uptime_seconds === 'number' ? entry.uptime_seconds : null
    }

    components.push({
      name: key,
      label: HEALTH_COMPONENT_LABELS[key] ?? key,
      status,
      message: typeof entry.message === 'string' ? entry.message : null,
      latency_ms: typeof entry.latency_ms === 'number' ? entry.latency_ms : null,
    })
  })

  // 整体状态：任一 down → down；存在 degraded → degraded；not_configured 不算故障
  let status: HealthState = 'ok'
  if (components.some((item) => item.status === 'down')) status = 'down'
  else if (components.some((item) => item.status === 'degraded')) status = 'degraded'

  return { status, env, version, uptime_seconds: uptime, components }
}

/** 健康检查（根路径，契约附录 A 的写法）：GET /health */
export async function fetchRootHealth(): Promise<HealthStatus> {
  const data = await httpGet<unknown>('/health', undefined, {
    silent: true,
    timeout: 5000,
    baseURL: '/',
  })
  return normalizeHealth(data)
}

/**
 * 兜底：若部署把健康检查放在接口前缀下，使用该函数（/api/v1/health）。
 * 首页优先尝试根路径，失败后自动降级调用本函数。
 */
export async function fetchHealth(): Promise<HealthStatus> {
  const data = await httpGet<unknown>('/health', undefined, { silent: true, timeout: 5000 })
  return normalizeHealth(data)
}
