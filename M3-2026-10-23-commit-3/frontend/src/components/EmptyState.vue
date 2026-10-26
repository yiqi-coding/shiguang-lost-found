<script setup lang="ts">
/**
 * EmptyState —— 统一空状态 / 错误状态占位。
 * 用于两类场景：
 * 1) 接口成功但列表为空（提示原因与下一步操作）；
 * 2) 接口失败或后端未启动（展示 errorMessage 与重试入口）。
 */
interface Props {
  /** 主描述 */
  description?: string
  /** 补充说明（例如「后端未启动时列表为空」的排查提示） */
  subDescription?: string
  /** 操作按钮文案，不传则不渲染按钮 */
  actionText?: string
  /** 视觉尺寸 */
  size?: 'small' | 'default'
}

const props = withDefaults(defineProps<Props>(), {
  description: '暂无数据',
  subDescription: '',
  actionText: '',
  size: 'default',
})

const emit = defineEmits<{
  /** 点击操作按钮 */
  (event: 'action'): void
}>()
</script>

<template>
  <div class="empty-state" :class="{ 'empty-state--small': props.size === 'small' }">
    <el-empty :description="props.description" :image-size="props.size === 'small' ? 80 : 120">
      <template #description>
        <p class="empty-state__title">{{ props.description }}</p>
        <p v-if="props.subDescription" class="empty-state__sub">{{ props.subDescription }}</p>
      </template>
      <slot />
      <el-button v-if="props.actionText" type="primary" plain @click="emit('action')">
        {{ props.actionText }}
      </el-button>
    </el-empty>
  </div>
</template>

<style scoped>
.empty-state {
  padding: var(--sg-gap) 0;
}

.empty-state--small {
  padding: var(--sg-gap-xs) 0;
}

.empty-state__title {
  margin: 0;
  font-size: 14px;
  color: var(--sg-text);
}

.empty-state__sub {
  margin: 6px auto 0;
  max-width: 520px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--sg-text-secondary);
}
</style>
