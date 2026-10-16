<script setup lang="ts">
/**
 * 应用根组件：只负责全局骨架（AppHeader + RouterView + AppFooter + 回到顶部）。
 * 页面业务逻辑一律写在 src/views 下的视图组件中，这里不做数据请求。
 */
import AppHeader from '@/components/AppHeader.vue'
import AppFooter from '@/components/AppFooter.vue'
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <main class="app-shell__main">
      <router-view />
    </main>
    <AppFooter />
    <el-backtop :right="20" :bottom="72" />
  </div>
</template>

<!-- 全局基础样式（非 scoped）：reset 与布局容器，只在这里定义一次 -->
<style lang="scss">
* {
  box-sizing: border-box;
}

html,
body,
#app {
  height: 100%;
  margin: 0;
  padding: 0;
}

body {
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei',
    Arial, sans-serif;
  background-color: var(--sg-bg);
  color: var(--sg-text);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

a {
  color: var(--sg-primary);
  text-decoration: none;
}

.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.app-shell__main {
  flex: 1 1 auto;
  padding-bottom: var(--sg-gap-lg);
}
</style>
