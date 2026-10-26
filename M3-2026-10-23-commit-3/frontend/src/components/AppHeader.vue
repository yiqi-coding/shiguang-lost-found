<script setup lang="ts">
/**
 * AppHeader —— 全站页头。
 * 包含品牌区、主导航、发布入口、登录/用户菜单；管理员额外显示审核台与日志入口。
 * 响应式：≥768px 使用横向 el-menu，窄屏折叠为抽屉导航。
 *
 * 骨架阶段没有独立登录页，登录弹窗在这里承载：后端 POST /auth/login 就绪后即可直接联调。
 */
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { useUserStore } from '@/stores/user'

interface NavItem {
  path: string
  label: string
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/** 主导航：未登录也展示，点击后由路由守卫统一拦截并提示 */
const NAV_ITEMS: NavItem[] = [
  { path: '/', label: '首页' },
  { path: '/items', label: '物品列表' },
  { path: '/items/new/lost', label: '发布失物' },
  { path: '/items/new/found', label: '发布拾物' },
  { path: '/me', label: '我的记录' },
]

const ADMIN_ITEMS: NavItem[] = [
  { path: '/admin/review', label: '审核台' },
  { path: '/admin/logs', label: '日志' },
]

const visibleAdminItems = computed(() => (userStore.isAdmin ? ADMIN_ITEMS : []))

/** el-menu 高亮项：详情页、匹配页归入「物品列表」，认领页归入「我的记录」 */
const activeMenu = computed(() => {
  const path = route.path
  if (path === '/') return '/'
  if (path.startsWith('/items/new')) return path
  if (path.startsWith('/items')) return '/items'
  if (path.startsWith('/claims')) return '/me'
  if (path.startsWith('/admin/review')) return '/admin/review'
  if (path.startsWith('/admin/logs')) return '/admin/logs'
  return path
})

const shortName = computed(() => userStore.displayName.slice(0, 1))

const drawerVisible = ref(false)

/** ---------- 登录弹窗（骨架阶段占位） ---------- */
const loginForm = reactive<{ username: string; password: string }>({
  username: '',
  password: '',
})
const loginFormRef = ref<FormInstance>()

const loginRules: FormRules = {
  username: [
    { required: true, message: '请输入账号', trigger: 'blur' },
    { min: 3, max: 64, message: '账号长度需为 3~64 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度需为 6~64 个字符', trigger: 'blur' },
  ],
}

const loginVisible = computed({
  get: () => userStore.loginDialogVisible,
  set: (value: boolean) => {
    userStore.loginDialogVisible = value
  },
})

async function handleLogin(): Promise<void> {
  if (!loginFormRef.value) return
  try {
    await loginFormRef.value.validate()
  } catch {
    return
  }

  try {
    await userStore.login({ username: loginForm.username.trim(), password: loginForm.password })
    loginVisible.value = false
    loginForm.password = ''
    const redirect = route.query.redirect
    if (typeof redirect === 'string' && redirect) await router.replace(redirect)
  } catch {
    // 失败提示由 axios 响应拦截器统一弹出（账号或密码错误统一为 UNAUTHORIZED）
  }
}

/** 演示快捷登录：一键切换学生 / 管理员身份（需后端 APP_ENV=demo） */
async function handleDemoLogin(role: 'student' | 'admin'): Promise<void> {
  const user = await userStore.demoLogin(role)
  if (!user) return
  loginVisible.value = false
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect) await router.replace(redirect)
}

function handleLogout(): void {
  userStore.logout()
}

function handleUserCommand(command: string): void {
  if (command === 'logout') {
    void handleLogout()
    return
  }
  go(command)
}

function go(path: string): void {
  drawerVisible.value = false
  void router.push(path)
}

// 路由变化时收起移动端抽屉
watch(
  () => route.fullPath,
  () => {
    drawerVisible.value = false
  },
)

