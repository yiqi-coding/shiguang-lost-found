<script setup lang="ts">
/**
 * 首页 HomeView —— 路由 /
 * 职责：介绍项目定位与核心能力、说明「发布 → 匹配 → 隐藏特征验证 → 管理员审核」流程、
 *       展示后端依赖健康状态，并固定展示平台免责声明。
 * 依赖接口：GET /health（silent 调用；契约 §3.1 返回 app/database/redis/obs/算法服务/MaaS 状态，
 *           失败时降级尝试根路径 /health，均失败只提示后端未启动，不阻塞首页展示）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchHealth, fetchRootHealth } from '@/api/health'
import PrivacyHint from '@/components/PrivacyHint.vue'
import type { HealthComponent, HealthState, HealthStatus } from '@/types/api'
import { HEALTH_STATE_LABELS, OWNERSHIP_DISCLAIMER } from '@/types/api'
import { MATCH_ALGO_NOTE, MATCH_DISCLAIMER_TEXT } from '@/types/match'

const router = useRouter()

/** ---------- 静态内容 ---------- */
interface FeatureItem {
  title: string
  description: string
}

const FEATURES: FeatureItem[] = [
  {
    title: '多模态检索',
    description: '图文、时间、地点、品牌型号多维度加权，快速找出可能属于你的物品。',
  },
  {
    title: '隐藏特征验证',
    description: '发布者填写仅本人与管理员可见的细节，系统据此生成 2~3 个验证问题。',
  },
  {
    title: '可信认领流程',
    description: '匹配 → 验证 → 管理员审核 → 线下交接，每一步都留痕并可追溯。',
  },
  {
    title: '隐私最小必要',
    description: '联系方式脱敏展示，隐藏特征不进入列表、公开详情与匹配依据。',
  },
]

interface StepItem {
  title: string
  description: string
}

const STEPS: StepItem[] = [
  { title: '发布信息', description: '失主发布失物、拾得者发布拾物，填写描述、时间地点与隐藏特征。' },
  { title: '候选匹配', description: '系统计算六个维度分数并给出匹配依据、不确定因素与风险提示。' },
  { title: '认领验证', description: '申请人回答基于隐藏特征生成的验证问题，系统给出可信度与风险等级。' },
  { title: '人工审核', description: '管理员综合匹配依据与验证结果作出结论，双方线下交接并登记。' },
]

/** ---------- 健康状态 ---------- */
const health = ref<HealthStatus | null>(null)
const healthLoading = ref(false)
const healthError = ref('')

async function loadHealth(): Promise<void> {
  healthLoading.value = true
  healthError.value = ''
  try {
    // 契约附录 A 把健康检查列为 GET /health（根路径），因此先试根路径，再退回 /api/v1/health
    health.value = await fetchRootHealth()
  } catch {
    try {
      health.value = await fetchHealth()
    } catch (error) {
      health.value = null
      healthError.value = error instanceof Error ? error.message : '健康检查失败'
    }
  } finally {
    healthLoading.value = false
  }
}

const healthState = computed<HealthState | 'unknown'>(() => health.value?.status ?? 'unknown')

const healthTagType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  const state = healthState.value
  if (state === 'ok') return 'success'
  if (state === 'degraded' || state === 'not_configured') return 'warning'
  if (state === 'down') return 'danger'
  return 'info'
})

const healthLabel = computed(() => {
  const state = healthState.value
  return state === 'unknown' ? '未检测' : HEALTH_STATE_LABELS[state]
})

function componentLabel(component: HealthComponent): string {
  const latency = typeof component.latency_ms === 'number' ? ` ${component.latency_ms}ms` : ''
  return `${component.label}：${HEALTH_STATE_LABELS[component.status]}${latency}`
}

function componentTagType(component: HealthComponent): 'success' | 'warning' | 'danger' | 'info' {
  if (component.status === 'ok') return 'success'
  if (component.status === 'down') return 'danger'
  if (component.status === 'degraded') return 'warning'
  // not_configured：未配置不等于故障，用中性色展示
  return 'info'
}

/** ---------- 导航 ---------- */
function go(path: string): void {
  void router.push(path)
}

onMounted(() => {
  void loadHealth()
})
</script>

