/// <reference types="vite/client" />

/** .vue 单文件组件的类型声明（供 TS 识别 import Xxx from './Xxx.vue'） */
declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

/** 环境变量类型：与 .env.example 中的键保持一致 */
interface ImportMetaEnv {
  /** 应用标题（注入 index.html 与 document.title） */
  readonly VITE_APP_TITLE: string
  /** 接口前缀，默认 /api/v1 */
  readonly VITE_API_BASE_URL: string
  /** 请求超时（毫秒） */
  readonly VITE_API_TIMEOUT?: string
  /** 仅开发环境：devServer 代理目标 */
  readonly VITE_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
