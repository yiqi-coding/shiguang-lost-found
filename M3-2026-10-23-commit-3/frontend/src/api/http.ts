/**
 * axios 统一封装（全站唯一的 HTTP 出口）。
 *
 * 1) 请求头：自动注入 X-Request-ID（crypto.randomUUID）与 Authorization: Bearer <token>；
 * 2) 响应解包：{ code: 'OK', message, data, request_id } → 直接返回 data；
 * 3) 错误处理：业务错误码 + HTTP 错误统一转成 ApiError，并弹出 ElMessage.error；
 * 4) 401：清理本地会话并回调 unauthorizedHandler（由 router 注册跳转逻辑，避免循环依赖）。
 *
 * 注意：这里刻意不设置全局 Content-Type，
 * 否则 axios 会把 multipart/form-data 的 FormData 序列化成 JSON，导致图片上传失败。
 */
import axios, { AxiosError, AxiosHeaders } from 'axios'
import type { AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiEnvelope, ApiErrorBody, BusinessErrorCode } from '@/types/api'
import { ERROR_CODE_TEXT } from '@/types/api'
import { clearToken, getToken } from '@/stores/session'

/** 业务错误码之外的本地错误码 */
export type LocalErrorCode = 'NETWORK_ERROR' | 'TIMEOUT' | 'UNKNOWN_ERROR'

export type HttpErrorCode = BusinessErrorCode | LocalErrorCode

/** 接口前缀：默认 /api/v1，可在 .env.* 中通过 VITE_API_BASE_URL 覆盖 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || '/api/v1'

/** 默认超时时间（毫秒） */
export const API_TIMEOUT: number = Number(import.meta.env.VITE_API_TIMEOUT ?? 15000) || 15000

/** 请求追踪头，后端应把它写入审计日志（契约 §6） */
export const REQUEST_ID_HEADER = 'X-Request-ID'

/** 幂等头：发布、认领、审核等写操作建议携带（契约 §1.1） */
export const IDEMPOTENCY_HEADER = 'X-Idempotency-Key'

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 为 true 时不弹出全局错误提示（用于健康检查、轮询等静默请求） */
    silent?: boolean
    /** 自定义请求追踪 ID；不传则自动生成 */
    requestId?: string
    /**
     * 幂等键：发布 / 认领 / 审核等写操作传入，防止重复提交产生多条记录。
     * 不传则不携带该请求头。
     */
    idempotencyKey?: string
  }
}

/** 统一的前端错误对象：页面通过 code 判断业务分支（如 ITEM_NOT_FOUND 展示空状态） */
export class ApiError extends Error {
  readonly code: HttpErrorCode
  readonly detail: unknown
  readonly requestId: string | null
  readonly httpStatus: number | null

  constructor(init: {
    code: HttpErrorCode
    message: string
    detail?: unknown
    requestId?: string | null
    httpStatus?: number | null
  }) {
    super(init.message)
    this.name = 'ApiError'
    this.code = init.code
    this.detail = init.detail ?? null
    this.requestId = init.requestId ?? null
    this.httpStatus = init.httpStatus ?? null
  }
}

let unauthorizedHandler: (() => void) | null = null

/** 注册 401 处理逻辑（由 src/router/index.ts 在初始化时注入，避免 http ↔ router 循环依赖） */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler
}

