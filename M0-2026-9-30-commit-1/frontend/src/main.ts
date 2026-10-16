/**
 * 应用入口：
 * 1) 全量注册 Element Plus（骨架阶段优先「少配置、易评审」，后续可换按需引入）；
 * 2) 全量注册 @element-plus/icons-vue 图标，模板中可直接使用 <Search /> 等；
 * 3) 样式加载顺序：Element Plus 基础样式 → 项目变量（覆盖主题色）→ 根组件全局样式。
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import '@/styles/variables.scss'

import App from '@/App.vue'
import router from '@/router'

const app = createApp(App)

// 图标全量注册：模板里可直接用名称引用，例如 <el-icon><Search /></el-icon>
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
