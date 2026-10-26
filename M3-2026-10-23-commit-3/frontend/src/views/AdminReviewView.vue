<script setup lang="ts">
/**
 * 管理员审核台 AdminReviewView —— 路由 /admin/review
 * 职责：左侧列出待审核认领（该列表接口未列入契约，缺失时降级为按认领 ID 查询），
 *       右侧展示审核所需的全部依据：双方物品与隐藏特征、匹配维度分与依据/不确定因素、
 *       逐题验证结果（只含是否命中/得分/理由，绝不含标准答案）、可信度与风险等级、
 *       双方脱敏联系方式、历史操作日志；底部提供 approve / reject / request_more_info 操作与备注。
 * 依赖接口：GET /admin/claims（预留）、GET /admin/claims/{claim_id}、POST /admin/claims/{claim_id}/review。
 * 权限：路由守卫做体验层拦截（requiresAdmin），真正的鉴权由后端完成。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getAdminClaimDetail, listAdminClaims, reviewClaim } from '@/api/admin'
import { ApiError } from '@/api/http'
import { toDisplayText } from '@/api/items'
import EmptyState from '@/components/EmptyState.vue'
import MatchScoreCard from '@/components/MatchScoreCard.vue'
import PrivacyHint from '@/components/PrivacyHint.vue'
import StatusTag from '@/components/StatusTag.vue'
import type {
  AdminClaimDetail,
  AdminClaimListItem,
  AdminClaimParty,
  AdminClaimQuery,
  ClaimStatus,
  ReviewAction,
} from '@/types/claim'
import { CLAIM_STATUS_LABELS, REVIEW_ACTION_LABELS } from '@/types/claim'
import { toScorePercent } from '@/types/match'
import { formatDateTime, safeText, stringifyDetail } from '@/utils/format'

const route = useRoute()
const router = useRouter()

/** ---------- 待审核列表 ---------- */
const listQuery = reactive<AdminClaimQuery>({ status: 'pending_review', page: 1, page_size: 10 })
const claims = ref<AdminClaimListItem[]>([])
const total = ref(0)
const listLoading = ref(false)
const listError = ref('')
/** 列表接口不存在（契约未提供）时置为 true，页面切换为「按认领 ID 查询」模式 */
const listUnsupported = ref(false)

const STATUS_OPTIONS: ReadonlyArray<{ value: ClaimStatus | ''; label: string }> = [
  { value: '', label: '全部状态' },
  ...(Object.keys(CLAIM_STATUS_LABELS) as ClaimStatus[]).map((value) => ({
    value,
    label: CLAIM_STATUS_LABELS[value],
  })),
]

/** 当前选中的认领（列表与详情的联动依据） */
const selectedClaimId = ref<number | null>(null)
/** 手工输入的认领 ID（列表接口不可用时的入口） */
const manualClaimId = ref<number | null>(null)

/** ---------- 审核详情 ---------- */
const detail = ref<AdminClaimDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

/** 备注：驳回与要求补充信息时必填（契约 §3.11：10~500 字） */
const comment = ref('')
const actionLoading = ref<ReviewAction | null>(null)

async function loadClaims(): Promise<void> {
  listLoading.value = true
  listError.value = ''
  try {
    const result = await listAdminClaims({ ...listQuery })
    claims.value = result.items
    total.value = result.total
    listQuery.page = result.page
    listQuery.page_size = result.page_size
    listUnsupported.value = false

    // 未选中任何记录时，默认选中第一条，减少一次点击
    if (!selectedClaimId.value && result.items.length > 0) {
      selectClaim(result.items[0].claim_id)
    }
  } catch (error) {
    claims.value = []
    total.value = 0
    if (error instanceof ApiError && (error.httpStatus === 404 || error.code === 'UNKNOWN_ERROR')) {
      listUnsupported.value = true
      listError.value = ''
    } else {
      listError.value = error instanceof Error ? error.message : '待审核列表加载失败'
    }
  } finally {
    listLoading.value = false
  }
}

function handleListPage(page: number): void {
  listQuery.page = page
  void loadClaims()
}

function handleStatusChange(): void {
  listQuery.page = 1
  void loadClaims()
}