// 已登录但本地没有资料缓存（例如换了浏览器标签）时补拉一次
watch(
  () => userStore.isLoggedIn,
  (loggedIn) => {
    if (loggedIn && !userStore.profile) void userStore.refreshProfile()
  },
  { immediate: true },
)
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <div class="app-header__brand" role="button" tabindex="0" @click="go('/')" @keyup.enter="go('/')">
        <span class="app-header__logo">拾</span>
        <span class="app-header__brand-text">
          <strong>拾光</strong>
          <small class="sg-only-desktop">校园失物检索与可信认领</small>
        </span>
      </div>

      <nav class="app-header__nav sg-only-desktop">
        <el-menu :default-active="activeMenu" mode="horizontal" :ellipsis="false" router>
          <el-menu-item v-for="item in NAV_ITEMS" :key="item.path" :index="item.path">
            {{ item.label }}
          </el-menu-item>
          <el-menu-item v-for="item in visibleAdminItems" :key="item.path" :index="item.path">
            {{ item.label }}
          </el-menu-item>
        </el-menu>
      </nav>

      <div class="app-header__actions">
        <el-button class="sg-only-desktop" type="primary" @click="go('/items/new/lost')">
          <el-icon><Plus /></el-icon>
          <span>发布</span>
        </el-button>

        <el-dropdown v-if="userStore.isLoggedIn" @command="handleUserCommand">
          <span class="app-header__user">
            <el-avatar :size="26">{{ shortName }}</el-avatar>
            <span class="sg-only-desktop">{{ userStore.displayName }}</span>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="/me">我的记录</el-dropdown-item>
              <el-dropdown-item v-if="userStore.isAdmin" command="/admin/review">
                管理员审核台
              </el-dropdown-item>
              <el-dropdown-item v-if="userStore.isAdmin" command="/admin/logs">
                管理员日志
              </el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <el-button v-else type="primary" plain @click="loginVisible = true">登录</el-button>

        <el-button class="sg-only-mobile" text @click="drawerVisible = true">
          <el-icon :size="20"><Menu /></el-icon>
        </el-button>
      </div>
    </div>

    <el-drawer v-model="drawerVisible" title="导航" direction="rtl" size="76%">
      <el-menu :default-active="activeMenu">
        <el-menu-item
          v-for="item in [...NAV_ITEMS, ...visibleAdminItems]"
          :key="item.path"
          :index="item.path"
          @click="go(item.path)"
        >
          {{ item.label }}
        </el-menu-item>
      </el-menu>
      <el-divider />
      <div class="app-header__drawer-actions">
        <el-button type="primary" @click="go('/items/new/lost')">发布失物</el-button>
        <el-button @click="go('/items/new/found')">发布拾物</el-button>
      </div>
    </el-drawer>

    <el-dialog v-model="loginVisible" title="登录" width="440px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="登录方式"
        description="使用校园账号登录（POST /api/v1/auth/login，JWT 有效期 2 小时）。答辩演示时可用下方快捷身份一键切换（需后端 APP_ENV=demo）。"
      />
      <el-form
        ref="loginFormRef"
        class="app-header__login-form"
        :model="loginForm"
        :rules="loginRules"
        label-width="72px"
        @submit.prevent
      >
        <el-form-item label="账号" prop="username">
          <el-input v-model="loginForm.username" placeholder="例如 demo_student" clearable />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="请输入密码"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
      </el-form>

      <div class="app-header__demo-login">
        <span class="sg-muted">演示身份快捷登录：</span>
        <el-button size="small" :loading="userStore.loading" @click="handleDemoLogin('student')">
          学生用户
        </el-button>
        <el-button size="small" :loading="userStore.loading" @click="handleDemoLogin('admin')">
          管理员
        </el-button>
      </div>

      <template #footer>
        <el-button @click="loginVisible = false">取消</el-button>
        <el-button type="primary" :loading="userStore.loading" @click="handleLogin">登录</el-button>
      </template>
    </el-dialog>
  </header>
</template>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--sg-surface);
  border-bottom: 1px solid var(--sg-border-light);
  box-shadow: var(--sg-shadow-sm);
}

.app-header__inner {
  display: flex;
  align-items: center;
  gap: var(--sg-gap);
  max-width: var(--sg-container);
  min-height: var(--sg-header-height);
  margin: 0 auto;
  padding: 0 var(--sg-gap);
}

.app-header__brand {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-xs);
  cursor: pointer;
  flex: 0 0 auto;
}

.app-header__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--sg-primary);
  color: #fff;
  font-weight: 700;
}

.app-header__brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.app-header__brand-text strong {
  font-size: 16px;
}

.app-header__brand-text small {
  font-size: 11px;
  color: var(--sg-text-secondary);
}

.app-header__nav {
  flex: 1 1 auto;
  min-width: 0;
}

.app-header__nav :deep(.el-menu) {
  border-bottom: none;
}

.app-header__actions {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-xs);
  flex: 0 0 auto;
}

.app-header__user {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 14px;
  color: var(--sg-text);
  outline: none;
}

.app-header__login-form {
  margin-top: var(--sg-gap);
}

.app-header__demo-login {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sg-gap-xs);
  margin-top: var(--sg-gap-xs);
  padding-top: var(--sg-gap-xs);
  border-top: 1px dashed var(--sg-border-light);
}

.app-header__drawer-actions {
  display: flex;
  gap: var(--sg-gap-xs);
}

@media (max-width: 767px) {
  .app-header__inner {
    padding: 0 var(--sg-gap-sm);
  }

  .app-header__brand-text small {
    display: none;
  }
}
</style>
