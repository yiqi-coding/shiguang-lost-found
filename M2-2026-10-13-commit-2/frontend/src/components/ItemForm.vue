<script setup lang="ts">
/**
 * ItemForm —— 失物 / 拾物共用的发布表单。
 * 字段、校验规则、隐私提示都在这里维护，ItemCreateLostView 与 ItemCreateFoundView 通过 postType 复用，
 * 避免两份几乎相同的表单各自演化。本组件只做校验与数据收集，提交（POST /items）由页面负责，
 * 便于页面处理上传进度、AI 分析状态与结果弹窗。
 */
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import ImageUploader from '@/components/ImageUploader.vue'
import PrivacyHint from '@/components/PrivacyHint.vue'
import type { ItemCategory, ItemCreatePayload, PostType } from '@/types/item'
import {
  CATEGORY_OPTIONS,
  CONTACT_ID_MAX_LENGTH,
  DESCRIPTION_MAX_LENGTH,
  DESCRIPTION_MIN_LENGTH,
  HIDDEN_FEATURES_MAX_LENGTH,
  HIDDEN_FEATURES_MIN_LENGTH,
  LOCATION_MAX_LENGTH,
  MAX_IMAGE_COUNT,
  PUBLIC_FEATURES_MAX_LENGTH,
  TITLE_MAX_LENGTH,
  TITLE_MIN_LENGTH,
} from '@/types/item'

interface Props {
  /** 发布类型：lost 失物 / found 拾物 */
  postType: PostType
  /** 提交中（禁用表单与按钮） */
  submitting?: boolean
  /** 提交按钮文案 */
  submitText?: string
}

const props = withDefaults(defineProps<Props>(), {
  submitting: false,
  submitText: '提交发布',
})

const emit = defineEmits<{
  /** 校验通过后抛出完整入参，由页面调用 POST /items */
  (event: 'submit', payload: ItemCreatePayload): void
  (event: 'cancel'): void
}>()

interface FormModel {
  category: ItemCategory | ''
  title: string
  description: string
  color: string
  brand: string
  location: string
  eventTime: Date | null
  publicFeatures: string
  hiddenFeatures: string
  contactId: string
  images: File[]
}

function createEmptyModel(): FormModel {
  return {
    category: '',
    title: '',
    description: '',
    color: '',
    brand: '',
    location: '',
    eventTime: null,
    publicFeatures: '',
    hiddenFeatures: '',
    contactId: '',
    images: [],
  }
}

const form = reactive<FormModel>(createEmptyModel())
const formRef = ref<FormInstance>()

const isLost = computed(() => props.postType === 'lost')
const eventTimeLabel = computed(() => (isLost.value ? '丢失时间' : '拾获时间'))
const locationLabel = computed(() => (isLost.value ? '丢失地点' : '拾获地点'))
const locationPlaceholder = computed(() =>
  isLost.value ? '例如：图书馆 3 楼自习区 A12 座位' : '例如：一食堂二楼靠窗餐台',
)
const hiddenFeatureItems = computed<string[]>(() =>
  isLost.value
    ? [
        '请填写只有真正拾到者才能确认的细节，例如耳机盒右侧的划痕、自定义刻字、序列号后四位。',
        '隐藏特征仅你本人与平台管理员可见，不会出现在列表、详情公开区域或匹配依据中。',
        '系统会基于这些细节生成 2~3 个认领验证问题，答对程度决定可信度与风险等级。',
      ]
    : [
        '请填写只有真正失主才能说出的细节，例如耳机盒内的贴纸、壁纸图案、序列号后四位。',
        '隐藏特征仅你本人与平台管理员可见，不会出现在列表、详情公开区域或匹配依据中。',
        '请勿填写完整手机号、身份证号等敏感信息；描述可核验的物理细节即可。',
      ],
)

/** 时间不能晚于当前时间（失物的丢失时间 / 拾物的拾获时间都是过去时） */
function validateEventTime(
  _rule: unknown,
  value: Date | null,
  callback: (error?: Error) => void,
): void {
  if (!value) {
    callback(new Error(`请选择${eventTimeLabel.value}`))
    return
  }
  if (value.getTime() > Date.now() + 60 * 1000) {
    callback(new Error(`${eventTimeLabel.value}不能晚于当前时间`))
    return
  }
  callback()
}

