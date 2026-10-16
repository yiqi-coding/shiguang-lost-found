/**
 * 登录用户状态：资料缓存 + 登录 / 演示登录 / 登出 + 角色判断。
 *
 * 页面统一通过 isLoggedIn / isAdmin 控制按钮与入口，避免各处重复判断角色；
 * token 本体由 session store 管理（axios 拦截器也需要读取，见 src/stores/session.ts）。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import * as authApi from '@/api/auth'
import {
  clearCachedProfile,
  getCachedProfile,
  saveCachedProfile,
  useSessionStore,
} from '@/stores/session'
import type { LoginPayload, LoginResult, UserProfile, UserRole } from '@/types/user'
import { USER_ROLE_LABELS } from '@/types/user'

export const useUserStore = defineStore('user', () => {
  const session = useSessionStore()

  const profile = ref<UserProfile | null>(getCachedProfile())
  const loading = ref(false)
  /** 骨架阶段没有独立登录页，登录弹窗由 AppHeader 承载 */
  const loginDialogVisible = ref(false)

  const isLoggedIn = computed(() => session.hasToken && !session.isExpired)
  const isAdmin = computed(() => isLoggedIn.value && profile.value?.role === 'admin')
  const displayName = computed(() => profile.value?.username || '未登录')
  const roleLabel = computed(() => (profile.value ? USER_ROLE_LABELS[profile.value.role] : '访客'))

  /** 写入会话与资料（登录 / 演示登录共用） */
  function applyLoginResult(result: LoginResult): void {
    session.setSession(result.access_token, result.expires_in)
    profile.value = result.user
    saveCachedProfile(result.user)
  }

  /** 刷新页面后从本地缓存恢复资料（守卫每次导航都会调用，必须是幂等的） */
  function restoreFromStorage(): void {
    if (!profile.value) profile.value = getCachedProfile()
  }

  /** 账号密码登录 */
  async function login(payload: LoginPayload): Promise<UserProfile> {
    loading.value = true
    try {
      const result = await authApi.login(payload)
      applyLoginResult(result)
      ElMessage.success(`欢迎回来，${result.user.username}`)
      return result.user
    } finally {
      loading.value = false
    }
  }

  /**
   * 演示快捷登录（仅 APP_ENV=demo 的后端可用，用于答辩时一键切换身份）。
   * 失败时返回 null，由调用方决定提示文案。
   */
  async function demoLogin(role: UserRole): Promise<UserProfile | null> {
    loading.value = true
    try {
      const result = await authApi.demoLogin({ role })
      applyLoginResult(result)
      ElMessage.success(`已切换为演示身份：${USER_ROLE_LABELS[role]}`)
      return result.user
    } catch {
      ElMessage.warning('演示登录不可用（需要后端 APP_ENV=demo）')
      return null
    } finally {
      loading.value = false
    }
  }

  /** 拉取最新资料（token 有效但本地无缓存时使用） */
  async function refreshProfile(): Promise<UserProfile | null> {
    if (!isLoggedIn.value) return null
    loading.value = true
    try {
      const result = await authApi.fetchProfile()
      profile.value = result
      saveCachedProfile(result)
      return result
    } catch {
      // 拉取失败不强制登出：可能只是网络抖动，保留本地缓存
      return profile.value
    } finally {
      loading.value = false
    }
  }

  /** 清理本地会话（401 或主动退出） */
  function clearSession(): void {
    session.reset()
    profile.value = null
    clearCachedProfile()
  }

  /**
   * 退出登录。
   * JWT 无状态，契约未定义登出接口，因此这里只清理本地会话。
   */
  function logout(): void {
    clearSession()
    ElMessage.success('已退出登录')
  }

  /**
   * 需要登录的操作前调用：未登录时提示并返回 false。
   * @param tip 提示文案，例如「发起认领前请先登录」
   */
  function ensureLogin(tip = '该操作需要登录后进行'): boolean {
    if (isLoggedIn.value) return true
    ElMessage.warning(tip)
    loginDialogVisible.value = true
    return false
  }

  return {
    profile,
    loading,
    loginDialogVisible,
    isLoggedIn,
    isAdmin,
    displayName,
    roleLabel,
    restoreFromStorage,
    login,
    demoLogin,
    refreshProfile,
    clearSession,
    logout,
    ensureLogin,
  }
})
