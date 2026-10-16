<script setup lang="ts">
/**
 * StatusTag —— 统一状态标签组件。
 * 状态文案映射（业务规则固定，不得在页面里另写一套）：
 * published 已发布 / matched 已匹配 / claiming 认领中 / pending_review 待审核 /
 * claimed 已认领 / rejected 已驳回 / request_more_info 待补充信息。
 */
import { computed } from 'vue'
import type { AiStatus, ItemStatus, TagType } from '@/types/item'
import { AI_STATUS_LABELS, AI_STATUS_TAG, ITEM_STATUS_LABELS, ITEM_STATUS_TAG } from '@/types/item'
import type { ClaimStatus, RiskLevel } from '@/types/claim'
import {
  CLAIM_STATUS_LABELS,
  CLAIM_STATUS_TAG,
  RISK_LEVEL_LABELS,
  RISK_LEVEL_TAG,
} from '@/types/claim'

/** 状态所属领域：物品状态 / 认领状态 / AI 分析状态 / 风险等级 */
type StatusKind = 'item' | 'claim' | 'ai' | 'risk'

interface Props {
  /** 状态原始值（后端返回值） */
  status: string
  /** 领域，决定文案映射表 */
  kind?: StatusKind
  size?: 'large' | 'default' | 'small'
  effect?: 'dark' | 'light' | 'plain'
}

const props = withDefaults(defineProps<Props>(), {
  kind: 'item',
  size: 'default',
  effect: 'light',
})

/** 未知状态直接回显原值，避免因后端新增枚举导致页面空白 */
const label = computed<string>(() => {
  if (props.kind === 'claim') return CLAIM_STATUS_LABELS[props.status as ClaimStatus] ?? props.status
  if (props.kind === 'ai') return AI_STATUS_LABELS[props.status as AiStatus] ?? props.status
  if (props.kind === 'risk') return RISK_LEVEL_LABELS[props.status as RiskLevel] ?? props.status
  return ITEM_STATUS_LABELS[props.status as ItemStatus] ?? props.status
})

const tagType = computed<TagType>(() => {
  if (props.kind === 'claim') return CLAIM_STATUS_TAG[props.status as ClaimStatus] ?? 'info'
  if (props.kind === 'ai') return AI_STATUS_TAG[props.status as AiStatus] ?? 'info'
  if (props.kind === 'risk') return RISK_LEVEL_TAG[props.status as RiskLevel] ?? 'info'
  return ITEM_STATUS_TAG[props.status as ItemStatus] ?? 'info'
})
</script>

<template>
  <el-tag :type="tagType" :size="props.size" :effect="props.effect" disable-transitions>
    {{ label }}
  </el-tag>
</template>
