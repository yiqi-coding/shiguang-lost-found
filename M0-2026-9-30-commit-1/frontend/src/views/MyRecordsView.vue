<script setup lang="ts">
/**
 * 我的记录 MyRecordsView —— 路由 /me
 * 职责：el-tabs 展示「我的发布」（我的失物/拾物及其状态、AI 分析状态）与
 *       「我的认领」（认领状态、可信度、风险等级），并提供详情 / 匹配 / 继续验证入口。
 * 依赖接口：GET /me/items、GET /me/claims（均需登录，路由守卫已做拦截）。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getMyItems } from '@/api/items'
import { getMyClaims } from '@/api/claims'
import EmptyState from '@/components/EmptyState.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useUserStore } from '@/stores/user'
import type { PageQuery } from '@/types/api'
import { DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE } from '@/types/api'
import type { ItemSummary } from '@/types/item'
import type { MyClaimRecord } from '@/types/claim'
import { toScorePercent } from '@/types/match'
import { formatDateTime } from '@/utils/format'

const router = useRouter()
const userStore = useUserStore()

const activeTab = ref<'items' | 'claims'>('items')

/** ---------- 我的发布 ---------- */
const itemQuery = reactive<PageQuery>({ page: 1, page_size: DEFAULT_PAGE_SIZE })
const myItems = ref<ItemSummary[]>([])
const itemTotal = ref(0)
const itemLoading = ref(false)
const itemError = ref('')

async function loadMyItems(): Promise<void> {
  itemLoading.value = true
  itemError.value = ''
  try {
    const result = await getMyItems({ ...itemQuery })
    myItems.value = result.items
    itemTotal.value = result.total
    itemQuery.page = result.page
    itemQuery.page_size = result.page_size
  } catch (error) {
    myItems.value = []
    itemTotal.value = 0
    itemError.value = error instanceof Error ? error.message : '我的发布加载失败'
  } finally {
    itemLoading.value = false
  }
}

/** ---------- 我的认领 ---------- */
const claimQuery = reactive<PageQuery>({ page: 1, page_size: DEFAULT_PAGE_SIZE })
const myClaims = ref<MyClaimRecord[]>([])
const claimTotal = ref(0)
const claimLoading = ref(false)
const claimError = ref('')

async function loadMyClaims(): Promise<void> {
  claimLoading.value = true
  claimError.value = ''
  try {
    const result = await getMyClaims({ ...claimQuery })
    myClaims.value = result.items
    claimTotal.value = result.total
    claimQuery.page = result.page
    claimQuery.page_size = result.page_size
  } catch (error) {
    myClaims.value = []
    claimTotal.value = 0
    claimError.value = error instanceof Error ? error.message : '我的认领加载失败'
  } finally {
    claimLoading.value = false
  }
}

/** ---------- 交互 ---------- */
function handleItemPage(page: number): void {
  itemQuery.page = page
  void loadMyItems()
}

function handleItemSize(size: number): void {
  itemQuery.page_size = Math.min(size, MAX_PAGE_SIZE)
  itemQuery.page = 1
  void loadMyItems()
}

function handleClaimPage(page: number): void {
  claimQuery.page = page
  void loadMyClaims()
}

function handleClaimSize(size: number): void {
  claimQuery.page_size = Math.min(size, MAX_PAGE_SIZE)
  claimQuery.page = 1
  void loadMyClaims()
}

function goDetail(itemId: number): void {
  void router.push({ name: 'item-detail', params: { id: itemId } })
}

function goMatches(itemId: number): void {
  void router.push({ name: 'item-matches', params: { id: itemId } })
}

/** 继续 / 查看认领验证：未提交验证的认领可继续作答 */
function goVerify(claimId: number): void {
  void router.push({ name: 'claim-verify', params: { id: claimId } })
}

function goCreateLost(): void {
  void router.push({ name: 'item-create-lost' })
}

function goItemList(): void {
  void router.push({ name: 'item-list' })
}

/** 认领是否仍需作答（已提交过验证的不再重复作答） */
function needVerify(record: MyClaimRecord): boolean {
  if (record.verification_score !== null && record.verification_score !== undefined) return false
  return record.status === 'claiming' || record.status === 'request_more_info'
}

onMounted(() => {
  void loadMyItems()
  void loadMyClaims()
})
</script>