/** 手工查询：列表接口不可用时使用 */
function handleManualQuery(): void {
  if (!manualClaimId.value || manualClaimId.value <= 0) {
    ElMessage.warning('请输入有效的认领 ID')
    return
  }
  selectClaim(Math.floor(manualClaimId.value))
}

async function loadDetail(): Promise<void> {
  if (!selectedClaimId.value) {
    detail.value = null
    return
  }

  detailLoading.value = true
  detailError.value = ''
  try {
    detail.value = await getAdminClaimDetail(selectedClaimId.value)
  } catch (error) {
    detail.value = null
    detailError.value = error instanceof Error ? error.message : '审核详情加载失败'
  } finally {
    detailLoading.value = false
  }
}

function selectClaim(claimId: number): void {
  selectedClaimId.value = claimId
  comment.value = ''
  void loadDetail()
  // 把选中项写入 URL，便于刷新与分享（不新增历史记录）
  void router.replace({ query: { ...route.query, claim_id: String(claimId) } })
}

const selectedSummary = computed(
  () => claims.value.find((item) => item.claim_id === selectedClaimId.value) ?? null,
)

/** 隐藏特征可信度（0~1 → 0~100） */
const verificationScore = computed(() =>
  detail.value?.claim.verification_score === null || detail.value?.claim.verification_score === undefined
    ? null
    : toScorePercent(detail.value.claim.verification_score),
)

/** 综合可信度（0~1 → 0~100） */
const credibilityScore = computed(() =>
  detail.value?.claim.credibility_score === null || detail.value?.claim.credibility_score === undefined
    ? null
    : toScorePercent(detail.value.claim.credibility_score),
)

const hasVerification = computed(() => (detail.value?.verification.per_question.length ?? 0) > 0)

/** 隐藏特征展示：后端为 JSON 数组，统一转成多行文本 */
function hiddenFeaturesText(party: AdminClaimParty | null): string {
  return safeText(toDisplayText(party?.hidden_features), '未填写隐藏特征')
}

/** ---------- 审核操作 ---------- */
async function submitReview(action: ReviewAction): Promise<void> {
  if (!selectedClaimId.value) {
    ElMessage.warning('请先选择一条认领记录')
    return
  }

  const trimmed = comment.value.trim()
  if (action !== 'approve' && (trimmed.length < 10 || trimmed.length > 500)) {
    ElMessage.warning('驳回或要求补充信息时，备注需为 10~500 字')
    return
  }
  if (action === 'approve' && trimmed.length === 0) {
    ElMessage.info('未填写备注：建议补充审核依据，便于后续追溯')
  }

  actionLoading.value = action
  try {
    const result = await reviewClaim(selectedClaimId.value, { action, comment: trimmed })
    const statusText = result.claim_status
      ? (CLAIM_STATUS_LABELS[result.claim_status] ?? result.claim_status)
      : '已更新'
    ElMessage.success(`已${REVIEW_ACTION_LABELS[action]}，认领状态：${statusText}`)
    comment.value = ''
    await Promise.all([loadClaims(), loadDetail()])
  } catch {
    // 错误提示由 axios 拦截器统一处理（含 409 STATE_CONFLICT）
  } finally {
    actionLoading.value = null
  }
}

function goFullLogs(): void {
  void router.push({
    name: 'admin-logs',
    query: { target_type: 'claim', target_id: String(selectedClaimId.value ?? '') },
  })
}

function goItemDetail(itemId: number): void {
  if (!itemId) return
  void router.push({ name: 'item-detail', params: { id: itemId } })
}

// URL 中的 claim_id 变化（刷新、前进后退）时同步选中项
watch(
  () => route.query.claim_id,
  (value) => {
    const id = Number(value)
    if (Number.isFinite(id) && id > 0 && id !== selectedClaimId.value) {
      selectedClaimId.value = id
      void loadDetail()
    }
  },
)

onMounted(async () => {
  const id = Number(route.query.claim_id)
  if (Number.isFinite(id) && id > 0) selectedClaimId.value = id
  await loadClaims()
  if (selectedClaimId.value && !detail.value) await loadDetail()
})
</script>

