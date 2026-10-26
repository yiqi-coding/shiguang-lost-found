<script setup lang="ts">
/**
 * 物品详情 ItemDetailView —— 路由 /items/:id
 * 职责：展示物品公开信息与图片；按后端返回的 can_view_hidden 决定是否展示隐藏特征（仅发布者与管理员可见）；
 *       提供「查看匹配结果 / 重新计算匹配 / 发起认领」入口。
 * 依赖接口：GET /items/{item_id}、POST /items/{item_id}/match（重算，需鉴权）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getItem } from '@/api/items'
import { recalculateMatches } from '@/api/matches'
import { ApiError } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import PrivacyHint from '@/components/PrivacyHint.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useUserStore } from '@/stores/user'
import type { ItemDetail } from '@/types/item'
import { POST_TYPE_LABELS } from '@/types/item'
import { DEFAULT_TOP_K, MATCH_DISCLAIMER_TEXT } from '@/types/match'
import { formatDateTime, safeText } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const itemId = computed(() => Number(route.params.id))
const item = ref<ItemDetail | null>(null)
const loading = ref(false)
const recalculating = ref(false)
const errorMessage = ref('')
const notFound = ref(false)

/** 图片列表：优先多图，缺失时回退封面 */
const gallery = computed<string[]>(() => {
  const urls = (item.value?.images ?? [])
    .map((image) => image.url)
    .filter((url): url is string => Boolean(url))
  if (urls.length === 0 && item.value?.cover_image_url) return [item.value.cover_image_url]
  return urls
})

const postTypeLabel = computed(() =>
  item.value ? POST_TYPE_LABELS[item.value.post_type] : '物品',
)

const canViewHidden = computed(() => Boolean(item.value?.can_view_hidden))

async function loadItem(): Promise<void> {
  if (!Number.isFinite(itemId.value) || itemId.value <= 0) {
    notFound.value = true
    return
  }

  loading.value = true
  errorMessage.value = ''
  notFound.value = false
  try {
    item.value = await getItem(itemId.value)
  } catch (error) {
    item.value = null
    if (error instanceof ApiError && error.code === 'ITEM_NOT_FOUND') {
      notFound.value = true
    } else {
      errorMessage.value = error instanceof Error ? error.message : '物品详情加载失败'
    }
  } finally {
    loading.value = false
  }
}

/** 重新计算匹配（需登录），成功后跳转匹配结果页 */
async function handleRecalculate(): Promise<void> {
  if (!userStore.ensureLogin('重新计算匹配需要登录后进行')) return
  recalculating.value = true
  try {
    await recalculateMatches(itemId.value, DEFAULT_TOP_K)
    ElMessage.success('匹配已重新计算')
    void router.push({ name: 'item-matches', params: { id: itemId.value } })
  } catch {
    // 错误提示由 axios 拦截器统一处理（如 ALGORITHM_SERVICE_ERROR）
  } finally {
    recalculating.value = false
  }
}

function goMatches(): void {
  void router.push({ name: 'item-matches', params: { id: itemId.value } })
}

function goList(): void {
  void router.push({ name: 'item-list' })
}

// 详情页可能被连续切换到另一个物品，这里监听参数变化重新加载
watch(
  () => route.params.id,
  () => {
    void loadItem()
  },
)

onMounted(() => {
  void loadItem()
})
</script>

