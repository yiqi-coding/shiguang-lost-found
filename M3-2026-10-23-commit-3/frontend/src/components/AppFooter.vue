<script setup lang="ts">
/**
 * AppFooter —— 全站页脚：快捷入口 + 免责声明 + 版权信息。
 * 免责声明文案统一取自 src/types/api.ts 的 OWNERSHIP_DISCLAIMER（全站唯一来源，禁止各页面改写）。
 */
import { computed } from 'vue'
import { OWNERSHIP_DISCLAIMER } from '@/types/api'

const currentYear = computed(() => new Date().getFullYear())

const links: Array<{ label: string; path: string }> = [
  { label: '首页', path: '/' },
  { label: '物品列表', path: '/items' },
  { label: '发布失物', path: '/items/new/lost' },
  { label: '发布拾物', path: '/items/new/found' },
  { label: '我的记录', path: '/me' },
]
</script>

<template>
  <footer class="app-footer">
    <div class="app-footer__inner">
      <div class="app-footer__top">
        <div class="app-footer__brand">
          <span class="app-footer__logo">拾</span>
          <div>
            <strong>拾光</strong>
            <p class="app-footer__slogan">校园多模态失物检索与可信认领平台</p>
          </div>
        </div>

        <nav class="app-footer__links">
          <router-link v-for="link in links" :key="link.path" :to="link.path">
            {{ link.label }}
          </router-link>
        </nav>
      </div>

      <el-alert
        class="app-footer__disclaimer"
        type="warning"
        :closable="false"
        show-icon
        title="免责声明"
      >
        <p class="app-footer__disclaimer-text">{{ OWNERSHIP_DISCLAIMER }}</p>
        <p class="app-footer__disclaimer-text">
          隐藏特征与联系方式按最小必要原则使用：隐藏特征仅发布者与管理员可见，联系方式在审核台以脱敏形式展示。
        </p>
      </el-alert>

      <p class="app-footer__copyright">
        © {{ currentYear }} 拾光 · 校园多模态失物检索与可信认领平台 | 华为云 AI 创新应用竞赛作品
      </p>
    </div>
  </footer>
</template>

<style scoped>
.app-footer {
  margin-top: var(--sg-gap-lg);
  background: var(--sg-surface);
  border-top: 1px solid var(--sg-border-light);
}

.app-footer__inner {
  max-width: var(--sg-container);
  margin: 0 auto;
  padding: var(--sg-gap-lg) var(--sg-gap) var(--sg-gap);
}

.app-footer__top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-sm);
}

.app-footer__brand {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-xs);
}

.app-footer__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--sg-primary);
  color: #fff;
  font-weight: 700;
}

.app-footer__slogan {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--sg-text-secondary);
}

.app-footer__links {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap);
  font-size: 13px;
}

.app-footer__disclaimer {
  margin: var(--sg-gap) 0;
  align-items: flex-start;
}

.app-footer__disclaimer-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.app-footer__disclaimer-text + .app-footer__disclaimer-text {
  margin-top: 4px;
}

.app-footer__copyright {
  margin: 0;
  text-align: center;
  font-size: 12px;
  color: var(--sg-text-placeholder);
}
</style>