/** 校验规则：阈值与后端一致（标题 5~80 字、描述 10~2000 字等） */
const rules = computed<FormRules>(() => ({
  category: [{ required: true, message: '请选择物品分类', trigger: 'change' }],
  title: [
    { required: true, message: '请填写标题', trigger: 'blur' },
    {
      min: TITLE_MIN_LENGTH,
      max: TITLE_MAX_LENGTH,
      message: `标题长度需为 ${TITLE_MIN_LENGTH}~${TITLE_MAX_LENGTH} 字`,
      trigger: 'blur',
    },
  ],
  description: [
    { required: true, message: '请填写详细描述', trigger: 'blur' },
    {
      min: DESCRIPTION_MIN_LENGTH,
      max: DESCRIPTION_MAX_LENGTH,
      message: `描述长度需为 ${DESCRIPTION_MIN_LENGTH}~${DESCRIPTION_MAX_LENGTH} 字`,
      trigger: 'blur',
    },
  ],
  location: [
    { required: true, message: `请填写${locationLabel.value}`, trigger: 'blur' },
    { max: LOCATION_MAX_LENGTH, message: `不超过 ${LOCATION_MAX_LENGTH} 字`, trigger: 'blur' },
  ],
  eventTime: [{ required: true, validator: validateEventTime, trigger: 'change' }],
  publicFeatures: [
    { max: PUBLIC_FEATURES_MAX_LENGTH, message: `不超过 ${PUBLIC_FEATURES_MAX_LENGTH} 字`, trigger: 'blur' },
  ],
  hiddenFeatures: [
    { required: true, message: '请填写隐藏特征，认领验证依赖该内容', trigger: 'blur' },
    {
      min: HIDDEN_FEATURES_MIN_LENGTH,
      max: HIDDEN_FEATURES_MAX_LENGTH,
      message: `隐藏特征长度需为 ${HIDDEN_FEATURES_MIN_LENGTH}~${HIDDEN_FEATURES_MAX_LENGTH} 字`,
      trigger: 'blur',
    },
  ],
  contactId: [
    { required: true, message: '请填写联系方式标识', trigger: 'blur' },
    { max: CONTACT_ID_MAX_LENGTH, message: `不超过 ${CONTACT_ID_MAX_LENGTH} 字`, trigger: 'blur' },
  ],
}))

/** 日期选择器禁止选择未来时间 */
function disabledFutureDate(date: Date): boolean {
  return date.getTime() > Date.now()
}

async function handleSubmit(): Promise<void> {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    ElMessage.warning('请先补全带 * 的必填项')
    return
  }

  emit('submit', {
    post_type: props.postType,
    category: form.category as ItemCategory,
    title: form.title.trim(),
    description: form.description.trim(),
    color: form.color.trim() || undefined,
    brand: form.brand.trim() || undefined,
    location: form.location.trim(),
    event_time: (form.eventTime as Date).toISOString(),
    public_features: form.publicFeatures.trim() || undefined,
    hidden_features: form.hiddenFeatures.trim(),
    contact_id: form.contactId.trim(),
    images: [...form.images],
  })
}

/** 重置表单（发布成功后「继续发布」时使用） */
function reset(): void {
  Object.assign(form, createEmptyModel())
  formRef.value?.clearValidate()
}

/** 供页面主动触发校验 */
function validate(): Promise<boolean> | undefined {
  return formRef.value?.validate()
}

defineExpose({ reset, validate })
</script>