<template>
  <div class="review sg-page">
    <div class="sg-page__header">
      <div>
        <h1 class="sg-page__title">管理员审核台</h1>
        <p class="sg-page__subtitle">
          综合匹配依据、隐藏特征验证结果与实际交接情况作出结论；系统只提供辅助建议。
        </p>
      </div>
      <div class="review__actions">
        <el-button :loading="listLoading" @click="loadClaims">
          <el-icon><Refresh /></el-icon>
          <span>刷新列表</span>
        </el-button>
        <el-button @click="goFullLogs">审计日志</el-button>
      </div>
    </div>

    <PrivacyHint
      class="review__privacy"
      title="审核权限与合规要求"
      level="warning"
      :items="[
        '隐藏特征与联系方式仅用于本次审核，禁止截图外传或用于其他用途。',
        '验证结果只展示「是否命中 + 得分 + 理由」，系统不返回标准答案与比对细节。',
        '审核结论需填写备注依据（驳回 / 要求补充信息时 10~500 字），操作会写入审计日志并可追溯。',
      ]"
    />

    <el-row :gutter="16">
      <!-- 左：待审核列表 -->
      <el-col :xs="24" :md="9" :lg="8">
        <el-card class="review__list-card" shadow="never">
          <template #header>
            <div class="review__list-header">
              <span>认领申请（{{ total }}）</span>
              <el-select
                v-model="listQuery.status"
                size="small"
                class="review__status-select"
                @change="handleStatusChange"
              >
                <el-option
                  v-for="option in STATUS_OPTIONS"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </div>
          </template>

          <!-- 列表接口不可用时的降级入口 -->
          <el-alert
            v-if="listUnsupported"
            class="review__list-error"
            type="info"
            :closable="false"
            show-icon
            title="待审核列表接口不可用"
            description="契约附录 A 未列出该接口；可直接输入认领 ID 查询详情并完成审核。"
          />
          <el-alert
            v-else-if="listError"
            class="review__list-error"
            type="error"
            :closable="false"
            show-icon
            title="列表加载失败"
            :description="listError"
          />

          <div class="review__manual">
            <el-input-number
              v-model="manualClaimId"
              class="review__manual-input"
              :min="1"
              :step="1"
              :controls="false"
              placeholder="认领 ID，例如 4001"
            />
            <el-button type="primary" plain @click="handleManualQuery">查询</el-button>
          </div>

          <el-skeleton v-if="listLoading && claims.length === 0" :rows="5" animated />

          <template v-else-if="claims.length > 0">
            <ul class="review__list">
              <li
                v-for="item in claims"
                :key="item.claim_id"
                class="review__list-item"
                :class="{ 'is-active': item.claim_id === selectedClaimId }"
                @click="selectClaim(item.claim_id)"
              >
                <div class="review__list-item-head">
                  <span class="review__list-item-title">{{ item.item_title }}</span>
                  <StatusTag :status="item.status" kind="claim" size="small" />
                </div>
                <p class="sg-muted review__list-item-meta">
                  #{{ item.claim_id }} · {{ item.claimant_nickname }} ·
                  {{ formatDateTime(item.created_at) }}
                </p>
                <div class="review__list-item-tags">
                  <StatusTag
                    v-if="item.risk_level"
                    :status="item.risk_level"
                    kind="risk"
                    size="small"
                    effect="plain"
                  />
                  <el-tag
                    v-if="item.verification_score !== null && item.verification_score !== undefined"
                    size="small"
                    type="info"
                    effect="plain"
                  >
                    可信度 {{ toScorePercent(item.verification_score) }}
                  </el-tag>
                  <el-tag
                    v-if="item.match_total_score !== null && item.match_total_score !== undefined"
                    size="small"
                    effect="plain"
                  >
                    匹配 {{ toScorePercent(item.match_total_score) }}
                  </el-tag>
                </div>
              </li>
            </ul>

            <el-pagination
              class="review__pagination"
              small
              background
              layout="prev, pager, next"
              :total="total"
              :current-page="listQuery.page"
              :page-size="listQuery.page_size"
              @current-change="handleListPage"
            />
          </template>

          <EmptyState
            v-else
            description="没有可展示的认领申请"
            sub-description="可切换状态筛选，或直接输入认领 ID 查询。"
            size="small"
          />
        </el-card>
      </el-col>

      <!-- 右：审核详情 -->
      <el-col :xs="24" :md="15" :lg="16">
        <el-skeleton v-if="detailLoading" class="review__detail-skeleton" :rows="10" animated />

        <el-alert
          v-else-if="detailError"
          type="error"
          :closable="false"
          show-icon
          title="审核详情加载失败"
          :description="detailError"
        />

        <EmptyState
          v-else-if="!detail"
          description="请选择一条认领申请"
          sub-description="左侧列表展示待审核与历史认领；也可直接输入认领 ID。选中后将展示匹配维度分、逐题验证结果、双方脱敏联系方式与历史日志。"
        />

        <template v-else>
          <!-- 认领概要 + 匹配依据 -->
          <el-card class="review__card" shadow="never">
            <template #header>
              <div class="review__card-header">
                <span>认领 #{{ detail.claim.id }}</span>
                <StatusTag :status="detail.claim.status" kind="claim" size="small" />
              </div>
            </template>

            <div class="review__score-grid">
              <div class="review__score-item">
                <span class="sg-muted">隐藏特征可信度</span>
                <el-progress
                  v-if="verificationScore !== null"
                  :percentage="verificationScore ?? 0"
                  :stroke-width="12"
                  :color="(verificationScore ?? 0) >= 75 ? '#2ba471' : (verificationScore ?? 0) >= 45 ? '#e37318' : '#d54941'"
                />
                <span v-else class="sg-muted">未提交验证</span>
              </div>
              <div class="review__score-item">
                <span class="sg-muted">综合可信度</span>
                <el-progress
                  v-if="credibilityScore !== null"
                  :percentage="credibilityScore ?? 0"
                  :stroke-width="12"
                  :color="(credibilityScore ?? 0) >= 75 ? '#2ba471' : (credibilityScore ?? 0) >= 45 ? '#e37318' : '#d54941'"
                />
                <span v-else class="sg-muted">—</span>
              </div>
              <div class="review__score-item">
                <span class="sg-muted">风险等级</span>
                <StatusTag
                  v-if="detail.claim.risk_level"
                  :status="detail.claim.risk_level"
                  kind="risk"
                />
                <span v-else class="sg-muted">—</span>
              </div>
              <div class="review__score-item">
                <span class="sg-muted">发起时间</span>
                <span>{{ formatDateTime(detail.claim.created_at) }}</span>
              </div>
            </div>

            <h3 class="review__sub-title">匹配依据</h3>
            <MatchScoreCard :candidate="detail.match" :show-claim="false" />
          </el-card>

          <!-- 双方物品 -->
          <el-card class="review__card" shadow="never">
            <template #header>
              <div class="review__card-header">
                <span>双方物品与隐藏特征</span>
                <span class="sg-muted">隐藏特征仅管理员可见，禁止外传</span>
              </div>
            </template>

            <el-row :gutter="16">
              <el-col :xs="24" :md="12">
                <div class="review__party">
                  <div class="review__party-head">
                    <el-tag type="danger" size="small" effect="plain">失物</el-tag>
                    <el-button
                      v-if="detail.lost_item"
                      link
                      type="primary"
                      @click="goItemDetail(detail.lost_item?.id ?? 0)"
                    >
                      #{{ detail.lost_item.id }}
                    </el-button>
                  </div>
                  <p class="review__party-title">
                    {{ safeText(detail.lost_item?.title, '未关联失物记录') }}
                  </p>
                  <p class="sg-muted review__party-line">
                    发布者：{{ safeText(detail.lost_item?.owner?.username) }} · 联系方式：
                    {{ safeText(detail.lost_item?.owner?.contact_id, '未提供') }}
                    <el-tag size="small" type="info" effect="plain">已脱敏</el-tag>
                  </p>
                  <p class="sg-muted review__party-line">隐藏特征：</p>
                  <p class="sg-pre-wrap review__text">{{ hiddenFeaturesText(detail.lost_item) }}</p>
                </div>
              </el-col>

              <el-col :xs="24" :md="12">
                <div class="review__party">
                  <div class="review__party-head">
                    <el-tag type="success" size="small" effect="plain">拾物</el-tag>
                    <el-button
                      v-if="detail.found_item"
                      link
                      type="primary"
                      @click="goItemDetail(detail.found_item?.id ?? 0)"
                    >
                      #{{ detail.found_item.id }}
                    </el-button>
                  </div>
                  <p class="review__party-title">
                    {{ safeText(detail.found_item?.title, '未关联拾物记录') }}
                  </p>
                  <p class="sg-muted review__party-line">
                    发布者：{{ safeText(detail.found_item?.owner?.username) }} · 联系方式：
                    {{ safeText(detail.found_item?.owner?.contact_id, '未提供') }}
                    <el-tag size="small" type="info" effect="plain">已脱敏</el-tag>
                  </p>
                  <p class="sg-muted review__party-line">隐藏特征：</p>
                  <p class="sg-pre-wrap review__text">{{ hiddenFeaturesText(detail.found_item) }}</p>
                </div>
              </el-col>
            </el-row>
          </el-card>

          <!-- 逐题验证结果 -->
          <el-card class="review__card" shadow="never">
            <template #header>
              <div class="review__card-header">
                <span>验证结果</span>
                <span class="sg-muted">只含是否命中 / 得分 / 理由，不含标准答案</span>
              </div>
            </template>

            <el-table v-if="hasVerification" :data="detail.verification.per_question" size="small" stripe>
              <el-table-column prop="question_text" label="问题" min-width="220">
                <template #default="{ row }">
                  {{ safeText(row.question_text, `问题 ${row.question_id}`) }}
                </template>
              </el-table-column>
              <el-table-column label="是否命中" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.matched ? 'success' : 'danger'" size="small" effect="plain">
                    {{ row.matched ? '命中' : '未命中' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="得分" width="90">
                <template #default="{ row }">{{ toScorePercent(row.score) }}</template>
              </el-table-column>
              <el-table-column prop="reason" label="判定理由" min-width="180" />
            </el-table>

            <el-alert
              v-else
              type="info"
              :closable="false"
              show-icon
              title="申请人尚未提交验证作答"
              description="可要求补充信息，或先联系申请人完成验证后再审核。"
            />

            <div class="review__verify-meta">
              <span class="sg-muted">
                已用尝试次数：{{ detail.verification.attempts_used ?? '—' }}
              </span>
            </div>

            <el-alert
              v-for="(tip, index) in detail.verification.risk_tips"
              :key="`risk-${index}`"
              class="review__risk-tip"
              type="warning"
              :closable="false"
              show-icon
              :title="tip"
            />
          </el-card>

          <!-- 历史日志 -->
          <el-card class="review__card" shadow="never">
            <template #header>
              <div class="review__card-header">
                <span>历史操作日志</span>
                <el-button link type="primary" @click="goFullLogs">按 target_id 查询全部日志</el-button>
              </div>
            </template>

            <el-timeline v-if="detail.history.length > 0">
              <el-timeline-item
                v-for="(log, index) in detail.history"
                :key="`${log.action}-${index}`"
                :timestamp="formatDateTime(log.created_at)"
                placement="top"
              >
                <p class="review__log-title">
                  {{ log.action }}
                  <span class="sg-muted">· {{ safeText(log.operator, '系统') }}</span>
                </p>
                <pre v-if="log.detail" class="review__log-detail">{{ stringifyDetail(log.detail) }}</pre>
              </el-timeline-item>
            </el-timeline>
            <p v-else class="sg-muted">暂无日志记录。</p>
          </el-card>

          <!-- 审核操作 -->
          <el-card class="review__card review__card--action" shadow="never">
            <template #header>
              <div class="review__card-header">
                <span>审核结论</span>
                <span v-if="selectedSummary" class="sg-muted">
                  #{{ selectedSummary.claim_id }} · {{ selectedSummary.item_title }}
                </span>
              </div>
            </template>

            <el-form label-position="top" @submit.prevent>
              <el-form-item label="审核备注（驳回 / 要求补充信息时必填，10~500 字）">
                <el-input
                  v-model="comment"
                  type="textarea"
                  :rows="3"
                  maxlength="500"
                  show-word-limit
                  placeholder="例如：匹配维度分与隐藏特征验证均通过，已核对校园卡学号，同意线下交接。"
                />
              </el-form-item>
            </el-form>

            <div class="review__submit">
              <el-popconfirm
                title="确认通过该认领？请确保已完成线下核验。"
                confirm-button-text="确认通过"
                cancel-button-text="取消"
                width="260"
                @confirm="submitReview('approve')"
              >
                <template #reference>
                  <el-button type="success" :loading="actionLoading === 'approve'">通过认领</el-button>
                </template>
              </el-popconfirm>

              <el-popconfirm
                title="确认要求申请人补充信息？"
                confirm-button-text="确认"
                cancel-button-text="取消"
                width="240"
                @confirm="submitReview('request_more_info')"
              >
                <template #reference>
                  <el-button type="warning" :loading="actionLoading === 'request_more_info'">
                    要求补充信息
                  </el-button>
                </template>
              </el-popconfirm>

              <el-popconfirm
                title="确认驳回该认领？该操作会写入审计日志。"
                confirm-button-text="确认驳回"
                cancel-button-text="取消"
                width="260"
                @confirm="submitReview('reject')"
              >
                <template #reference>
                  <el-button type="danger" :loading="actionLoading === 'reject'">驳回认领</el-button>
                </template>
              </el-popconfirm>
            </div>

            <el-alert
              class="review__action-note"
              type="warning"
              :closable="false"
              show-icon
              title="候选匹配不等于确认归属"
              description="请勿仅凭匹配分数作出结论；应结合隐藏特征验证、风险等级与实际交接情况综合判断。"
            />
          </el-card>
        </template>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.review__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
}

.review__privacy {
  margin-bottom: var(--sg-gap-sm);
}

.review__list-card,
.review__card {
  margin-bottom: var(--sg-gap);
}

.review__list-header,
.review__card-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
}

