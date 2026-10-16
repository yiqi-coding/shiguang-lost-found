<script setup lang="ts">
/**
 * 404 页 NotFoundView —— 路由 /:pathMatch(.*)*
 * 职责：兜底未知路径，提示用户返回首页或物品列表。
 * 依赖接口：无。
 */
import { useRouter } from 'vue-router'

const router = useRouter()

function goHome(): void {
  void router.push({ name: 'home' })
}

function goList(): void {
  void router.push({ name: 'item-list' })
}

function goBack(): void {
  router.back()
}
</script>

<template>
  <div class="not-found sg-page">
    <el-result icon="warning" title="404" sub-title="页面不存在或链接已失效">
      <template #extra>
        <div class="not-found__actions">
          <el-button type="primary" @click="goHome">返回首页</el-button>
          <el-button @click="goList">浏览物品列表</el-button>
          <el-button text @click="goBack">返回上一页</el-button>
        </div>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  justify-content: center;
}

.not-found__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--sg-gap-xs);
}

@media (max-width: 767px) {
  .not-found__actions .el-button {
    margin-left: 0;
  }
}
</style>
