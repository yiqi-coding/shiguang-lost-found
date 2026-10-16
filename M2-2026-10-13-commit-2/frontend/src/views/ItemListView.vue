<script setup lang="ts">
/**
 * 物品列表 ItemListView —— 路由 /items
 * 职责：按 post_type / category / status 筛选 + 关键词搜索，分页展示物品；
 *       列表接口不会返回 hidden_features，页面也不展示隐藏特征。
 * 依赖接口：GET /items?post_type=&category=&status=&page=&page_size=
 * 支持 /items?post_type=found 这类直达链接（首页入口会携带查询参数）。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listItems } from '@/api/items'
import type { ItemCategory, ItemListQuery, ItemStatus, ItemSummary } from '@/types/item'
import { CATEGORY_OPTIONS, ITEM_STATUS_LABELS, POST_TYPE_OPTIONS } from '@/types/item'
import { DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE } from '@/types/api'
import EmptyState from '@/components/EmptyState.vue'
import StatusTag from '@/components/StatusTag.vue'
import { formatDateTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()

/** 筛选与分页条件（直接映射后端查询参数） */
const query = reactive<ItemListQuery>({
  post_type: '',
  category: '',
  status: '',
  keyword: '',
  page: 1,
  page_size: DEFAULT_PAGE_SIZE,
})

const list = ref<ItemSummary[]>([])
const total = ref(0)
const loading = ref(false)
const errorMessage = ref('')

/** 状态下拉选项：文案与 StatusTag 共用同一份映射 */
const STATUS_OPTIONS: ReadonlyArray<{ value: ItemStatus; label: string }> = (
  Object.keys(ITEM_STATUS_LABELS) as ItemStatus[]
).map((value) => ({ value, label: ITEM_STATUS_LABELS[value] }))

async function loadList(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await listItems({ ...query })
    list.value = result.items
    total.value = result.total
    query.page = result.page
    query.page_size = result.page_size
  } catch (error) {
    list.value = []
    total.value = 0
    errorMessage.value = error instanceof Error ? error.message : '物品列表加载失败'
  } finally {
    loading.value = false
  }
}

/** 从路由查询参数初始化筛选条件 */
function applyRouteQuery(): void {
  const { post_type, category, status, keyword, page } = route.query
  if (post_type === 'lost' || post_type === 'found') query.post_type = post_type
  if (typeof category === 'string' && category) query.category = category as ItemCategory
  if (typeof status === 'string' && status) query.status = status as ItemStatus
  if (typeof keyword === 'string') query.keyword = keyword
  if (typeof page === 'string' && Number(page) > 0) query.page = Math.floor(Number(page))
}

function handleSearch(): void {
  query.page = 1
  void loadList()
}

function handleReset(): void {
  query.post_type = ''
  query.category = ''
  query.status = ''
  query.keyword = ''
  query.page = 1
  query.page_size = DEFAULT_PAGE_SIZE
  void loadList()
}

function handlePageChange(page: number): void {
  query.page = page
  void loadList()
}

function handleSizeChange(size: number): void {
  query.page_size = Math.min(size, MAX_PAGE_SIZE)
  query.page = 1
  void loadList()
}

function goDetail(item: ItemSummary): void {
  void router.push({ name: 'item-detail', params: { id: item.item_id } })
}

function goMatches(item: ItemSummary): void {
  void router.push({ name: 'item-matches', params: { id: item.item_id } })
}

function goCreateLost(): void {
  void router.push({ name: 'item-create-lost' })
}

onMounted(() => {
  applyRouteQuery()
  void loadList()
})
</script>