.review__status-select {
  width: 132px;
}

.review__list-error {
  margin-bottom: var(--sg-gap-xs);
  align-items: flex-start;
}

.review__manual {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-xs);
  margin-bottom: var(--sg-gap-xs);
}

.review__manual-input {
  flex: 1 1 auto;
  width: auto;
}

.review__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.review__list-item {
  padding: var(--sg-gap-xs);
  border: 1px solid var(--sg-border-light);
  border-radius: var(--sg-radius-sm);
  cursor: pointer;
  transition: all 0.2s ease;
}

.review__list-item + .review__list-item {
  margin-top: var(--sg-gap-xs);
}

.review__list-item:hover {
  border-color: var(--sg-primary);
}

.review__list-item.is-active {
  border-color: var(--sg-primary);
  background: var(--sg-primary-light);
}

.review__list-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
}

.review__list-item-title {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review__list-item-meta {
  margin: 4px 0;
}

.review__list-item-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.review__pagination {
  margin-top: var(--sg-gap-xs);
  justify-content: center;
}

.review__detail-skeleton {
  padding: var(--sg-gap);
}

.review__score-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sg-gap);
}

.review__score-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 14px;
}

.review__sub-title {
  margin: var(--sg-gap) 0 var(--sg-gap-xs);
  font-size: 14px;
  font-weight: 600;
}