<template>
  <div class="detail sg-page">
    <el-skeleton v-if="loading" :rows="8" animated />

    <EmptyState
      v-else-if="notFound"
      description="物品记录不存在"
      sub-description="该物品可能已被删除或链接有误，请返回列表重新查找。"
      action-text="返回物品列表"
      @action="goList"
    />

    <EmptyState
      v-else-if="errorMessage"
      description="物品详情加载失败"
      :sub-description="errorMessage"
      action-text="重新加载"
      @action="loadItem"
    />

    <template v-else-if="item">
      <div class="sg-page__header">
        <div>
          <h1 class="sg-page__title">{{ item.title }}</h1>
          <div class="detail__meta">
            <el-tag :type="item.post_type === 'lost' ? 'danger' : 'success'" effect="plain" size="small">
              {{ postTypeLabel }}
            </el-tag>
            <el-tag type="info" effect="plain" size="small">{{ item.category }}</el-tag>
            <StatusTag :status="item.status" kind="item" size="small" />
            <StatusTag :status="item.ai_status" kind="ai" size="small" effect="plain" />
          </div>
        </div>
        <div class="detail__header-actions">
          <el-button @click="goList">返回列表</el-button>
          <el-button :loading="recalculating" @click="handleRecalculate">重新计算匹配</el-button>
          <el-button type="primary" @click="goMatches">查看匹配结果</el-button>
        </div>
      </div>

      <el-alert
        v-if="item.ai_status === 'failed'"
        class="detail__alert"
        type="warning"
        :closable="false"
        show-icon
        title="AI 分析失败"
        description="该物品的 AI 分析未成功，属性以发布者填写内容为准；发布与匹配流程不受影响，可在匹配结果页重新计算。"
      />
      <el-alert
        v-else-if="item.ai_status === 'pending'"
        class="detail__alert"
        type="info"
        :closable="false"
        show-icon
        title="AI 分析进行中"
        description="AI 正在提取图片与描述特征，稍后刷新页面即可看到补充的属性标签。"
      />

      <el-row :gutter="16">
        <el-col :xs="24" :md="14">
          <el-card class="detail__card" shadow="never">
            <h2 class="sg-section-title">物品信息</h2>

            <div v-if="gallery.length > 0" class="detail__gallery">
              <el-image
                v-for="(url, index) in gallery"
                :key="url"
                class="detail__image"
                :src="url"
                fit="cover"
                :preview-src-list="gallery"
                :initial-index="index"
                preview-teleported
              />
            </div>
            <el-alert
              v-else
              class="detail__no-image"
              type="info"
              :closable="false"
              show-icon
              title="该物品未上传图片"
              description="图像相似度维度将不计分，建议以时间、地点与文字描述为主要判断依据。"
            />

            <dl class="detail__grid">
              <div class="detail__grid-item">
                <dt>{{ item.post_type === 'lost' ? '丢失时间' : '拾获时间' }}</dt>
                <dd>{{ formatDateTime(item.event_time) }}</dd>
              </div>
              <div class="detail__grid-item">
                <dt>{{ item.post_type === 'lost' ? '丢失地点' : '拾获地点' }}</dt>
                <dd>{{ safeText(item.location) }}</dd>
              </div>
              <div class="detail__grid-item">
                <dt>颜色</dt>
                <dd>{{ safeText(item.color) }}</dd>
              </div>
              <div class="detail__grid-item">
                <dt>品牌 / 型号</dt>
                <dd>{{ safeText(item.brand) }}</dd>
              </div>
              <div class="detail__grid-item">
                <dt>发布时间</dt>
                <dd>{{ formatDateTime(item.created_at) }}</dd>
              </div>
              <div class="detail__grid-item">
                <dt>发布者</dt>
                <dd>
                  {{ item.owner_user_id ? `用户 #${item.owner_user_id}` : '匿名用户' }}
                  <span class="sg-muted">（昵称与联系方式按角色脱敏）</span>
                </dd>
              </div>
            </dl>

            <h3 class="detail__sub-title">详细描述</h3>
            <p class="sg-pre-wrap detail__text">{{ safeText(item.description) }}</p>

            <h3 class="detail__sub-title">公开特征</h3>
            <p class="sg-pre-wrap detail__text">{{ safeText(item.public_features, '发布者未填写公开特征') }}</p>

            <!-- AI 提取属性：ai_status=failed 时为 null，此时以发布者手填内容为准 -->
            <template v-if="item.ai_attributes">
              <h3 class="detail__sub-title">AI 提取属性</h3>
              <div class="detail__ai-tags">
                <el-tag v-if="item.ai_attributes.color" size="small" effect="plain">
                  颜色：{{ item.ai_attributes.color }}
                </el-tag>
                <el-tag v-if="item.ai_attributes.category" size="small" effect="plain">
                  类别：{{ item.ai_attributes.category }}
                </el-tag>
                <el-tag
                  v-for="keyword in item.ai_attributes.keywords ?? []"
                  :key="`kw-${keyword}`"
                  size="small"
                  type="info"
                  effect="plain"
                >
                  {{ keyword }}
                </el-tag>
              </div>
              <ul v-if="(item.ai_attributes.uncertainties ?? []).length > 0" class="detail__ai-list">
                <li v-for="text in item.ai_attributes.uncertainties ?? []" :key="text">{{ text }}</li>
              </ul>
            </template>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="10">
          <!-- 隐藏特征：仅发布者与管理员可见，是否可见由后端 can_view_hidden 决定 -->
          <el-card class="detail__card" shadow="never">
            <h2 class="sg-section-title">隐藏特征</h2>

            <template v-if="canViewHidden">
              <PrivacyHint
                title="仅你与管理员可见"
                level="warning"
                compact
                :items="[
                  '该内容不会出现在物品列表、公开详情与匹配依据中。',
                  '认领申请人只能看到由它生成的验证问题，看不到原文。',
                ]"
              />
              <p class="sg-pre-wrap detail__text">{{ safeText(item.hidden_features, '未填写隐藏特征') }}</p>
            </template>

            <template v-else>
              <PrivacyHint
                title="隐藏特征不可见"
                level="info"
                :items="[
                  '隐藏特征仅发布者本人与平台管理员可见，当前账号无权查看。',
                  '若你是失主，请通过「匹配结果 → 发起认领」回答验证问题，由系统与管理员核验。',
                ]"
              />
            </template>
          </el-card>

          <el-card class="detail__card" shadow="never">
            <h2 class="sg-section-title">认领与联系方式</h2>
            <div class="detail__row">
              <span class="sg-muted">联系方式</span>
              <span>
                {{ safeText(item.contact_masked, '仅管理员可见') }}
                <el-tag size="small" type="info" effect="plain">脱敏</el-tag>
              </span>
            </div>
            <el-alert
              class="detail__disclaimer"
              type="warning"
              :closable="false"
              show-icon
              :title="MATCH_DISCLAIMER_TEXT"
              description="认领必须基于某条候选匹配发起，并回答由隐藏特征生成的验证问题；最终由管理员审核确认交接。"
            />
            <el-button class="detail__claim-button" type="primary" @click="goMatches">
              前往匹配结果发起认领
            </el-button>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<style scoped>
