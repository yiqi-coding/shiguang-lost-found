<script setup lang="ts">
/**
 * 发布拾物 ItemCreateFoundView —— 路由 /items/new/found
 * 职责：以「拾物」语义填写并提交物品信息，处理图片上传进度、AI 分析状态与发布结果反馈；
 *       额外强调拾获经过与保管方式的说明义务，避免出现冒领纠纷。
 * 依赖接口：POST /items（multipart/form-data，结构与发布失物一致，post_type=found）
 * 表单字段与校验规则由 ItemForm 承载（与发布失物共用）。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createItem } from '@/api/items'
import ItemForm from '@/components/ItemForm.vue'
import PrivacyHint from '@/components/PrivacyHint.vue'
import StatusTag from '@/components/StatusTag.vue'
import type { ItemCreatePayload, ItemCreateResult } from '@/types/item'

const router = useRouter()

const submitting = ref(false)
const uploadPercent = ref(0)
const result = ref<ItemCreateResult | null>(null)
const resultVisible = ref(false)

/** ItemForm 暴露的方法（显式结构类型） */
const formRef = ref<{ reset: () => void; validate: () => Promise<boolean> | undefined } | null>(null)

async function handleSubmit(payload: ItemCreatePayload): Promise<void> {
  submitting.value = true
  uploadPercent.value = 0
  try {
    const created = await createItem(payload, (percent) => {
      uploadPercent.value = percent
    })
    result.value = created
    resultVisible.value = true

    if (created.ai_status === 'failed') {
      ElMessage.warning('物品已发布成功；AI 分析失败，已保留你填写的描述与属性。')
    } else if (created.ai_status === 'pending') {
      ElMessage.info('发布成功，AI 正在分析图片与描述，稍后可在详情页查看结果。')
    } else {
      ElMessage.success('发布成功，失主发起认领时会回答你填写的隐藏特征问题。')
    }
  } catch {
    // 失败提示由 axios 响应拦截器统一弹出
  } finally {
    submitting.value = false
    uploadPercent.value = 0
  }
}

function goDetail(): void {
  if (!result.value) return
  void router.push({ name: 'item-detail', params: { id: result.value.item_id } })
}

function goMatches(): void {
  if (!result.value) return
  void router.push({ name: 'item-matches', params: { id: result.value.item_id } })
}

function continueCreate(): void {
  resultVisible.value = false
  result.value = null
  formRef.value?.reset()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function goMyRecords(): void {
  void router.push({ name: 'my-records' })
}
</script>

<template>
  <div class="create sg-page">
    <div class="sg-page__header">
      <div>
        <h1 class="sg-page__title">发布拾物</h1>
        <p class="sg-page__subtitle">
          感谢你的拾金不昧。请写清拾获经过与保管方式，并填写只有真正失主才知道的隐藏特征。
        </p>
      </div>
    </div>

    <el-alert class="create__notice" type="success" :closable="false" show-icon title="发布须知">
      <p class="create__notice-text">
        请在描述中写明拾获经过与保管方式（例如「已交至 3 号宿舍楼值班室」），便于失主线下认领。
      </p>
      <p class="create__notice-text">
        认领必须经过「隐藏特征验证 + 管理员审核」，请勿直接向陌生人交付物品。
      </p>
    </el-alert>

    <PrivacyHint
      class="create__privacy"
      title="隐藏特征怎么填"
      level="warning"
      :items="[
        '填写只有真正失主才能说出的细节：盒内贴纸、刻字、壁纸、序列号后四位、损坏痕迹等。',
        '系统据此生成 2~3 个验证问题；答案不会展示给申请人，只用于计算可信度与风险等级。',
        '隐藏特征仅你与管理员可见，但请仍然避免填写身份证号等敏感信息。',
      ]"
    />

    <el-card class="create__card" shadow="never">
      <ItemForm
        ref="formRef"
        post-type="found"
        submit-text="提交拾物信息"
        :submitting="submitting"
        @submit="handleSubmit"
        @cancel="goMyRecords"
      />

      <el-progress
        v-if="submitting"
        class="create__progress"
        :percentage="uploadPercent"
        :stroke-width="10"
        striped
        striped-flow
      />
      <p v-if="submitting" class="sg-muted create__progress-text">
        正在上传图片并提交（{{ uploadPercent }}%）…提交后后端会触发 AI 分析。
      </p>
    </el-card>

    <el-dialog v-model="resultVisible" title="发布结果" width="520px" :close-on-click-modal="false">
      <template v-if="result">
        <el-result
          :icon="result.ai_status === 'failed' ? 'warning' : 'success'"
          :title="result.ai_status === 'failed' ? '发布成功（AI 分析失败）' : '发布成功'"
          :sub-title="`物品 ID：${result.item_id}`"
        >
          <template #extra>
            <div class="create__result">
              <div class="create__result-row">
                <span class="sg-muted">当前状态</span>
                <StatusTag :status="result.status" kind="item" />
              </div>
              <div class="create__result-row">
                <span class="sg-muted">AI 分析状态</span>
                <StatusTag :status="result.ai_status" kind="ai" />
              </div>
              <div class="create__result-row">
                <span class="sg-muted">图片 Key</span>
                <span>{{ result.image_keys.length }} 个</span>
              </div>
            </div>

            <el-alert
              v-if="result.ai_status === 'failed'"
              class="create__result-alert"
              type="warning"
              :closable="false"
              show-icon
              title="AI 分析失败不影响本次发布"
              description="你填写的描述与属性已完整保存，拾物可继续参与匹配；可稍后在匹配结果页重新计算匹配分数。"
            />
            <el-alert
              v-else-if="result.ai_status === 'pending'"
              class="create__result-alert"
              type="info"
              :closable="false"
              show-icon
              title="AI 分析进行中"
              description="稍后刷新详情页即可看到补充的属性标签；期间不影响匹配与认领流程。"
            />
          </template>
        </el-result>
      </template>

      <template #footer>
        <el-button @click="continueCreate">继续发布</el-button>
        <el-button @click="goMyRecords">我的记录</el-button>
        <el-button type="primary" @click="goMatches">查看匹配结果</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.create__notice {
  margin-bottom: var(--sg-gap-sm);
  align-items: flex-start;
}

.create__notice-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.create__notice-text + .create__notice-text {
  margin-top: 4px;
}

.create__privacy {
  margin-bottom: var(--sg-gap-sm);
}

.create__card {
  margin-bottom: var(--sg-gap);
}

.create__progress {
  margin-top: var(--sg-gap-sm);
}

.create__progress-text {
  margin: 6px 0 0;
}

.create__result {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: left;
}

.create__result-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
  font-size: 14px;
}

.create__result-alert {
  margin-top: var(--sg-gap-sm);
  text-align: left;
  align-items: flex-start;
}
</style>