.review__party {
  padding: var(--sg-gap-xs);
  border: 1px solid var(--sg-border-light);
  border-radius: var(--sg-radius-sm);
  margin-bottom: var(--sg-gap-sm);
}

.review__party-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
}

.review__party-title {
  margin: 6px 0;
  font-size: 14px;
  font-weight: 600;
}

.review__party-line {
  margin: 4px 0;
}

.review__text {
  margin: 0;
  font-size: 14px;
}

.review__verify-meta {
  margin-top: var(--sg-gap-xs);
}

.review__risk-tip {
  margin-top: var(--sg-gap-xs);
  align-items: flex-start;
}

.review__log-title {
  margin: 0;
  font-size: 14px;
}

.review__log-detail {
  margin: 4px 0 0;
  padding: 6px 8px;
  background: var(--sg-bg);
  border-radius: var(--sg-radius-sm);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.review__card--action {
  border-color: var(--sg-primary);
}

.review__submit {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
}

.review__action-note {
  margin-top: var(--sg-gap-sm);
  align-items: flex-start;
}

@media (max-width: 767px) {
  .review__actions {
    width: 100%;
  }

  .review__actions .el-button {
    flex: 1 1 auto;
    margin-left: 0;
  }

  .review__score-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .review__submit .el-button {
    width: 100%;
    margin-left: 0;
  }
}
</style>