<template>
  <div class="item-list sg-page">
    <div class="sg-page__header">
      <div>
        <h1 class="sg-page__title">物品列表</h1>
        <p class="sg-page__subtitle">
          共 {{ total }} 条记录；列表仅展示公开字段，隐藏特征不会出现在这里。
        </p>
      </div>
      <div class="item-list__header-actions">
        <el-button :loading="loading" @click="loadList">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
        <el-button type="primary" @click="goCreateLost">发布失物</el-button>
      </div>
    </div>

    <!-- 筛选区：窄屏自动换行 -->
    <el-card class="item-list__filter" shadow="never">
      <el-form :inline="true" class="item-list__form" @submit.prevent>
        <el-form-item label="类型">
          <el-select v-model="query.post_type" class="item-list__select" placeholder="全部">
            <el-option label="全部" value="" />
            <el-option
              v-for="option in POST_TYPE_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="分类">
          <el-select v-model="query.category" class="item-list__select" placeholder="全部">
            <el-option label="全部" value="" />
            <el-option
              v-for="option in CATEGORY_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="状态">
          <el-select v-model="query.status" class="item-list__select" placeholder="全部">
            <el-option label="全部" value="" />
            <el-option
              v-for="option in STATUS_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="关键词">
          <el-input
            v-model="query.keyword"
            class="item-list__input"
            placeholder="标题 / 地点 / 描述"
            clearable
            @keyup.enter="handleSearch"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="loading" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-alert
      v-if="errorMessage"
      class="item-list__error"
      type="error"
      :closable="false"
      show-icon
      title="列表加载失败"
      :description="errorMessage"
    />

    <!-- 桌面端表格 -->
    <el-card class="sg-only-desktop" shadow="never">
      <el-skeleton v-if="loading && list.length === 0" :rows="6" animated />

      <el-table
        v-else-if="list.length > 0"
        :data="list"
        row-key="item_id"
        stripe
        @row-click="goDetail"
      >
        <el-table-column label="物品" min-width="260">
          <template #default="{ row }">
            <div class="item-list__cell">
              <el-image class="item-list__thumb" :src="row.cover_image_url || ''" fit="cover">
                <template #error>
                  <div class="item-list__thumb-fallback">
                    <el-icon><Picture /></el-icon>
                  </div>
                </template>
              </el-image>
              <div class="item-list__cell-text">
                <p class="item-list__cell-title">{{ row.title }}</p>
                <p class="sg-muted item-list__cell-desc">{{ row.description }}</p>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="类型" width="88">
          <template #default="{ row }">
            <el-tag :type="row.post_type === 'lost' ? 'danger' : 'success'" effect="plain" size="small">
              {{ row.post_type === 'lost' ? '失物' : '拾物' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="category" label="分类" width="100" />
        <el-table-column prop="location" label="地点" min-width="140" show-overflow-tooltip />
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.event_time) }}</template>
        </el-table-column>

        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <StatusTag :status="row.status" kind="item" size="small" />
          </template>
        </el-table-column>

        <el-table-column label="AI 分析" width="120">
          <template #default="{ row }">
            <StatusTag :status="row.ai_status" kind="ai" size="small" effect="plain" />
          </template>
        </el-table-column>

        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="goDetail(row)">详情</el-button>
            <el-button link type="primary" @click.stop="goMatches(row)">匹配</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <EmptyState
            description="暂无物品记录"
            sub-description="可以调整筛选条件，或先发布一条失物 / 拾物信息；若后端未启动，列表也会为空。"
            action-text="发布失物"
            size="small"
            @action="goCreateLost"
          />
        </template>
      </el-table>

      <el-pagination
        v-if="total > 0"
        class="item-list__pagination"
        background
        layout="total, sizes, prev, pager, next, jumper"
        :total="total"
        :current-page="query.page"
        :page-size="query.page_size"
        :page-sizes="[10, 20, 50]"
        :hide-on-single-page="false"
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
    </el-card>

    <!-- 窄屏卡片列表（纯 CSS 切换，避免重复请求） -->
    <div class="sg-only-mobile">
      <el-skeleton v-if="loading && list.length === 0" :rows="4" animated />

      <template v-else-if="list.length > 0">
        <el-card v-for="item in list" :key="item.item_id" class="item-list__card" shadow="never">
          <div class="item-list__card-head">
            <span class="item-list__cell-title">{{ item.title }}</span>
            <StatusTag :status="item.status" kind="item" size="small" />
          </div>
          <p class="sg-muted item-list__card-meta">
            {{ item.post_type === 'lost' ? '失物' : '拾物' }} · {{ item.category }} ·
            {{ formatDateTime(item.event_time) }}
          </p>
          <p class="sg-muted item-list__card-desc">{{ item.location }}｜{{ item.description }}</p>
          <div class="item-list__card-actions">
            <el-button size="small" @click="goDetail(item)">查看详情</el-button>
            <el-button size="small" type="primary" plain @click="goMatches(item)">匹配结果</el-button>
          </div>
        </el-card>

        <el-pagination
          class="item-list__pagination"
          background
          small
          layout="prev, pager, next"
          :total="total"
          :current-page="query.page"
          :page-size="query.page_size"
          @current-change="handlePageChange"
        />
      </template>

      <EmptyState
        v-else
        description="暂无物品记录"
        sub-description="可以调整筛选条件，或先发布一条失物 / 拾物信息。"
        action-text="发布失物"
        @action="goCreateLost"
      />
    </div>
  </div>
</template>

<style scoped>
.item-list__header-actions {
  display: flex;
  gap: var(--sg-gap-xs);
}

.item-list__filter {
  margin-bottom: var(--sg-gap);
}

.item-list__form :deep(.el-form-item) {
  margin-bottom: var(--sg-gap-xs);
}

.item-list__select {
  width: 150px;
}

.item-list__input {
  width: 220px;
}

.item-list__error {
  margin-bottom: var(--sg-gap-sm);
}

.item-list__cell {
  display: flex;
  align-items: center;
  gap: var(--sg-gap-xs);
}

.item-list__thumb {
  width: 48px;
  height: 48px;
  border-radius: var(--sg-radius-sm);
  flex: 0 0 auto;
  background: var(--sg-bg);
}

.item-list__thumb-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--sg-text-placeholder);
}

.item-list__cell-text {
  min-width: 0;
}

.item-list__cell-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--sg-text);
}

.item-list__cell-desc {
  margin: 2px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 320px;
}

.item-list__pagination {
  margin-top: var(--sg-gap);
  justify-content: flex-end;
}

.item-list__card {
  margin-bottom: var(--sg-gap-xs);
}

.item-list__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
}

.item-list__card-meta {
  margin: 6px 0 0;
}

.item-list__card-desc {
  margin: 4px 0 var(--sg-gap-xs);
}

.item-list__card-actions {
  display: flex;
  gap: var(--sg-gap-xs);
}

@media (max-width: 767px) {
  .item-list__header-actions {
    width: 100%;
  }

  .item-list__select,
  .item-list__input {
    width: 100%;
  }

  .item-list__form :deep(.el-form-item) {
    display: block;
    width: 100%;
  }

  .item-list__pagination {
    justify-content: center;
  }
}
</style>