<template>
  <el-form
    ref="formRef"
    class="item-form"
    :model="form"
    :rules="rules"
    label-position="top"
    :disabled="props.submitting"
    @submit.prevent
  >
    <el-row :gutter="16">
      <el-col :xs="24" :sm="8">
        <el-form-item label="物品分类" prop="category">
          <el-select v-model="form.category" placeholder="请选择分类" class="item-form__full">
            <el-option
              v-for="option in CATEGORY_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
      </el-col>

      <el-col :xs="12" :sm="8">
        <el-form-item label="颜色（选填）" prop="color">
          <el-input v-model="form.color" maxlength="20" placeholder="例如：白色 / 深空灰" clearable />
        </el-form-item>
      </el-col>

      <el-col :xs="12" :sm="8">
        <el-form-item label="品牌 / 型号（选填）" prop="brand">
          <el-input v-model="form.brand" maxlength="40" placeholder="例如：Apple AirPods Pro" clearable />
        </el-form-item>
      </el-col>
    </el-row>

    <el-form-item :label="`标题（${TITLE_MIN_LENGTH}~${TITLE_MAX_LENGTH} 字）`" prop="title">
      <el-input
        v-model="form.title"
        :maxlength="TITLE_MAX_LENGTH"
        show-word-limit
        placeholder="例如：白色 AirPods Pro 充电盒（带磕碰）"
        clearable
      />
    </el-form-item>

    <el-form-item :label="`详细描述（${DESCRIPTION_MIN_LENGTH}~${DESCRIPTION_MAX_LENGTH} 字）`" prop="description">
      <el-input
        v-model="form.description"
        type="textarea"
        :rows="4"
        :maxlength="DESCRIPTION_MAX_LENGTH"
        show-word-limit
        :placeholder="
          isLost
            ? '请描述物品外观、内部物品、丢失经过等，便于算法与管理员核对。'
            : '请写明拾获经过与保管方式（如已交至几号楼值班室），便于失主认领。'
        "
      />
    </el-form-item>

    <el-row :gutter="16">
      <el-col :xs="24" :sm="12">
        <el-form-item :label="locationLabel" prop="location">
          <el-input v-model="form.location" :maxlength="LOCATION_MAX_LENGTH" :placeholder="locationPlaceholder" clearable />
        </el-form-item>
      </el-col>

      <el-col :xs="24" :sm="12">
        <el-form-item :label="eventTimeLabel" prop="eventTime">
          <el-date-picker
            v-model="form.eventTime"
            class="item-form__full"
            type="datetime"
            :placeholder="`请选择${eventTimeLabel}`"
            :disabled-date="disabledFutureDate"
          />
        </el-form-item>
      </el-col>
    </el-row>

    <el-form-item label="图片（最多 3 张，用于图像相似度匹配）" prop="images">
      <ImageUploader v-model="form.images" :max-count="MAX_IMAGE_COUNT" :disabled="props.submitting" />
    </el-form-item>

    <el-form-item label="公开特征（选填，所有人可见）" prop="publicFeatures">
      <el-input
        v-model="form.publicFeatures"
        type="textarea"
        :rows="2"
        :maxlength="PUBLIC_FEATURES_MAX_LENGTH"
        show-word-limit
        placeholder="可公开展示的外观特征，例如：透明保护壳、贴有校徽贴纸"
      />
    </el-form-item>

    <el-form-item label="隐藏特征（必填，仅你与管理员可见）" prop="hiddenFeatures">
      <PrivacyHint title="隐藏特征隐私说明" level="warning" compact :items="hiddenFeatureItems" />
      <el-input
        v-model="form.hiddenFeatures"
        type="textarea"
        :rows="3"
        :maxlength="HIDDEN_FEATURES_MAX_LENGTH"
        show-word-limit
        placeholder="例如：充电盒右侧有一道 1cm 划痕，盒内刻有 YQ，序列号后四位 8823"
      />
    </el-form-item>

    <el-form-item label="联系方式标识（必填，后端脱敏存储）" prop="contactId">
      <el-input
        v-model="form.contactId"
        :maxlength="CONTACT_ID_MAX_LENGTH"
        placeholder="学号 / 手机号 / 微信号，用于认领核验与线下交接"
        clearable
      />
      <p class="sg-muted item-form__hint">
        该字段仅用于认领流程与线下交接，审核台展示时由后端脱敏（例如 138****5678）。
      </p>
    </el-form-item>

    <el-alert class="item-form__alert" type="info" :closable="false" show-icon title="关于 AI 分析">
      <p class="item-form__alert-text">
        提交后后端会触发 AI 分析，自动补充颜色、品牌等属性并计入匹配的「属性一致性」维度，分析期间状态为
        AI 分析中。
      </p>
      <p class="item-form__alert-text">
        若 AI 服务异常（ai_status=failed），你填写的描述与属性仍会完整保存，发布不会失败；可继续发布并后续手动补充属性。
      </p>
    </el-alert>

    <div class="item-form__actions">
      <el-button @click="emit('cancel')">取消</el-button>
      <el-button type="primary" :loading="props.submitting" @click="handleSubmit">
        {{ props.submitText }}
      </el-button>
    </div>
  </el-form>
</template>

<style scoped>
.item-form {
  width: 100%;
}

.item-form__full {
  width: 100%;
}

.item-form__hint {
  margin: 4px 0 0;
}

.item-form__alert {
  margin-bottom: var(--sg-gap);
  align-items: flex-start;
}

.item-form__alert-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.item-form__alert-text + .item-form__alert-text {
  margin-top: 4px;
}

.item-form__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sg-gap-xs);
  padding-top: var(--sg-gap-xs);
}

@media (max-width: 767px) {
  .item-form__actions {
    flex-direction: column-reverse;
  }

  .item-form__actions .el-button {
    width: 100%;
    margin-left: 0;
  }
}
</style>
