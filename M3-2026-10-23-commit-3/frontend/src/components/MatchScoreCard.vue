<script setup lang="ts">
/**
 * MatchScoreCard —— 单条候选匹配卡片。
 * 展示内容：匹配总分、6 个维度分（类别/属性/图像/时间/地点/品牌型号）、匹配依据、
 * 不确定因素、风险提示，以及「发起认领」入口。
 *
 * 合规要求：卡片底部固定展示「候选匹配不等于确认归属」，任何页面都不得改成肯定性表述。
 */
import { computed } from 'vue'
import type { MatchCandidate, MatchScoreKey } from '@/types/match'
import {
  MATCH_DISCLAIMER_TEXT,
  MATCH_LEVEL_LABELS,
  MATCH_LEVEL_TAG,
  MATCH_SCORE_HINTS,
  MATCH_SCORE_KEYS,
  MATCH_SCORE_LABELS,
  toScorePercent,
} from '@/types/match'
import { POST_TYPE_LABELS } from '@/types/item'

interface Props {
  candidate: MatchCandidate
  /** 候选序号，从 1 开始；0 表示不展示序号 */
  rank?: number
  /** 是否展示「发起认领」按钮 */
  showClaim?: boolean
  /** 认领请求进行中（同一时刻只允许一个候选提交） */
  claiming?: boolean
  /** 操作按钮禁用（未登录等） */
  actionDisabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  rank: 0,
  showClaim: true,
  claiming: false,
  actionDisabled: false,
})

const emit = defineEmits<{
  /** 点击「发起认领」，携带 match_id */
  (event: 'claim', matchId: number): void
  /** 点击「查看对方物品」 */
  (event: 'view-detail', itemId: number): void
}>()

const totalPercent = computed(() => toScorePercent(props.candidate.total_score))

/** 总分颜色：≥70 高、40~69 中、<40 低，仅用于视觉提示，不代表归属判定 */
const totalColor = computed(() => {
  if (totalPercent.value >= 70) return '#2ba471'
  if (totalPercent.value >= 40) return '#e37318'
  return '#d54941'
})

/** 6 个维度：字段缺失按 0 处理，保证卡片结构稳定 */
const dimensions = computed<
  Array<{ key: MatchScoreKey; label: string; hint: string; percent: number }>
>(() => {
  const detail = (props.candidate.score_detail ?? {}) as Partial<Record<MatchScoreKey, number>>
  return MATCH_SCORE_KEYS.map((key) => ({
    key,
    label: MATCH_SCORE_LABELS[key],
    hint: MATCH_SCORE_HINTS[key],
    percent: toScorePercent(detail[key] ?? 0),
  }))
})

const evidence = computed(() => props.candidate.evidence ?? [])
const uncertainties = computed(() => props.candidate.uncertainties ?? [])
const riskTips = computed(() => props.candidate.risk_tips ?? [])

const matchedItem = computed(() => props.candidate.matched_item ?? null)
const matchedTitle = computed(
  () => matchedItem.value?.title || `物品 #${props.candidate.matched_item_id}`,
)
const matchedPostTypeLabel = computed(() =>
  matchedItem.value ? POST_TYPE_LABELS[matchedItem.value.post_type] : '',
)
</script>

