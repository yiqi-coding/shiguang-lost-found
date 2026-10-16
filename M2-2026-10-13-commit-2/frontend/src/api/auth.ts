/**
 * 鉴权接口（契约 §3.2）：
 *   POST /auth/login       账号密码登录 → access_token（JWT，默认 2h）
 *   POST /auth/demo-login  演示快捷登录（仅 APP_ENV=demo 可用，线上返回 404）
 *   GET  /auth/me          当前用户，用于刷新页面后恢复会话
 *
 * 说明：契约未定义独立的登出接口（JWT 无状态），前端 logout 只清理本地会话；
 * 若后续后端提供 POST /auth/logout，只需在此补充实现。
 */
import { httpGet, httpPost } from './http'
import type { DemoLoginPayload, LoginPayload, LoginResult, UserProfile } from '@/types/user'

/** 账号密码登录 */
export function login(payload: LoginPayload): Promise<LoginResult> {
  return httpPost<LoginResult>('/auth/login', {
    username: payload.username.trim(),
    password: payload.password,
  })
}

/** 演示用一键切换身份（失主 / 拾到者 / 管理员） */
export function demoLogin(payload: DemoLoginPayload): Promise<LoginResult> {
  return httpPost<LoginResult>('/auth/demo-login', { role: payload.role })
}

/** 当前登录用户 */
export function fetchProfile(): Promise<UserProfile> {
  return httpGet<UserProfile>('/auth/me')
}
