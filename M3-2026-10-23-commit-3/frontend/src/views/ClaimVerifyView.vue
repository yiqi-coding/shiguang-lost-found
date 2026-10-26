<script setup lang="ts">
/**
 * 认领验证 ClaimVerifyView —— 路由 /claims/:id/verify
 * 职责：展示 2~3 个基于隐藏特征的验证问题、收集作答、提交后展示可信度、风险等级与认领状态。
 * 依赖接口：GET /claims/{claim_id}（刷新恢复问题，契约未列出、待后端补充）、
 *           POST /claims/{claim_id}/verify（提交作答）。
 *
 * 合规要求：页面只展示问题文本与申请人自己的作答，绝不展示标准答案、关键词或比对逻辑；
 *           验证结果仅作为管理员审核的辅助依据，不构成归属认定。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { fetchClaimDetail, verifyClaim } from '@/api/claims'
import { ApiError } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import PrivacyHint from '@/components/PrivacyHint.vue'
import StatusTag from '@/components/StatusTag.vue'
import { clearPendingClaim, readPendingClaim } from '@/stores/session'
import type { ClaimQuestion, VerifyClaimPayload, VerifyClaimResult } from '@/types/claim'
import { RISK_LEVEL_ADVICE } from '@/types/claim'
import type { RiskLevel } from '@/types/claim'
import { toScorePercent } from '@/types/match'

const route = useRoute()
const router = useRouter()

const claimId = computed(() => Number(route.params.id))

const loading = ref(false)
const loadError = ref('')
const questions = ref<ClaimQuestion[]>([])
const itemId = ref<number | null>(null)
/** 问题来源：server 表示接口返回，cache 表示创建认领时的暂存数据（接口未实现时的降级） */
const questionSource = ref<'server' | 'cache' | ''>('')

/** 作答内容：key 为 question_id */
const answers = reactive<Record<string, string>>({})
const formRef = ref<FormInstance>()

const submitting = ref(false)
const result = ref<VerifyClaimResult | null>(null)

const scorePercent = computed(() => (result.value ? toScorePercent(result.value.verification_score) : 0))

/** 综合可信度（后端 credibility_score，0~1） */
const credibilityPercent = computed(() =>
  result.value?.credibility_score === null || result.value?.credibility_score === undefined
    ? null
    : toScorePercent(result.value.credibility_score),
)

const riskLevel = computed<RiskLevel | null>(() => result.value?.risk_level ?? null)

/** 逐题结果：只含「是否命中 + 得分 + 理由」，后端保证理由不回显标准答案 */
const perQuestion = computed(() => result.value?.per_question ?? [])

/** 每个问题独立校验：必填 + 2~200 字 */
const rules = computed<FormRules>(() => {
  const ruleMap: FormRules = {}
  questions.value.forEach((question) => {
    ruleMap[question.question_id] = [
      { required: true, message: '请回答该问题', trigger: 'blur' },
      { min: 2, max: 200, message: '答案长度需为 2~200 字', trigger: 'blur' },
    ]
  })
  return ruleMap
})

async function loadClaim(): Promise<void> {
  if (!Number.isFinite(claimId.value) || claimId.value <= 0) {
    loadError.value = '认领 ID 不合法，请从匹配结果页重新发起认领。'
    return
  }

  loading.value = true
  loadError.value = ''
  try {
    const detail = await fetchClaimDetail(claimId.value)
    questions.value = detail.questions ?? []
    itemId.value = detail.item_id ?? null
    questionSource.value = 'server'

    // 已提交过验证的认领：直接展示历史结果，不再重复作答
    if (detail.verification_score !== null && detail.verification_score !== undefined) {
      result.value = {
        verification_score: detail.verification_score,
        risk_level: detail.risk_level ?? 'medium',
        status: detail.status,
      }
    }

    if (questions.value.length === 0 && !result.value) {
      loadError.value = '后端未返回验证问题，请从匹配结果页重新发起认领。'
    }
  } catch (error) {
    // 降级：使用进入本页前暂存的问题（后端 GET /claims/{id} 尚未实现时）
    const cached = readPendingClaim(claimId.value)
    if (cached && cached.questions.length > 0) {
      questions.value = cached.questions
      questionSource.value = 'cache'
    } else if (error instanceof ApiError && error.code === 'CLAIM_NOT_FOUND') {
      loadError.value = '认领记录不存在或已失效，请从匹配结果页重新发起认领。'
    } else {
      loadError.value = error instanceof Error ? error.message : '认领信息加载失败'
    }
  } finally {
    questions.value.forEach((question) => {
      if (answers[question.question_id] === undefined) answers[question.question_id] = ''
    })
    loading.value = false
  }
}

