<script setup lang="ts">
/**
 * PrivacyHint —— 隐私与合规提示条。
 * 全站只在这里维护「隐藏特征可见范围」「联系方式脱敏」「匹配不等于归属」等措辞，
 * 各页面通过 level / items 组合复用，避免同一规则出现多种说法。
 */
interface Props {
  /** 提示标题 */
  title?: string
  /** 提示级别：info 说明 / warning 强调隐私风险 */
  level?: 'info' | 'success' | 'warning' | 'error'
  /** 提示条目；不传时使用隐藏特征默认说明 */
  items?: string[]
  /** 紧凑模式（缩小行距，用于表单内部） */
  compact?: boolean
  /** 是否可关闭（默认不可关闭，合规提示应常驻） */
  closable?: boolean
}

/**
 * 默认文案：隐藏特征与敏感信息处理规则。
 *
 * 注意：必须内联在 withDefaults() 里，不能引用本文件的局部常量 ——
 * defineProps 的默认值会被编译器提升到 setup() 之外，引用局部变量会报
 * "cannot reference locally declared variables"。
 */
const props = withDefaults(defineProps<Props>(), {
  title: '隐私提示',
  level: 'warning',
  items: () => [
    '隐藏特征仅发布者本人与平台管理员可见，不会出现在物品列表、详情公开区域或匹配依据中。',
    '认领申请人只会看到系统根据隐藏特征生成的验证问题，无法看到你填写的原始内容。',
    '请勿在隐藏特征中填写身份证号、完整手机号等敏感信息，建议描述划痕、贴纸、序列号后四位等只有本人知道的细节。',
  ],
  compact: false,
  closable: false,
})
</script>

<template>
  <el-alert
    class="privacy-hint"
    :class="{ 'privacy-hint--compact': props.compact }"
    :type="props.level"
    :title="props.title"
    :closable="props.closable"
    show-icon
  >
    <ul v-if="props.items.length > 0" class="privacy-hint__list">
      <li v-for="(item, index) in props.items" :key="index">{{ item }}</li>
    </ul>
    <slot />
  </el-alert>
</template>

<style scoped>
.privacy-hint {
  margin-bottom: var(--sg-gap-sm);
  align-items: flex-start;
}

.privacy-hint__list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
}

.privacy-hint__list li + li {
  margin-top: 4px;
}

.privacy-hint--compact :deep(.el-alert__description) {
  margin-top: 2px;
}
</style>