<template>
  <div class="records sg-page">
    <div class="sg-page__header">
      <div>
        <h1 class="sg-page__title">我的记录</h1>
        <p class="sg-page__subtitle">
          {{ userStore.displayName }}（{{ userStore.roleLabel }}）· 发布与认领进度都在这里查看
        </p>
      </div>
      <div class="records__actions">
        <el-button type="primary" @click="goCreateLost">发布失物</el-button>
        <el-button @click="goItemList">浏览物品列表</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-tabs v-model="activeTab">
        <!-- 我的发布 -->
        <el-tab-pane label="我的发布" name="items">
          <div class="records__toolbar">
            <span class="sg-muted">共 {{ itemTotal }} 条</span>
            <el-button size="small" :loading="itemLoading" @click="loadMyItems">
              <el-icon><Refresh /></el-icon>
              <span>刷新</span>
            </el-button>
          </div>

          <el-alert
            v-if="itemError"
            class="records__error"
            type="error"
            :closable="false"
            show-icon
            title="加载失败"
            :description="itemError"
          />

          <el-skeleton v-if="itemLoading && myItems.length === 0" :rows="5" animated />

          <template v-else-if="myItems.length > 0">
            <el-table :data="myItems" row-key="item_id" stripe>
              <el-table-column label="标题" min-width="220">
                <template #default="{ row }">
                  <el-button link type="primary" @click="goDetail(row.item_id)">{{ row.title }}</el-button>
                </template>
              </el-table-column>
              <el-table-column label="类型" width="86">
                <template #default="{ row }">
                  <el-tag :type="row.post_type === 'lost' ? 'danger' : 'success'" size="small" effect="plain">
                    {{ row.post_type === 'lost' ? '失物' : '拾物' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="category" label="分类" width="100" />
              <el-table-column label="发生时间" width="150">
                <template #default="{ row }">{{ formatDateTime(row.event_time) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <StatusTag :status="row.status" kind="item" size="small" />
                </template>
              </el-table-column>
              <el-table-column label="AI 分析" width="118">
                <template #default="{ row }">
                  <StatusTag :status="row.ai_status" kind="ai" size="small" effect="plain" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="150" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="goDetail(row.item_id)">详情</el-button>
                  <el-button link type="primary" @click="goMatches(row.item_id)">匹配</el-button>
                </template>
              </el-table-column>

              <template #empty>
                <EmptyState
                  description="还没有发布记录"
                  sub-description="发布失物或拾物后，这里会显示状态与 AI 分析结果。"
                  action-text="发布失物"
                  size="small"
                  @action="goCreateLost"
                />
              </template>
            </el-table>

            <el-pagination
              class="records__pagination"
              background
              layout="total, sizes, prev, pager, next"
              :total="itemTotal"
              :current-page="itemQuery.page"
              :page-size="itemQuery.page_size"
              :page-sizes="[10, 20, 50]"
              @current-change="handleItemPage"
              @size-change="handleItemSize"
            />
          </template>

          <EmptyState
            v-else
            description="还没有发布记录"
            sub-description="发布失物或拾物后，这里会显示状态、AI 分析结果与匹配入口。"
            action-text="发布失物"
            @action="goCreateLost"
          />
        </el-tab-pane>

        <!-- 我的认领 -->
        <el-tab-pane label="我的认领" name="claims">
          <div class="records__toolbar">
            <span class="sg-muted">共 {{ claimTotal }} 条</span>
            <el-button size="small" :loading="claimLoading" @click="loadMyClaims">
              <el-icon><Refresh /></el-icon>
              <span>刷新</span>
            </el-button>
          </div>

          <el-alert
            v-if="claimError"
            class="records__error"
            type="error"
            :closable="false"
            show-icon
            title="加载失败"
            :description="claimError"
          />

          <el-skeleton v-if="claimLoading && myClaims.length === 0" :rows="5" animated />

          <template v-else-if="myClaims.length > 0">
            <el-table :data="myClaims" row-key="claim_id" stripe>
              <el-table-column label="物品" min-width="200">
                <template #default="{ row }">
                  <el-button link type="primary" @click="goMatches(row.item_id)">
                    {{ row.item_title }}
                  </el-button>
                </template>
              </el-table-column>
              <el-table-column prop="claim_id" label="认领编号" width="100" />
              <el-table-column label="发起时间" width="150">
                <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="112">
                <template #default="{ row }">
                  <StatusTag :status="row.status" kind="claim" size="small" />
                </template>
              </el-table-column>
              <el-table-column label="可信度" width="140">
                <template #default="{ row }">
                  <el-progress
                    v-if="row.verification_score !== null && row.verification_score !== undefined"
                    :percentage="toScorePercent(row.verification_score)"
                    :stroke-width="10"
                  />
                  <span v-else class="sg-muted">未提交验证</span>
                </template>
              </el-table-column>
              <el-table-column label="风险等级" width="110">
                <template #default="{ row }">
                  <StatusTag v-if="row.risk_level" :status="row.risk_level" kind="risk" size="small" />
                  <span v-else class="sg-muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="140" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="goVerify(row.claim_id)">
                    {{ needVerify(row) ? '继续验证' : '查看验证' }}
                  </el-button>
                  <el-button link type="primary" @click="goMatches(row.item_id)">匹配结果</el-button>
                </template>
              </el-table-column>

              <template #empty>
                <EmptyState
                  description="还没有认领记录"
                  sub-description="在匹配结果页选择一条候选并点击「发起认领」，即可回答隐藏特征验证问题。"
                  size="small"
                />
              </template>
            </el-table>

            <el-pagination
              class="records__pagination"
              background
              layout="total, sizes, prev, pager, next"
              :total="claimTotal"
              :current-page="claimQuery.page"
              :page-size="claimQuery.page_size"
              :page-sizes="[10, 20, 50]"
              @current-change="handleClaimPage"
              @size-change="handleClaimSize"
            />
          </template>

          <EmptyState
            v-else
            description="还没有认领记录"
            sub-description="认领必须基于某条候选匹配发起：先查看匹配结果，再回答隐藏特征验证问题。"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<style scoped>
.records__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
}

.records__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
  margin-bottom: var(--sg-gap-xs);
}

.records__error {
  margin-bottom: var(--sg-gap-sm);
}

.records__pagination {
  margin-top: var(--sg-gap);
  justify-content: flex-end;
}

@media (max-width: 767px) {
  .records__actions {
    width: 100%;
  }

  .records__actions .el-button {
    flex: 1 1 auto;
    margin-left: 0;
  }

  .records__pagination {
    justify-content: center;
  }
}
</style>