.detail__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: var(--sg-gap-xs);
}

.detail__header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
}

.detail__alert {
  margin-bottom: var(--sg-gap-sm);
  align-items: flex-start;
}

.detail__card {
  margin-bottom: var(--sg-gap);
}

.detail__gallery {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
  margin-bottom: var(--sg-gap-sm);
}

.detail__image {
  width: 132px;
  height: 132px;
  border-radius: var(--sg-radius-sm);
  background: var(--sg-bg);
}

.detail__no-image {
  margin-bottom: var(--sg-gap-sm);
  align-items: flex-start;
}

.detail__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sg-gap-xs) var(--sg-gap);
  margin: 0;
}

.detail__grid-item dt {
  font-size: 12px;
  color: var(--sg-text-secondary);
}

.detail__grid-item dd {
  margin: 2px 0 0;
  font-size: 14px;
  word-break: break-word;
}

.detail__sub-title {
  margin: var(--sg-gap) 0 6px;
  font-size: 14px;
  font-weight: 600;
}

.detail__text {
  margin: 0;
  font-size: 14px;
}

.detail__ai-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.detail__ai-list {
  margin: var(--sg-gap-xs) 0 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--sg-text-secondary);
}

.detail__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
  font-size: 14px;
}

.detail__disclaimer {
  margin: var(--sg-gap-sm) 0;
  align-items: flex-start;
}

.detail__claim-button {
  width: 100%;
}

@media (max-width: 767px) {
  .detail__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .detail__header-actions {
    width: 100%;
  }

  .detail__header-actions .el-button {
    flex: 1 1 auto;
    margin-left: 0;
  }
}
</style>