/** 生成请求追踪 ID：优先使用 crypto.randomUUID，非安全上下文下降级 */
export function createRequestId(): string {
  const cryptoObj = typeof globalThis.crypto === 'undefined' ? undefined : globalThis.crypto
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return `req_${cryptoObj.randomUUID()}`
  }
  return `req_${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/** 生成幂等键：发布 / 认领 / 审核等写操作使用，避免网络重试造成重复记录 */
export function createIdempotencyKey(): string {
  return createRequestId().replace(/^req_/, 'idem_')
}

const service: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  headers: { Accept: 'application/json' },
})

service.interceptors.request.use((config) => {
  const headers = AxiosHeaders.from(config.headers)
  headers.set(REQUEST_ID_HEADER, config.requestId || createRequestId())
  if (config.idempotencyKey) headers.set(IDEMPOTENCY_HEADER, config.idempotencyKey)

  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  config.headers = headers
  return config
})

/** 判断响应体是否为统一成功包装（用于兼容「HTTP 503 但业务体为 OK」的健康检查场景） */
function isSuccessEnvelope(body: unknown): boolean {
  return Boolean(body && typeof body === 'object' && (body as ApiEnvelope<unknown>).code === 'OK')
}

/** 解包统一响应包装；非包装响应（如二进制流）原样返回 */
function unwrap(response: AxiosResponse<ApiEnvelope<unknown>>): unknown {
  const responseType = response.config.responseType
  if (responseType === 'blob' || responseType === 'arraybuffer') return response.data

  const body = response.data
  if (!body || typeof body !== 'object' || !('code' in body)) return body

  const envelope = body as ApiEnvelope<unknown>
  if (envelope.code === 'OK') return envelope.data

  throw new ApiError({
    code: envelope.code,
    message: envelope.message || ERROR_CODE_TEXT[envelope.code] || '请求失败',
    detail: envelope.detail,
    requestId: envelope.request_id,
    httpStatus: response.status,
  })
}

/** HTTP 状态码 → 业务错误码（后端未返回包装体时的兜底，映射见契约 §1.2） */
function statusToErrorCode(status: number): HttpErrorCode {
  if (status === 400 || status === 413 || status === 422) return 'VALIDATION_ERROR'
  if (status === 401) return 'UNAUTHORIZED'
  if (status === 403) return 'FORBIDDEN'
  if (status === 404) return 'UNKNOWN_ERROR'
  if (status === 409) return 'STATE_CONFLICT'
  if (status === 429) return 'RATE_LIMITED'
  if (status === 502) return 'ALGORITHM_SERVICE_ERROR'
  if (status === 503) return 'INTERNAL_ERROR'
  if (status >= 500) return 'INTERNAL_ERROR'
  return 'UNKNOWN_ERROR'
}

function statusToMessage(status: number): string {
  const code = statusToErrorCode(status)
  if (code === 'UNKNOWN_ERROR') return `请求失败（HTTP ${status}），请稍后重试`
  return ERROR_CODE_TEXT[code as BusinessErrorCode] ?? `请求失败（HTTP ${status}）`
}

/** 401 统一处理：清理 token 并通知路由跳转 */
function handleUnauthorized(): void {
  clearToken()
  unauthorizedHandler?.()
}

/** axios 异常 → ApiError */
function normalizeError(error: AxiosError<ApiErrorBody>): ApiError {
  const status = error.response?.status ?? null
  const body = error.response?.data

  if (status !== null && body && typeof body === 'object' && 'code' in body) {
    const envelope = body as ApiErrorBody
    if (status === 401) handleUnauthorized()
    return new ApiError({
      code: envelope.code,
      message: envelope.message || ERROR_CODE_TEXT[envelope.code] || statusToMessage(status),
      detail: envelope.detail,
      requestId: envelope.request_id,
      httpStatus: status,
    })
  }

  if (status !== null) {
    if (status === 401) handleUnauthorized()
    return new ApiError({
      code: statusToErrorCode(status),
      message: statusToMessage(status),
      httpStatus: status,
    })
  }

  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
    return new ApiError({
      code: 'TIMEOUT',
      message: `请求超时（超过 ${API_TIMEOUT} ms），请检查网络后重试`,
    })
  }

  return new ApiError({
    code: 'NETWORK_ERROR',
    message: '网络异常或后端服务未启动，请确认后端已运行（默认 http://127.0.0.1:8000）',
  })
}

/** 统一错误提示：401 由 unauthorizedHandler 负责提示，避免重复弹窗 */
function notifyError(error: ApiError, silent?: boolean): void {
  if (import.meta.env.DEV) {
    console.warn(`[api] ${error.code}: ${error.message}`, {
      requestId: error.requestId,
      detail: error.detail,
    })
  }
  if (silent) return
  if (error.code === 'UNAUTHORIZED') return
  ElMessage.error(error.message)
}

service.interceptors.response.use(
  (response) => {
    try {
      // 拦截器实际返回的是业务 data，与 axios 声明的 AxiosResponse 类型不一致，
      // 这里显式转换一次；对外由 httpRequest<T>() 还原为 Promise<T>，业务层拿到的始终是 data。
      return unwrap(response) as unknown as AxiosResponse
    } catch (error) {
      const apiError =
        error instanceof ApiError
          ? error
          : new ApiError({ code: 'UNKNOWN_ERROR', message: '响应数据解析失败' })
      notifyError(apiError, response.config.silent)
      return Promise.reject(apiError)
    }
  },
  (error: AxiosError<ApiErrorBody>) => {
    // 健康检查在依赖不可用时返回 503，但业务体仍是统一成功包装（契约 §3.1），
    // 这种情况按成功处理，避免首页把「依赖降级」误报成请求失败。
    if (error.response && isSuccessEnvelope(error.response.data)) {
      try {
        return unwrap(error.response as AxiosResponse<ApiEnvelope<unknown>>) as unknown as AxiosResponse
      } catch {
        // 落到下面的统一错误处理
      }
    }

    const apiError = normalizeError(error)
    notifyError(apiError, error.config?.silent)
    return Promise.reject(apiError)
  },
)

/** 通用请求：返回解包后的业务数据 */
export function httpRequest<T>(config: AxiosRequestConfig): Promise<T> {
  return service.request<unknown, T>(config)
}

/** GET */
export function httpGet<T>(
  url: string,
  params?: Record<string, unknown>,
  config: AxiosRequestConfig = {},
): Promise<T> {
  return httpRequest<T>({ ...config, url, method: 'GET', params })
}

/** POST（JSON） */
export function httpPost<T>(
  url: string,
  data?: unknown,
  config: AxiosRequestConfig = {},
): Promise<T> {
  return httpRequest<T>({ ...config, url, method: 'POST', data })
}

/** POST（multipart/form-data）；不要手动设置 Content-Type，交给浏览器补全 boundary */
export function httpPostForm<T>(
  url: string,
  form: FormData,
  config: AxiosRequestConfig = {},
): Promise<T> {
  return httpRequest<T>({ ...config, url, method: 'POST', data: form })
}

/** 原始实例：需要取消请求、上传进度等高级能力时使用 */
export { service as axiosInstance }