/** 提交作答：返回 verification_score / risk_level / status */
async function handleSubmit(): Promise<void> {
  if (!formRef.value) return
  if (questions.value.length === 0) {
    ElMessage.warning('当前没有可作答的验证问题')
    return
  }

  try {
    await formRef.value.validate()
  } catch {
    ElMessage.warning('请先完成全部问题的作答')
    return
  }

  submitting.value = true
  try {
    const payload: VerifyClaimPayload = {
      answers: questions.value.map((question) => ({
        question_id: question.question_id,
        answer: (answers[question.question_id] ?? '').trim(),
      })),
    }
    result.value = await verifyClaim(claimId.value, payload)
    clearPendingClaim()
    ElMessage.success('验证已提交，结果已生成')
  } catch {
    // 错误提示由 axios 拦截器统一处理
  } finally {
    submitting.value = false
  }
}

function goMatches(): void {
  const target = itemId.value ?? null
  if (target) {
    void router.push({ name: 'item-matches', params: { id: target } })
    return
  }
  void router.push({ name: 'item-list' })
}

function goMyRecords(): void {
  void router.push({ name: 'my-records' })
}

onMounted(() => {
  void loadClaim()
})
</script>

<template>
  <div class="verify sg-page">
    <div class="sg-page__header">
      <div>
        <h1 class="sg-page__title">认领验证</h1>
        <p class="sg-page__subtitle">
          认领编号 {{ claimId }}｜请如实回答以下问题，系统会据此计算可信度与风险等级，供管理员审核参考。
        </p>
      </div>
      <div class="verify__actions">
        <el-button @click="goMatches">返回匹配结果</el-button>
        <el-button @click="goMyRecords">我的认领记录</el-button>
      </div>
    </div>

    <PrivacyHint
      class="verify__privacy"
      title="作答说明"
      level="warning"
      :items="[
        '问题由发布者填写的隐藏特征生成，答案不会展示给你，也不会返回给发布者。',
        '请只填写你真实知道的细节：模糊或编造的答案会拉低可信度并提高风险等级。',
        '验证结果只是管理员审核的辅助依据，不构成物品归属的自动认定。',
      ]"
    />

    <el-skeleton v-if="loading" :rows="6" animated />

    <EmptyState
      v-else-if="loadError"
      description="无法进入认领验证"
      :sub-description="loadError"
      action-text="查看匹配结果"
      @action="goMatches"
    />

    <template v-else>
      <!-- 结果面板：可信度 + 风险等级 + 状态 -->
      <el-card v-if="result" class="verify__card" shadow="never">
        <h2 class="sg-section-title">验证结果</h2>
        <el-row :gutter="16">
          <el-col :xs="24" :sm="8">
            <div class="verify__score">
              <el-progress
                type="dashboard"
                :percentage="scorePercent"
                :width="128"
                :stroke-width="9"
                :color="scorePercent >= 75 ? '#2ba471' : scorePercent >= 45 ? '#e37318' : '#d54941'"
              />
              <p class="verify__score-label">隐藏特征可信度 {{ scorePercent }} / 100</p>
            </div>
          </el-col>

          <el-col :xs="24" :sm="16">
            <div class="verify__result-rows">
              <div class="verify__result-row">
                <span class="sg-muted">风险等级</span>
                <StatusTag v-if="riskLevel" :status="riskLevel" kind="risk" />
                <span v-else>—</span>
              </div>
              <div class="verify__result-row">
                <span class="sg-muted">综合可信度</span>
                <span v-if="credibilityPercent !== null">
                  {{ credibilityPercent }} / 100
                  <span class="sg-muted">（含候选匹配分、信息完整度与行为风险）</span>
                </span>
                <span v-else>—</span>
              </div>
              <div class="verify__result-row">
                <span class="sg-muted">认领状态</span>
                <StatusTag :status="result.status" kind="claim" />
              </div>
              <div class="verify__result-row">
                <span class="sg-muted">剩余作答次数</span>
                <span>{{ result.attempts_left ?? '—' }}</span>
              </div>
              <div class="verify__result-row">
                <span class="sg-muted">下一步</span>
                <span class="verify__result-hint">
                  {{ result.next_step || (riskLevel ? RISK_LEVEL_ADVICE[riskLevel] : '请等待管理员审核。') }}
                </span>
              </div>
            </div>

            <el-alert
              class="verify__result-alert"
              type="info"
              :closable="false"
              show-icon
              title="审核流程"
              description="管理员会综合匹配依据、验证结果、风险等级与实际交接情况作出结论；若结论为「待补充信息」，请按管理员备注补充材料。"
            />
          </el-col>
        </el-row>

        <!-- 逐题结果：只有是否命中 / 得分 / 理由，不含标准答案 -->
        <template v-if="perQuestion.length > 0">
          <h3 class="verify__sub-title">逐题结果</h3>
          <el-table :data="perQuestion" size="small" stripe>
            <el-table-column label="问题" min-width="220">
              <template #default="{ row }">
                {{ row.question_text || `问题 ${row.question_id}` }}
              </template>
            </el-table-column>
            <el-table-column label="是否命中" width="100">
              <template #default="{ row }">
                <el-tag :type="row.matched ? 'success' : 'danger'" size="small" effect="plain">
                  {{ row.matched ? '命中' : '未命中' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="说明" min-width="200" />
          </el-table>
        </template>

        <el-alert
          v-for="(tip, index) in result.risk_tips ?? []"
          :key="`tip-${index}`"
          class="verify__risk-tip"
          type="warning"
          :closable="false"
          show-icon
          :title="tip"
        />

        <el-alert
          class="verify__no-answer"
          type="warning"
          :closable="false"
          show-icon
          title="系统不会展示标准答案"
          description="为保证公平，问题答案、关键词与比对逻辑均不对外展示，也不会回显给发布者。"
        />
      </el-card>

      <!-- 作答表单 -->
      <el-card v-else class="verify__card" shadow="never">
        <h2 class="sg-section-title">
          验证问题（{{ questions.length }} 题）
          <el-tag v-if="questionSource === 'cache'" class="verify__source-tag" size="small" type="info" effect="plain">
            来自创建认领时的暂存数据
          </el-tag>
        </h2>

        <el-alert
          class="verify__hint"
          type="info"
          :closable="false"
          show-icon
          title="请按实际情况作答"
          description="每题 2~200 字；描述越具体（位置、痕迹、编号等）越有助于管理员判断。提交后不可重复提交，如需修改请联系管理员。"
        />

        <el-form ref="formRef" :model="answers" :rules="rules" label-position="top" @submit.prevent>
          <el-form-item
            v-for="(question, index) in questions"
            :key="question.question_id"
            :label="`第 ${index + 1} 题：${question.question_text}`"
            :prop="question.question_id"
          >
            <el-input
              v-model="answers[question.question_id]"
              type="textarea"
              :rows="3"
              maxlength="200"
              show-word-limit
              placeholder="请填写你所知道的真实细节，例如位置、颜色、痕迹、编号等"
            />
          </el-form-item>
        </el-form>

        <div class="verify__submit">
          <el-button @click="goMatches">放弃本次认领</el-button>
          <el-button type="primary" :loading="submitting" @click="handleSubmit">
            提交验证
          </el-button>
        </div>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.verify__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
}

.verify__privacy {
  margin-bottom: var(--sg-gap-sm);
}

.verify__card {
  margin-bottom: var(--sg-gap);
}

.verify__score {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding-bottom: var(--sg-gap-xs);
}

.verify__score-label {
  margin: 0;
  font-size: 13px;
  color: var(--sg-text-secondary);
}

.verify__result-rows {
  display: flex;
  flex-direction: column;
  gap: var(--sg-gap-xs);
}

.verify__result-row {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-xs);
  font-size: 14px;
}

.verify__result-hint {
  line-height: 1.7;
}

.verify__result-alert {
  margin-top: var(--sg-gap-sm);
  align-items: flex-start;
}

.verify__no-answer {
  margin-top: var(--sg-gap);
  align-items: flex-start;
}

.verify__sub-title {
  margin: var(--sg-gap) 0 var(--sg-gap-xs);
  font-size: 14px;
  font-weight: 600;
}

.verify__risk-tip {
  margin-top: var(--sg-gap-xs);
  align-items: flex-start;
}

.verify__source-tag {
  margin-left: 6px;
}

.verify__hint {
  margin-bottom: var(--sg-gap);
  align-items: flex-start;
}

.verify__submit {
  display: flex;
  justify-content: flex-end;
  gap: var(--sg-gap-xs);
  margin-top: var(--sg-gap-sm);
}

@media (max-width: 767px) {
  .verify__actions {
    width: 100%;
  }

  .verify__actions .el-button {
    flex: 1 1 auto;
    margin-left: 0;
  }

  .verify__submit {
    flex-direction: column-reverse;
  }

  .verify__submit .el-button {
    width: 100%;
    margin-left: 0;
  }
}
</style>