<template>
  <el-card class="match-card" shadow="hover">
    <div class="match-card__head">
      <div class="match-card__title">
        <span v-if="props.rank > 0" class="match-card__rank">候选 {{ props.rank }}</span>
        <span class="match-card__name">{{ matchedTitle }}</span>
        <el-tag v-if="matchedPostTypeLabel" size="small" effect="plain">
          {{ matchedPostTypeLabel }}
        </el-tag>
        <el-tag v-if="matchedItem" size="small" type="info" effect="plain">
          {{ matchedItem.category }}
        </el-tag>
      </div>

      <div class="match-card__score">
        <el-progress
          type="dashboard"
          :percentage="totalPercent"
          :color="totalColor"
          :width="104"
          :stroke-width="8"
        />
        <div class="match-card__score-text">
          <strong>匹配总分 {{ totalPercent }}</strong>
          <!-- 档位由后端 match_level 给出：low 不得展示为「高匹配」 -->
          <el-tag
            v-if="props.candidate.match_level"
            size="small"
            :type="MATCH_LEVEL_TAG[props.candidate.match_level]"
          >
            {{ MATCH_LEVEL_LABELS[props.candidate.match_level] }}
          </el-tag>
          <span class="sg-muted">满分 100，仅作参考</span>
        </div>
      </div>
    </div>

    <!-- 6 个维度分：维度名 + 释义 + 进度条 -->
    <div class="match-card__dimensions">
      <div v-for="item in dimensions" :key="item.key" class="match-card__dimension">
        <div class="match-card__dimension-head">
          <el-tooltip :content="item.hint" placement="top">
            <span class="match-card__dimension-label">{{ item.label }}</span>
          </el-tooltip>
          <span class="match-card__dimension-value">{{ item.percent }}</span>
        </div>
        <el-progress :percentage="item.percent" :show-text="false" :stroke-width="8" />
      </div>
    </div>

    <div class="match-card__reason">
      <div class="match-card__reason-block">
        <p class="match-card__reason-title">匹配依据</p>
        <ul v-if="evidence.length > 0" class="match-card__list">
          <li v-for="(text, index) in evidence" :key="`evidence-${index}`">{{ text }}</li>
        </ul>
        <p v-else class="sg-muted">算法未返回文字依据，请结合维度分与实物核验。</p>
      </div>

      <div class="match-card__reason-block">
        <p class="match-card__reason-title">不确定因素</p>
        <ul v-if="uncertainties.length > 0" class="match-card__list match-card__list--warning">
          <li v-for="(text, index) in uncertainties" :key="`uncertainty-${index}`">{{ text }}</li>
        </ul>
        <p v-else class="sg-muted">算法未标注不确定因素，仍建议线下核验。</p>
      </div>
    </div>

    <el-alert
      v-if="riskTips.length > 0"
      class="match-card__risk"
      type="error"
      :closable="false"
      show-icon
      title="风险提示"
    >
      <ul class="match-card__list">
        <li v-for="(text, index) in riskTips" :key="`risk-${index}`">{{ text }}</li>
      </ul>
    </el-alert>

    <div class="match-card__footer">
      <p class="match-card__disclaimer">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ MATCH_DISCLAIMER_TEXT }}</span>
      </p>
      <div class="match-card__actions">
        <el-button link type="primary" @click="emit('view-detail', props.candidate.matched_item_id)">
          查看对方物品
        </el-button>
        <el-button
          v-if="props.showClaim"
          type="primary"
          :loading="props.claiming"
          :disabled="props.actionDisabled"
          @click="emit('claim', props.candidate.match_id)"
        >
          发起认领
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.match-card {
  margin-bottom: var(--sg-gap);
}

.match-card__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-sm);
}

.match-card__title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sg-gap-xs);
  font-size: 16px;
  font-weight: 600;
}

.match-card__rank {
  font-size: 12px;
  font-weight: 500;
  color: #fff;
  background: var(--sg-primary);
  border-radius: 999px;
  padding: 2px 10px;
}

.match-card__name {
  word-break: break-word;
}

.match-card__score {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-sm);
}

.match-card__score-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 14px;
}

.match-card__dimensions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sg-gap-sm) var(--sg-gap);
  margin: var(--sg-gap) 0;
}

.match-card__dimension-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 2px;
}

.match-card__dimension-label {
  color: var(--sg-text-secondary);
  border-bottom: 1px dashed var(--sg-border);
  cursor: help;
}

.match-card__dimension-value {
  font-weight: 600;
}

.match-card__reason {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sg-gap);
}

.match-card__reason-title {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 600;
}

.match-card__list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.match-card__list--warning li::marker {
  color: var(--sg-warning);
}

.match-card__risk {
  margin-top: var(--sg-gap-sm);
}

.match-card__footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
  margin-top: var(--sg-gap);
  padding-top: var(--sg-gap-sm);
  border-top: 1px solid var(--sg-border-light);
}

.match-card__disclaimer {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--sg-danger);
}

@media (max-width: 767px) {
  .match-card__dimensions {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .match-card__reason {
    grid-template-columns: minmax(0, 1fr);
  }

  .match-card__score {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
