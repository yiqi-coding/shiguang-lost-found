<script setup lang="ts">
/**
 * 匹配结果 MatchResultView —— 路由 /items/:id/matches
 * 职责：展示候选匹配（总分 + 6 个维度分 + 匹配依据 + 不确定因素 + 风险提示），
 *       支持调整 top_k 与重新计算，并从某条候选发起认领。
 * 依赖接口：GET /items/{item_id}/matches?top_k=、POST /items/{item_id}/match、POST /claims。
 *
 * 合规要求（必须保留）：页面固定展示「候选匹配不等于确认归属」，不得使用认定归属的措辞。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getItem } from '@/api/items'
import { fetchMatches, recalculateMatches } from '@/api/matches'
import { createClaim } from '@/api/claims'
import { ApiError } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import MatchScoreCard from '@/components/MatchScoreCard.vue'
import { savePendingClaim } from '@/stores/session'
import { useUserStore } from '@/stores/user'
import type { ItemDetail } from '@/types/item'
import type { MatchCandidate, MatchListResult, MatchScoreKey } from '@/types/match'
import {
  DEFAULT_TOP_K,
  MATCH_ALGO_NOTE,
  MATCH_DEGRADED_TEXT,
  MATCH_DISCLAIMER_TEXT,
  MATCH_SCORE_HINTS,
  MATCH_SCORE_KEYS,
  MATCH_SCORE_LABELS,
  MAX_TOP_K,
} from '@/types/match'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const itemId = computed(() => Number(route.params.id))

const item = ref<ItemDetail | null>(null)
const candidates = ref<MatchCandidate[]>([])
const topK = ref<number>(DEFAULT_TOP_K)
const generatedAt = ref<string | null>(null)
const modelVersion = ref<string | null>(null)
/** 算法服务不可用时后端会返回 degraded=true，此时必须显式标注「降级结果」 */
const degraded = ref(false)
const cached = ref(false)
const loading = ref(false)
const recalculating = ref(false)
const claimingMatchId = ref<number | null>(null)
const errorMessage = ref('')

/** 把归一化后的匹配结果写入页面状态 */
function applyMatchResult(result: MatchListResult): void {
  candidates.value = result.candidates
  generatedAt.value = result.generated_at ?? null
  modelVersion.value = result.model_version ?? null
  degraded.value = result.degraded === true
  cached.value = result.cached === true
}

/** 维度说明（可折叠）：让用户与管理员理解分数来源 */
const dimensionNotes = computed<Array<{ key: MatchScoreKey; label: string; hint: string }>>(() =>
  MATCH_SCORE_KEYS.map((key) => ({
    key,
    label: MATCH_SCORE_LABELS[key],
    hint: MATCH_SCORE_HINTS[key],
  })),
)

const topKOptions = computed(() => {
  const options = new Set<number>([3, DEFAULT_TOP_K, 10])
  return [...options].filter((value) => value <= MAX_TOP_K).sort((a, b) => a - b)
})

/** 读取候选匹配（同时尝试读取物品标题，失败不影响匹配展示） */
async function loadMatches(): Promise<void> {
  if (!Number.isFinite(itemId.value) || itemId.value <= 0) {
    errorMessage.value = '物品 ID 不合法'
    return
  }

  loading.value = true
  errorMessage.value = ''
  try {
    applyMatchResult(await fetchMatches(itemId.value, topK.value))
  } catch (error) {
    candidates.value = []
    if (error instanceof ApiError && error.code === 'MATCH_NOT_FOUND') {
      errorMessage.value = '尚未生成匹配结果，可点击「重新计算匹配」触发一次计算。'
    } else {
      errorMessage.value = error instanceof Error ? error.message : '匹配结果加载失败'
    }
  } finally {
    loading.value = false
  }
}

async function loadItem(): Promise<void> {
  try {
    item.value = await getItem(itemId.value)
  } catch {
    // 物品信息仅用于展示标题，失败不阻塞匹配结果
    item.value = null
  }
}

/** 重新计算匹配（需登录） */
async function handleRecalculate(): Promise<void> {
  if (!userStore.ensureLogin('重新计算匹配需要登录后进行')) return
  recalculating.value = true
  try {
    const result = await recalculateMatches(itemId.value, topK.value)
    applyMatchResult(result)
    ElMessage.success(`匹配已重新计算，共 ${result.candidates.length} 条候选`)
  } catch {
    // 错误提示由 axios 拦截器统一处理
  } finally {
    recalculating.value = false
  }
}

/** 从某条候选发起认领：POST /claims { match_id }，成功后跳转验证页 */
async function handleClaim(matchId: number): Promise<void> {
  if (!userStore.ensureLogin('发起认领需要登录后进行')) return

  claimingMatchId.value = matchId
  try {
    const created = await createClaim({ match_id: matchId })
    // 暂存问题列表：验证页在 GET /claims/{id} 未实现时可降级展示
    savePendingClaim(created)
    ElMessage.success('认领已创建，请回答 2~3 个隐藏特征验证问题')
    void router.push({ name: 'claim-verify', params: { id: created.claim_id } })
  } catch {
    // 错误提示由 axios 拦截器统一处理（如 MATCH_NOT_FOUND）
  } finally {
    claimingMatchId.value = null
  }
}

function goItemDetail(): void {
  void router.push({ name: 'item-detail', params: { id: itemId.value } })
}

function goMatchedItem(matchedItemId: number): void {
  void router.push({ name: 'item-detail', params: { id: matchedItemId } })
}