<template>
  <div class="home sg-page">
    <!-- 首屏 -->
    <section class="home__hero">
      <div class="home__hero-text">
        <p class="home__badge">华为云 AI 创新应用竞赛作品</p>
        <h1 class="home__title">拾光</h1>
        <p class="home__subtitle">校园多模态失物检索与可信认领平台</p>
        <p class="home__desc">
          把「丢了东西」变成一次可核验的匹配：图文、时间、地点多模态计算候选匹配，
          再用只有失主才说得出的隐藏特征完成验证，最后由管理员确认交接。
        </p>
        <div class="home__actions">
          <el-button type="primary" size="large" @click="go('/items/new/lost')">
            发布失物
          </el-button>
          <el-button size="large" @click="go('/items/new/found')">发布拾物</el-button>
          <el-button size="large" text type="primary" @click="go('/items')">
            浏览物品列表
          </el-button>
        </div>
      </div>

      <el-card class="home__health" shadow="never">
        <template #header>
          <div class="home__health-header">
            <span>服务状态</span>
            <el-tag :type="healthTagType" size="small" effect="light">{{ healthLabel }}</el-tag>
          </div>
        </template>

        <el-skeleton v-if="healthLoading" :rows="3" animated />

        <template v-else>
          <el-alert
            v-if="healthError"
            type="warning"
            :closable="false"
            show-icon
            title="未能连接后端服务"
            :description="`${healthError}。页面可正常浏览，数据将在后端启动后加载。`"
          />
          <template v-else-if="health">
            <p class="sg-muted">接口前缀：/api/v1（可通过 VITE_API_BASE_URL 配置）</p>
            <p v-if="health.version" class="sg-muted">
              服务版本：{{ health.version }}
              <span v-if="health.env"> · 环境：{{ health.env }}</span>
            </p>
            <div class="home__health-tags">
              <el-tag
                v-for="component in health.components"
                :key="component.name"
                :type="componentTagType(component)"
                effect="plain"
                size="small"
              >
                {{ componentLabel(component) }}
              </el-tag>
              <span v-if="health.components.length === 0" class="sg-muted">
                后端未返回依赖明细
              </span>
            </div>
          </template>
          <el-button text type="primary" :loading="healthLoading" @click="loadHealth">
            重新检测
          </el-button>
        </template>
      </el-card>
    </section>

    <!-- 核心能力 -->
    <section class="home__section">
      <h2 class="sg-section-title">核心能力</h2>
      <el-row :gutter="16">
        <el-col v-for="feature in FEATURES" :key="feature.title" :xs="24" :sm="12" :md="6">
          <el-card class="home__feature" shadow="hover">
            <h3 class="home__feature-title">{{ feature.title }}</h3>
            <p class="sg-muted">{{ feature.description }}</p>
          </el-card>
        </el-col>
      </el-row>
    </section>

    <!-- 认领流程 -->
    <section class="home__section">
      <h2 class="sg-section-title">认领流程</h2>
      <el-steps class="home__steps sg-only-desktop" :active="4" align-center finish-status="success">
        <el-step v-for="step in STEPS" :key="step.title" :title="step.title" :description="step.description" />
      </el-steps>

      <div class="sg-only-mobile">
        <el-card v-for="(step, index) in STEPS" :key="step.title" class="home__step-card" shadow="never">
          <strong>第 {{ index + 1 }} 步 · {{ step.title }}</strong>
          <p class="sg-muted">{{ step.description }}</p>
        </el-card>
      </div>
    </section>

    <!-- 匹配机制与合规说明 -->
    <section class="home__section">
      <h2 class="sg-section-title">关于匹配结果</h2>
      <el-alert type="warning" :closable="false" show-icon :title="MATCH_DISCLAIMER_TEXT">
        <p class="home__alert-text">{{ MATCH_ALGO_NOTE }}</p>
      </el-alert>

      <PrivacyHint class="home__privacy" title="隐私与数据使用" level="info">
        <ul class="home__list">
          <li>隐藏特征仅发布者与管理员可见，不进入列表、公开详情与匹配依据。</li>
          <li>联系方式在审核台以脱敏形式展示，仅用于认领核验与线下交接。</li>
          <li>图片仅用于匹配计算与人工核验，不用于任何商业用途。</li>
        </ul>
      </PrivacyHint>

      <el-alert type="error" :closable="false" show-icon title="免责声明">
        <p class="home__alert-text">{{ OWNERSHIP_DISCLAIMER }}</p>
        <p class="home__alert-text">
          请勿在平台填写身份证号、银行卡号等与物品无关的敏感信息；线下交接时请注意人身与财产安全。
        </p>
      </el-alert>
    </section>
  </div>
</template>

<style scoped>
.home__hero {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr);
  gap: var(--sg-gap-lg);
  align-items: start;
  padding: var(--sg-gap-lg);
  background: linear-gradient(135deg, #eef4ff 0%, #ffffff 60%);
  border: 1px solid var(--sg-border-light);
  border-radius: var(--sg-radius-lg);
}

.home__badge {
  display: inline-block;
  margin: 0 0 var(--sg-gap-xs);
  padding: 2px 10px;
  font-size: 12px;
  color: var(--sg-primary);
  background: var(--sg-primary-light);
  border-radius: 999px;
}

.home__title {
  margin: 0;
  font-size: 40px;
  line-height: 1.1;
  letter-spacing: 4px;
}

.home__subtitle {
  margin: 6px 0 var(--sg-gap-sm);
  font-size: 17px;
  font-weight: 600;
  color: var(--sg-primary);
}

.home__desc {
  margin: 0;
  max-width: 620px;
  font-size: 14px;
  line-height: 1.9;
  color: var(--sg-text-secondary);
}

.home__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sg-gap-xs);
  margin-top: var(--sg-gap);
}

.home__health-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sg-gap-xs);
}

.home__health-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: var(--sg-gap-xs) 0;
}

.home__section {
  margin-top: var(--sg-gap-lg);
}

.home__feature {
  height: 100%;
  margin-bottom: var(--sg-gap-sm);
}

.home__feature-title {
  margin: 0 0 6px;
  font-size: 15px;
}

.home__steps {
  padding: var(--sg-gap) 0;
}

.home__step-card {
  margin-bottom: var(--sg-gap-xs);
}

.home__alert-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.75;
}

.home__alert-text + .home__alert-text {
  margin-top: 4px;
}

.home__privacy {
  margin-top: var(--sg-gap-sm);
}

.home__list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
}

@media (max-width: 991px) {
  .home__hero {
    grid-template-columns: minmax(0, 1fr);
    padding: var(--sg-gap);
  }

  .home__title {
    font-size: 32px;
  }
}
</style>
