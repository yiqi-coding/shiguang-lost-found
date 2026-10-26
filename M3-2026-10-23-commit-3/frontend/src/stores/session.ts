/**
 * 会话与本地缓存：token、登录时间、用户资料快照。
 *
 * 设计说明：axios 拦截器（src/api/http.ts）需要在任意时刻读取 token，
 * 但拦截器不能依赖 pinia 的激活实例，因此这里先提供一组与框架无关的函数，
 * 再由 useSessionStore 包装成响应式状态，二者共用同一份 localStorage 数据。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { CreateClaimResult } from '@/types/claim'
import type { UserProfile } from '@/types/user'

const TOKEN_KEY = 'sg_token'
const TOKEN_EXPIRES_AT_KEY = 'sg_token_expires_at'
const LOGIN_AT_KEY = 'sg_login_at'
const PROFILE_KEY = 'sg_user_profile'
const PENDING_CLAIM_KEY = 'sg_pending_claim'

/** 本地缓存读写：隐私模式下 localStorage 可能抛异常，统一降级为不缓存 */
function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // 忽略：无法写入时仅失去「刷新后保持登录」的能力，不影响当前会话
  }
}

function removeStorage(key: string): void {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // 忽略
  }
}

/** 进程内缓存，减少高频 localStorage 读取 */
let tokenCache: string | null = null
let tokenExpiresAtCache = -1
let loginAtCache = -1

/** 读取 token（未登录返回 null） */
export function getToken(): string | null {
  if (tokenCache === null) tokenCache = readStorage(TOKEN_KEY)
  return tokenCache
}

/** token 过期时间戳（毫秒）；0 表示后端未返回有效期 */
export function getTokenExpiresAt(): number {
  if (tokenExpiresAtCache < 0) {
    const raw = readStorage(TOKEN_EXPIRES_AT_KEY)
    tokenExpiresAtCache = raw ? Number(raw) || 0 : 0
  }
  return tokenExpiresAtCache
}

/** 最近一次登录时间戳（毫秒），未登录返回 null */
export function getLoginAt(): number | null {
  if (loginAtCache < 0) {
    const raw = readStorage(LOGIN_AT_KEY)
    loginAtCache = raw ? Number(raw) || 0 : 0
  }
  return loginAtCache > 0 ? loginAtCache : null
}

/** 是否已过期：无 token 或已超过有效期均视为过期 */
export function isTokenExpired(): boolean {
  if (!getToken()) return true
  const expiresAt = getTokenExpiresAt()
  return expiresAt > 0 && Date.now() >= expiresAt
}

/** 保存 token（expiresInSeconds 来自登录接口的 expires_in） */
export function saveToken(token: string, expiresInSeconds?: number): void {
  tokenCache = token
  writeStorage(TOKEN_KEY, token)

  const expiresAt = expiresInSeconds && expiresInSeconds > 0 ? Date.now() + expiresInSeconds * 1000 : 0
  tokenExpiresAtCache = expiresAt
  if (expiresAt > 0) writeStorage(TOKEN_EXPIRES_AT_KEY, String(expiresAt))
  else removeStorage(TOKEN_EXPIRES_AT_KEY)

  loginAtCache = Date.now()
  writeStorage(LOGIN_AT_KEY, String(loginAtCache))
}

/** 清理 token（401、主动退出时调用） */
export function clearToken(): void {
  tokenCache = null
  tokenExpiresAtCache = 0
  loginAtCache = 0
  removeStorage(TOKEN_KEY)
  removeStorage(TOKEN_EXPIRES_AT_KEY)
  removeStorage(LOGIN_AT_KEY)
}

/** 读取缓存的用户资料（刷新页面后恢复登录态用） */
export function getCachedProfile(): UserProfile | null {
  const raw = readStorage(PROFILE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as UserProfile
  } catch {
    removeStorage(PROFILE_KEY)
    return null
  }
}

/** 缓存用户资料 */
export function saveCachedProfile(profile: UserProfile): void {
  writeStorage(PROFILE_KEY, JSON.stringify(profile))
}

/** 清除用户资料缓存 */
export function clearCachedProfile(): void {
  removeStorage(PROFILE_KEY)
}

/**
 * 暂存刚创建的认领（含 2~3 个验证问题）。
 * 用途：认领验证页优先调用 GET /claims/{claim_id} 恢复问题列表；
 * 若后端尚未实现该接口，则用这里的暂存数据降级展示，避免用户刚创建认领就看不到问题。
 * 只放 sessionStorage：关闭标签页即失效，不长期保存。
 */
export function savePendingClaim(claim: CreateClaimResult): void {
  try {
    window.sessionStorage.setItem(PENDING_CLAIM_KEY, JSON.stringify(claim))
  } catch {
    // 忽略：无 sessionStorage 时页面会提示从匹配结果页重新发起认领
  }
}

/** 读取暂存的认领；claimId 不匹配则视为无效 */
export function readPendingClaim(claimId: number): CreateClaimResult | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_CLAIM_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as CreateClaimResult
    return Number(parsed.claim_id) === Number(claimId) ? parsed : null
  } catch {
    return null
  }
}

/** 清除暂存的认领（提交验证成功后调用） */
export function clearPendingClaim(): void {
  try {
    window.sessionStorage.removeItem(PENDING_CLAIM_KEY)
  } catch {
    // 忽略
  }
}

/** 响应式会话状态：供页面展示登录时间 / 过期提示 */
export const useSessionStore = defineStore('session', () => {
  const token = ref<string | null>(getToken())
  const expiresAt = ref<number>(getTokenExpiresAt())
  const loginAt = ref<number | null>(getLoginAt())

  const hasToken = computed(() => Boolean(token.value))
  const isExpired = computed(() => (expiresAt.value > 0 ? Date.now() >= expiresAt.value : false))

  /** 写入会话（登录成功后调用） */
  function setSession(nextToken: string, expiresInSeconds?: number): void {
    saveToken(nextToken, expiresInSeconds)
    token.value = nextToken
    expiresAt.value = getTokenExpiresAt()
    loginAt.value = getLoginAt()
  }

  /** 清空会话（退出登录 / 401 时调用） */
  function reset(): void {
    clearToken()
    clearCachedProfile()
    token.value = null
    expiresAt.value = 0
    loginAt.value = null
  }

  return { token, expiresAt, loginAt, hasToken, isExpired, setSession, reset }
})
