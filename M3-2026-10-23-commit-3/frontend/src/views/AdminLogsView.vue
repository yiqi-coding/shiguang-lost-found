<script setup lang="ts">
/**
 * 管理员日志 AdminLogsView —— 路由 /admin/logs
 * 职责：按 target_type（item / claim / user）与 target_id 查询审计日志，分页展示操作时间、动作、
 *       操作者、request_id，并支持查看脱敏后的详情 JSON。
 * 依赖接口：GET /admin/audit-logs?target_type=&target_id=&page=&page_size=
 * 支持 /admin/logs?target_type=claim&target_id=4001 直达（审核台「查看全部日志」入口会携带参数）。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { fetchAuditLogs } from '@/api/admin'
import EmptyState from '@/components/EmptyState.vue'
import PrivacyHint from '@/components/PrivacyHint.vue'
import type { AuditLog, AuditLogQuery, AuditTargetType } from '@/types/user'
import { AUDIT_TARGET_TYPE_LABELS, AUDIT_TARGET_TYPE_OPTIONS } from '@/types/user'
import { DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE } from '@/types/api'
import { formatDateTime, safeText, stringifyDetail } from '@/utils/format'

const route = useRoute()

const query = reactive<AuditLogQuery>({
  target_type: '',
  target_id: null,
  page: 1,
  page_size: DEFAULT_PAGE_SIZE,
})

const logs = ref<AuditLog[]>([])
const total = ref(0)
const loading = ref(false)
const errorMessage = ref('')

const detailVisible = ref(false)
const currentLog = ref<AuditLog | null>(null)

async function loadLogs(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await fetchAuditLogs({ ...query })
    logs.value = result.items
    total.value = result.total
    query.page = result.page
    query.page_size = result.page_size
  } catch (error) {
    logs.value = []
    total.value = 0
    errorMessage.value = error instanceof Error ? error.message : '审计日志加载失败'
  } finally {
    loading.value = false
  }
}

/** 从 URL 读取初始查询条件（审核台跳转时携带） */
function applyRouteQuery(): void {
  const { target_type, target_id } = route.query
  if (target_type === 'item' || target_type === 'claim' || target_type === 'user') {
    query.target_type = target_type as AuditTargetType
  }
  if (typeof target_id === 'string' && Number(target_id) > 0) {
    query.target_id = Math.floor(Number(target_id))
  }
}

function handleSearch(): void {
  query.page = 1
  void loadLogs()
}

function handleReset(): void {
  query.target_type = ''
  query.target_id = null
  query.page = 1
  query.page_size = DEFAULT_PAGE_SIZE
  void loadLogs()
}

function handlePage(page: number): void {
  query.page = page
  void loadLogs()
}

function handleSize(size: number): void {
  query.page_size = Math.min(size, MAX_PAGE_SIZE)
  query.page = 1
  void loadLogs()
}

function openDetail(log: AuditLog): void {
  currentLog.value = log
  detailVisible.value = true
}

function targetLabel(log: AuditLog): string {
  return `${AUDIT_TARGET_TYPE_LABELS[log.target_type] ?? log.target_type} #${log.target_id}`
}

/** 动作标签颜色：创建类 info、通过类 success、驳回类 danger、其余 warning */
function actionTagType(action: string): 'success' | 'danger' | 'warning' | 'info' {
  const value = action.toLowerCase()
  if (value.includes('approve') || value.includes('success') || value.includes('create')) return 'success'
  if (value.includes('reject') || value.includes('fail') || value.includes('delete')) return 'danger'
  if (value.includes('review') || value.includes('verify') || value.includes('request')) return 'warning'
  return 'info'
}

onMounted(() => {
  applyRouteQuery()
  void loadLogs()
})
</script>