function handleTopKChange(value: number): void {
  topK.value = value
  void loadMatches()
}

// 支持在同一路由下切换物品 ID
watch(
  () => route.params.id,
  () => {
    void loadItem()
    void loadMatches()
  },
)

onMounted(() => {
  void loadItem()
  void loadMatches()
})
</script>

<template>
  <div class="matches sg-page">
    <div class="sg-page__header">
      <div>
        <h1 class="sg-page__title">匹配结果</h1>
        <p class="sg-page__subtitle">
          {{ item ? `物品：${item.title}` : `物品 ID：${itemId}` }} ·
          共 {{ candidates.length }} 条候选
          <span v-if="generatedAt"> · 生成于 {{ formatDateTime(generatedAt) }}</span>
          <span v-if="modelVersion"> · 模型版本 {{ modelVersion }}</span>
          <span v-if="cached"> · 来自缓存</span>
        </p>
      </div>
      <div class="matches__actions">
        <el-button @click="goItemDetail">返回物品详情</el-button>
        <el-button :loading="loading" @click="loadMatches">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
        <el-button type="primary" :loading="recalculating" @click="handleRecalculate">
          重新计算匹配
        </el-button>
      </div>
    </div>

    <!-- 合规文案：候选匹配不等于确认归属 -->
    <el-alert
      class="matches__disclaimer"
      type="warning"
      :closable="false"
      show-icon
      :title="MATCH_DISCLAIMER_TEXT"
      :description="MATCH_ALGO_NOTE"
    />

    <!-- 降级提示：算法服务不可用时的规则匹配结果必须显式标注 -->
    <el-alert
      v-if="degraded"
      class="matches__degraded"
      type="error"
      :closable="false"
      show-icon
      title="降级结果"
      :description="MATCH_DEGRADED_TEXT"
    />

    <el-card class="matches__toolbar" shadow="never">
      <div class="matches__toolbar-row">
        <div class="matches__toolbar-item">
          <span class="sg-muted">候选数量 top_k</span>
          <el-select
            :model-value="topK"
            class="matches__topk"
            @change="handleTopKChange"
          >
            <el-option v-for="value in topKOptions" :key="value" :label="`${value} 条`" :value="value" />
          </el-select>
        </div>
        <p class="sg-muted matches__toolbar-tip">
          认领流程：选择一条候选 → 回答隐藏特征验证问题 → 管理员综合匹配依据与验证结果审核。
        </p>
      </div>

      <el-collapse class="matches__collapse">
        <el-collapse-item title="六个维度的含义（点击展开）" name="dimensions">
          <ul class="matches__dimension-list">
            <li v-for="note in dimensionNotes" :key="note.key">
              <strong>{{ note.label }}</strong>
              <span class="sg-muted">：{{ note.hint }}</span>
            </li>
          </ul>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <el-alert
      v-if="errorMessage"
      class="matches__error"
      type="info"
      :closable="false"
      show-icon
      title="匹配结果提示"
      :description="errorMessage"
    />

    <el-skeleton v-if="loading" class="matches__skeleton" :rows="8" animated />

    <template v-else-if="candidates.length > 0">
      <MatchScoreCard
        v-for="(candidate, index) in candidates"
        :key="candidate.match_id"
        :candidate="candidate"
        :rank="index + 1"
        :claiming="claimingMatchId === candidate.match_id"
        :action-disabled="claimingMatchId !== null && claimingMatchId !== candidate.match_id"
        @claim="handleClaim"
        @view-detail="goMatchedItem"
      />
    </template>

    <EmptyState
      v-else
      description="暂无候选匹配"
      sub-description="可能对方尚未发布记录，或匹配算法服务未返回结果。可点击「重新计算匹配」再试一次；若后端未启动，这里会一直为空。"
      action-text="重新计算匹配"
      @action="handleRecalculate"
    />

    <el-alert
      class="matches__footer-note"
      type="error"
      :closable="false"
      show-icon
      title="重要提示"
    >
      <p class="matches__footer-text">{{ MATCH_DISCLAIMER_TEXT }}。</p>
      <p class="matches__footer-text">
        系统只提供候选匹配与辅助审核建议；请勿依据匹配分数私下交付物品，认领结果以管理员审核与线下交接记录为准。
      </p>
    </el-alert>
  </div>
</template>

<style scoped>
.matches__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
}

.matches__disclaimer {
  margin-bottom: var(--sg-gap-sm);
  align-items: flex-start;
}

.matches__degraded {
  margin-bottom: var(--sg-gap-sm);
  align-items: flex-start;
}

.matches__toolbar {
  margin-bottom: var(--sg-gap);
}

.matches__toolbar-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
}

.matches__toolbar-item {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-xs);
}

.matches__topk {
  width: 110px;
}

.matches__toolbar-tip {
  margin: 0;
}

.matches__collapse {
  margin-top: var(--sg-gap-xs);
}

.matches__dimension-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.8;
}

.matches__error {
  margin-bottom: var(--sg-gap-sm);
  align-items: flex-start;
}

.matches__skeleton {
  padding: var(--sg-gap);
}

.matches__footer-note {
  margin-top: var(--sg-gap);
  align-items: flex-start;
}

.matches__footer-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.75;
}

.matches__footer-text + .matches__footer-text {
  margin-top: 4px;
}

@media (max-width: 767px) {
  .matches__actions {
    width: 100%;
  }

  .matches__actions .el-button {
    flex: 1 1 auto;
    margin-left: 0;
  }
}
</style>
