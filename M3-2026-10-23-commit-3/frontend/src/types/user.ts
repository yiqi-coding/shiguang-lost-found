/**
 * 用户、鉴权与审计日志类型。
 * 字段命名对齐《拾光 API 契约（Baseline v1.0 · Frozen）》§3.2 与 §3.13：
 * 登录返回 access_token（JWT，有效期 2h），用户角色只有 student / admin 两种。
 */
import type { PageQuery } from './api'

/** 角色：student 普通用户（失主 / 拾到者）、admin 管理员 */
export type UserRole = 'student' | 'admin'

export const USER_ROLE_LABELS: Record<UserRole, string> = {
  student: '普通用户',
  admin: '管理员',
}

/** 当前登录用户（契约中 login 与 /auth/me 返回同一结构） */
export interface UserProfile {
  id: number
  username: string
  role: UserRole
  campus_id?: string | null
  /** 脱敏联系方式，例如 138****8888 */
  contact_id?: string | null
}

/** 登录入参（POST /auth/login） */
export interface LoginPayload {
  username: string
  password: string
}

/** 登录结果 */
export interface LoginResult {
  access_token: string
  token_type: string
  /** 有效期（秒），契约默认 7200 */
  expires_in: number
  user: UserProfile
}

/** 演示快捷登录入参（POST /auth/demo-login，仅 APP_ENV=demo 可用） */
export interface DemoLoginPayload {
  role: UserRole
}

/** 审计日志目标类型（契约 §3.13） */
export type AuditTargetType = 'item' | 'claim' | 'user' | 'config'

export const AUDIT_TARGET_TYPE_LABELS: Record<AuditTargetType, string> = {
  item: '物品',
  claim: '认领',
  user: '用户',
  config: '配置',
}

export const AUDIT_TARGET_TYPE_OPTIONS: ReadonlyArray<{ label: string; value: AuditTargetType }> = (
  Object.keys(AUDIT_TARGET_TYPE_LABELS) as AuditTargetType[]
).map((value) => ({ label: AUDIT_TARGET_TYPE_LABELS[value], value }))

/**
 * 审计日志（GET /admin/audit-logs）。
 * detail 由后端脱敏，禁止出现联系方式明文、隐藏特征明文与 Authorization。
 */
export interface AuditLog {
  id: number
  operator_id?: number | null
  operator_username?: string | null
  target_type: AuditTargetType
  target_id: number
  /** 动作标识，取值见契约 §2.8，例如 item.create / claim.verify / claim.review */
  action: string
  detail?: Record<string, unknown> | null
  request_id?: string | null
  created_at: string
}

/** 审计日志查询参数 */
export interface AuditLogQuery extends PageQuery {
  target_type?: AuditTargetType | ''
  target_id?: number | null
}
