<script setup lang="ts">
/**
 * ImageUploader —— 图片选择与预览（只维护本地 File 列表，不直接发起上传）。
 * 图片随后端发布接口一起以 multipart/form-data 提交，字段名固定为 image。
 * 校验规则与后端一致：jpg / jpeg / png / webp，单张 ≤ 10MB，最多 3 张，超出即提示并拒绝。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ACCEPTED_IMAGE_EXT,
  ACCEPTED_IMAGE_MIME_TEXT,
  ACCEPTED_IMAGE_TYPES,
  MAX_IMAGE_COUNT,
  MAX_IMAGE_SIZE_MB,
} from '@/types/item'

interface Props {
  /** 已选择的文件列表（v-model） */
  modelValue: File[]
  /** 最多张数 */
  maxCount?: number
  /** 单张大小上限（MB） */
  maxSizeMb?: number
  /** 提交中禁用交互 */
  disabled?: boolean
  /** 自定义提示文案 */
  tip?: string
}

const props = withDefaults(defineProps<Props>(), {
  maxCount: MAX_IMAGE_COUNT,
  maxSizeMb: MAX_IMAGE_SIZE_MB,
  disabled: false,
  tip: '',
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: File[]): void
  /** 校验失败时抛出，页面可据此做额外提示 */
  (event: 'invalid', payload: { file: File; reason: string }): void
}>()

const inputRef = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const previewUrl = ref('')
const previewVisible = ref(false)

/** object URL 缓存：同一个 File 只创建一次，避免重复渲染造成内存泄漏 */
const urlCache = new Map<File, string>()

function urlOf(file: File): string {
  const cached = urlCache.get(file)
  if (cached) return cached
  const url = URL.createObjectURL(file)
  urlCache.set(file, url)
  return url
}

const previews = computed(() => props.modelValue.map((file) => ({ file, url: urlOf(file) })))

const tipText = computed(
  () =>
    props.tip ||
    `支持 ${ACCEPTED_IMAGE_MIME_TEXT}；单张不超过 ${props.maxSizeMb}MB；最多 ${props.maxCount} 张`,
)

watch(
  () => props.modelValue,
  (files) => {
    // 已移除的文件立即释放 object URL
    urlCache.forEach((url, file) => {
      if (!files.includes(file)) {
        URL.revokeObjectURL(url)
        urlCache.delete(file)
      }
    })
  },
)

onBeforeUnmount(() => {
  urlCache.forEach((url) => URL.revokeObjectURL(url))
  urlCache.clear()
})

function notify(reason: string, file: File): void {
  ElMessage.warning(`${file.name}：${reason}`)
  emit('invalid', { file, reason })
}

/** 单张图片校验，返回错误原因（通过则返回 null） */
function validate(file: File): string | null {
  if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
    return `格式不支持，仅允许 ${ACCEPTED_IMAGE_MIME_TEXT}`
  }
  if (file.size > props.maxSizeMb * 1024 * 1024) {
    return `文件过大，单张不能超过 ${props.maxSizeMb}MB`
  }
  if (file.size === 0) {
    return '文件内容为空，请重新选择'
  }
  return null
}

function addFiles(fileList: FileList | null): void {
  if (props.disabled || !fileList || fileList.length === 0) return

  const next: File[] = [...props.modelValue]
  Array.from(fileList).forEach((file) => {
    if (next.length >= props.maxCount) {
      notify(`最多上传 ${props.maxCount} 张图片`, file)
      return
    }
    const reason = validate(file)
    if (reason) {
      notify(reason, file)
      return
    }
    const duplicated = next.some((item) => item.name === file.name && item.size === file.size)
    if (duplicated) {
      notify('已存在同名同大小的图片', file)
      return
    }
    next.push(file)
  })

  if (next.length !== props.modelValue.length) emit('update:modelValue', next)
}

function openPicker(): void {
  if (props.disabled) return
  inputRef.value?.click()
}

function onChange(event: Event): void {
  const input = event.target as HTMLInputElement
  addFiles(input.files)
  // 清空 input，保证同一文件删除后还能再次选择
  input.value = ''
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  if (props.disabled) return
  addFiles(event.dataTransfer?.files ?? null)
}

function removeAt(index: number): void {
  emit(
    'update:modelValue',
    props.modelValue.filter((_, current) => current !== index),
  )
}

function openPreview(url: string): void {
  previewUrl.value = url
  previewVisible.value = true
}
</script>

<template>
  <div
    class="uploader"
    :class="{ 'is-disabled': props.disabled, 'is-dragging': dragging }"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <div class="uploader__grid">
      <div v-for="(item, index) in previews" :key="`${item.file.name}-${item.file.size}-${index}`" class="uploader__item">
        <img class="uploader__thumb" :src="item.url" :alt="item.file.name" />
        <div class="uploader__mask">
          <el-button link type="primary" :disabled="props.disabled" @click="openPreview(item.url)">
            预览
          </el-button>
          <el-button link type="danger" :disabled="props.disabled" @click="removeAt(index)">
            删除
          </el-button>
        </div>
      </div>

      <button
        v-if="previews.length < props.maxCount"
        type="button"
        class="uploader__add"
        :disabled="props.disabled"
        @click="openPicker"
      >
        <el-icon :size="22"><Plus /></el-icon>
        <span>选择图片</span>
        <span class="uploader__counter">{{ previews.length }} / {{ props.maxCount }}</span>
      </button>
    </div>

    <input
      ref="inputRef"
      class="uploader__input"
      type="file"
      :accept="ACCEPTED_IMAGE_EXT"
      multiple
      @change="onChange"
    />

    <p class="uploader__tip">{{ tipText }}</p>

    <el-dialog v-model="previewVisible" title="图片预览" width="640px">
      <img class="uploader__preview" :src="previewUrl" alt="图片预览" />
    </el-dialog>
  </div>
</template>

<style scoped>
.uploader {
  border: 1px dashed var(--sg-border);
  border-radius: var(--sg-radius);
  padding: var(--sg-gap-sm);
  background: var(--sg-surface);
  transition: border-color 0.2s ease;
}

.uploader.is-dragging {
  border-color: var(--sg-primary);
  background: var(--sg-primary-light);
}

.uploader.is-disabled {
  opacity: 0.7;
}

.uploader__grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-sm);
}

.uploader__item {
  position: relative;
  width: 112px;
  height: 112px;
  border-radius: var(--sg-radius-sm);
  overflow: hidden;
  border: 1px solid var(--sg-border-light);
}

.uploader__thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.uploader__mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: rgba(0, 0, 0, 0.45);
  opacity: 0;
  transition: opacity 0.2s ease;
}

.uploader__item:hover .uploader__mask {
  opacity: 1;
}

.uploader__add {
  width: 112px;
  height: 112px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
  color: var(--sg-text-secondary);
  background: var(--sg-bg);
  border: 1px dashed var(--sg-border);
  border-radius: var(--sg-radius-sm);
  font-size: 13px;
}

.uploader__add:disabled {
  cursor: not-allowed;
  color: var(--sg-text-placeholder);
}

.uploader__counter {
  font-size: 12px;
  color: var(--sg-text-placeholder);
}

.uploader__input {
  display: none;
}

.uploader__tip {
  margin: var(--sg-gap-xs) 0 0;
  font-size: 12px;
  color: var(--sg-text-secondary);
  line-height: 1.6;
}

.uploader__preview {
  width: 100%;
  height: auto;
  display: block;
  border-radius: var(--sg-radius-sm);
}

@media (max-width: 767px) {
  .uploader__item,
  .uploader__add {
    width: 92px;
    height: 92px;
  }
}
</style>
