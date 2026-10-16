/**
 * 路由表与全局守卫。
 *
 * 路由路径与需求说明书的页面清单一一对应（见 README 的「页面与路由对照表」）；
 * /items/new/lost、/items/new/found 必须声明在 /items/:id 之前，否则会被动态参数吃掉。
 *
 * 守卫只做体验层拦截：requiresAuth 未登录时回到首页并提示，
 * requiresAdmin 非管理员直接拒绝；真正的权限校验由后端完成。
 */
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { setUnauthorizedHandler } from '@/api/http'
import { useUserStore } from '@/stores/user'

declare module 'vue-router' {
  interface RouteMeta {
    /** 页面标题（用于 document.title） */
    title: string
    /** 是否需要登录 */
    requiresAuth?: boolean
    /** 是否需要管理员角色 */
    requiresAdmin?: boolean
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { title: '首页' },
  },
  {
    path: '/items',
    name: 'item-list',
    component: () => import('@/views/ItemListView.vue'),
    meta: { title: '物品列表' },
  },
  {
    path: '/items/new/lost',
    name: 'item-create-lost',
    component: () => import('@/views/ItemCreateLostView.vue'),
    meta: { title: '发布失物', requiresAuth: true },
  },
  {
    path: '/items/new/found',
    name: 'item-create-found',
    component: () => import('@/views/ItemCreateFoundView.vue'),
    meta: { title: '发布拾物', requiresAuth: true },
  },
  {
    path: '/items/:id',
    name: 'item-detail',
    component: () => import('@/views/ItemDetailView.vue'),
    meta: { title: '物品详情' },
  },
  {
    path: '/items/:id/matches',
    name: 'item-matches',
    component: () => import('@/views/MatchResultView.vue'),
    meta: { title: '匹配结果' },
  },
  {
    path: '/claims/:id/verify',
    name: 'claim-verify',
    component: () => import('@/views/ClaimVerifyView.vue'),
    meta: { title: '认领验证', requiresAuth: true },
  },
  {
    path: '/me',
    name: 'my-records',
    component: () => import('@/views/MyRecordsView.vue'),
    meta: { title: '我的记录', requiresAuth: true },
  },
  {
    path: '/admin/review',
    name: 'admin-review',
    component: () => import('@/views/AdminReviewView.vue'),
    meta: { title: '管理员审核台', requiresAuth: true, requiresAdmin: true },
  },
  {
    path: '/admin/logs',
    name: 'admin-logs',
    component: () => import('@/views/AdminLogsView.vue'),
    meta: { title: '管理员日志', requiresAuth: true, requiresAdmin: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

/** 应用标题后缀（来自环境变量） */
const APP_TITLE = import.meta.env.VITE_APP_TITLE || '拾光'

router.beforeEach((to) => {
  const userStore = useUserStore()
  userStore.restoreFromStorage()

  if (to.meta.requiresAuth && !userStore.isLoggedIn) {
    ElMessage.warning('请先登录后再访问该页面')
    return { name: 'home', query: { redirect: to.fullPath, need_login: '1' } }
  }

  if (to.meta.requiresAdmin && !userStore.isAdmin) {
    ElMessage.error('该页面仅管理员可访问')
    return { name: 'home' }
  }

  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · ${APP_TITLE}` : APP_TITLE
})

// 401 统一处理：清会话 + 提示 + 回首页（带 redirect，登录后可跳回）
setUnauthorizedHandler(() => {
  const userStore = useUserStore()
  const current = router.currentRoute.value
  userStore.clearSession()
  ElMessage.warning('登录状态已失效，请重新登录')
  if (current.name !== 'home') {
    void router.replace({ name: 'home', query: { redirect: current.fullPath, need_login: '1' } })
  }
})

export default router