<template>
  <div class="logs sg-page">
    <div class="sg-page__header">
      <div>
        <h1 class="sg-page__title">管理员日志</h1>
        <p class="sg-page__subtitle">
          按目标对象查询审计日志，共 {{ total }} 条；日志中的联系方式与隐藏特征均已脱敏。
        </p>
      </div>
      <div class="logs__actions">
        <el-button :loading="loading" @click="loadLogs">
          <el-icon><Refresh /></el-icon>
          <span>刷新</span>
        </el-button>
      </div>
    </div>

    <PrivacyHint
      class="logs__privacy"
      title="日志使用规范"
      level="info"
      compact
      :items="[
        '日志用于审核追溯与问题排查，禁止用于与业务无关的用途。',
        'detail 字段由后端脱敏后返回，前端不做二次加工与导出。',
      ]"
    />

    <el-card class="logs__filter" shadow="never">
      <el-form :inline="true" class="logs__form" @submit.prevent>
        <el-form-item label="目标类型">
          <el-select v-model="query.target_type" class="logs__select" placeholder="全部">
            <el-option label="全部" value="" />
            <el-option
              v-for="option in AUDIT_TARGET_TYPE_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="目标 ID">
          <el-input-number
            v-model="query.target_id"
            class="logs__number"
            :min="1"
            :step="1"
            :controls="false"
            placeholder="例如 4001"
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
      class="logs__error"
      type="error"
      :closable="false"
      show-icon
      title="日志加载失败"
      :description="errorMessage"
    />

    <el-card shadow="never">
      <el-skeleton v-if="loading && logs.length === 0" :rows="6" animated />

      <el-table v-else-if="logs.length > 0" :data="logs" row-key="id" stripe>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="目标对象" min-width="150">
          <template #default="{ row }">{{ targetLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="动作" min-width="160">
          <template #default="{ row }">
            <el-tag :type="actionTagType(row.action)" size="small" effect="light">
              {{ row.action }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作者" width="150">
          <template #default="{ row }">{{ safeText(row.operator_username, '系统') }}</template>
        </el-table-column>
        <el-table-column label="request_id" min-width="220">
          <template #default="{ row }">
            <span class="logs__request-id">{{ safeText(row.request_id) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="96" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <EmptyState
            description="暂无审计日志"
            sub-description="调整目标类型与目标 ID 后重试；若后端未实现 /admin/audit-logs，这里会一直为空。"
            size="small"
          />
        </template>
      </el-table>

      <el-pagination
        v-if="total > 0"
        class="logs__pagination"
        background
        layout="total, sizes, prev, pager, next, jumper"
        :total="total"
        :current-page="query.page"
        :page-size="query.page_size"
        :page-sizes="[10, 20, 50]"
        @current-change="handlePage"
        @size-change="handleSize"
      />
    </el-card>

    <!-- 日志详情：只读展示后端已脱敏的 detail -->
    <el-drawer v-model="detailVisible" title="日志详情" size="480px">
      <template v-if="currentLog">
        <div class="logs__detail-row">
          <span class="sg-muted">时间</span>
          <span>{{ formatDateTime(currentLog.created_at) }}</span>
        </div>
        <div class="logs__detail-row">
          <span class="sg-muted">目标对象</span>
          <span>{{ targetLabel(currentLog) }}</span>
        </div>
        <div class="logs__detail-row">
          <span class="sg-muted">动作</span>
          <span>{{ currentLog.action }}</span>
        </div>
        <div class="logs__detail-row">
          <span class="sg-muted">操作者</span>
          <span>
            {{ safeText(currentLog.operator_username, '系统') }}（ID
            {{ safeText(currentLog.operator_id, '—') }}）
          </span>
        </div>
        <div class="logs__detail-row">
          <span class="sg-muted">request_id</span>
          <span class="logs__request-id">{{ safeText(currentLog.request_id) }}</span>
        </div>

        <h3 class="logs__detail-title">详情（后端脱敏）</h3>
        <pre class="logs__detail-json">{{ stringifyDetail(currentLog.detail) }}</pre>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.logs__actions {
  display: flex;
  gap: var(--sg-gap-xs);
}

.logs__privacy {
  margin-bottom: var(--sg-gap-sm);
}

.logs__filter {
  margin-bottom: var(--sg-gap-sm);
}

.logs__form :deep(.el-form-item) {
  margin-bottom: var(--sg-gap-xs);
}

.logs__select {
  width: 150px;
}

.logs__number {
  width: 160px;
}

.logs__error {
  margin-bottom: var(--sg-gap-sm);
}

.logs__request-id {
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 12px;
  word-break: break-all;
}

.logs__pagination {
  margin-top: var(--sg-gap);
  justify-content: flex-end;
}

.logs__detail-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
  padding: 6px 0;
  font-size: 14px;
  border-bottom: 1px dashed var(--sg-border-light);
}

.logs__detail-title {
  margin: var(--sg-gap) 0 6px;
  font-size: 14px;
}

.logs__detail-json {
  margin: 0;
  padding: var(--sg-gap-xs);
  background: var(--sg-bg);
  border-radius: var(--sg-radius-sm);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

@media (max-width: 767px) {
  .logs__select,
  .logs__number {
    width: 100%;
  }

  .logs__form :deep(.el-form-item) {
    display: block;
    width: 100%;
  }

  .logs__pagination {
    justify-content: center;
  }
}
</style>
