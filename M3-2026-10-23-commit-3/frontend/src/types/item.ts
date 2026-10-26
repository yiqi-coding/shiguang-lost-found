/**
 * 物品领域类型与业务文案常量。
 * 覆盖：发布类型、分类、状态、AI 分析状态、表单校验阈值、创建/查询入参。
 *
 * 权限要点：hidden_features 仅发布者与管理员可见，
 * 因此 ItemSummary（列表项）刻意不包含该字段，避免前端误用。
 */
import type { PageQuery } from './api'

/** 发布类型：失物 / 拾物 */
export type PostType = 'lost' | 'found'

/** 物品业务状态（契约 §2.3，状态机由后端集中实现） */
export type ItemStatus =
  | 'published'
  | 'matched'
  | 'claiming'
  | 'pending_review'
  | 'claimed'
  | 'rejected'
  | 'request_more_info'
  | 'suspended'
  | 'disputed'
  | 'closed'

/** AI 分析状态：pending 分析中 / success 已产出属性 / failed 失败（不阻塞发布） */
export type AiStatus = 'pending' | 'success' | 'failed'

/** 物品分类（固定枚举，后端按精确值匹配） */
export type ItemCategory = '无线耳机' | '水杯' | '书籍' | '校园卡' | '雨伞' | '背包' | '其他'

export const ITEM_CATEGORIES: readonly ItemCategory[] = [
  '无线耳机',
  '水杯',
  '书籍',
  '校园卡',
  '雨伞',
  '背包',
  '其他',
]

export const CATEGORY_OPTIONS: ReadonlyArray<{ label: ItemCategory; value: ItemCategory }> =
  ITEM_CATEGORIES.map((category) => ({ label: category, value: category }))

export const POST_TYPE_LABELS: Record<PostType, string> = {
  lost: '失物',
  found: '拾物',
}

export const POST_TYPE_OPTIONS: ReadonlyArray<{ label: string; value: PostType }> = [
  { label: POST_TYPE_LABELS.lost, value: 'lost' },
  { label: POST_TYPE_LABELS.found, value: 'found' },
]

/** 认领状态文案映射（业务规则要求的固定措辞，不要在页面里另写一套） */
export const ITEM_STATUS_LABELS: Record<ItemStatus, string> = {
  published: '已发布',
  matched: '已匹配',
  claiming: '认领中',
  pending_review: '待审核',
  claimed: '已认领',
  rejected: '已驳回',
  request_more_info: '待补充信息',
  suspended: '已暂停',
  disputed: '争议中',
  closed: '已关闭',
}

/** Element Plus el-tag 的 type 取值 */
export type TagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

export const ITEM_STATUS_TAG: Record<ItemStatus, TagType> = {
  published: 'info',
  matched: 'primary',
  claiming: 'warning',
  pending_review: 'warning',
  claimed: 'success',
  rejected: 'danger',
  request_more_info: 'warning',
  suspended: 'info',
  disputed: 'danger',
  closed: 'info',
}

export const AI_STATUS_LABELS: Record<AiStatus, string> = {
  pending: 'AI 分析中',
  success: 'AI 分析完成',
  failed: 'AI 分析失败',
}

export const AI_STATUS_TAG: Record<AiStatus, TagType> = {
  pending: 'warning',
  success: 'success',
  failed: 'danger',
}

/** 表单校验阈值（与后端校验保持一致，前端仅做前置提示） */
export const TITLE_MIN_LENGTH = 5
export const TITLE_MAX_LENGTH = 80
export const DESCRIPTION_MIN_LENGTH = 10
export const DESCRIPTION_MAX_LENGTH = 2000
export const HIDDEN_FEATURES_MIN_LENGTH = 2
export const HIDDEN_FEATURES_MAX_LENGTH = 500
export const PUBLIC_FEATURES_MAX_LENGTH = 500
export const LOCATION_MAX_LENGTH = 100
export const CONTACT_ID_MAX_LENGTH = 64

/** 图片规则：jpg/jpeg/png/webp，单张 ≤ 10MB，最多 3 张 */
export const MAX_IMAGE_COUNT = 3
export const MAX_IMAGE_SIZE_MB = 10
export const MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024
export const ACCEPTED_IMAGE_TYPES: readonly string[] = ['image/jpeg', 'image/png', 'image/webp']
export const ACCEPTED_IMAGE_EXT = '.jpg,.jpeg,.png,.webp'
export const ACCEPTED_IMAGE_MIME_TEXT = 'jpg / jpeg / png / webp'

/** 物品图片（后端只给签名 URL，image_key 仅发布者与管理员可见） */
export interface ItemImage {
  image_key: string
  url?: string | null
  is_primary?: boolean
}

/** AI 属性提取结果（契约 §3.5 / §3.14） */
export interface AiAttributes {
  category?: string | null
  color?: string | null
  visible_features?: string[]
  keywords?: string[]
  uncertainties?: string[]
}

/**
 * 列表项：注意不含 hidden_features（契约 §3.4 红线：列表必须剔除隐藏特征与原始对象 Key）。
 *
 * 字段说明：后端主键在列表/详情中名为 `id`，为兼容「item_id」写法，
 * 归一化函数 normalizeItemSummary 会把两者统一成 `item_id`，页面只使用 `item_id`。
 */
export interface ItemSummary {
  item_id: number
  post_type: PostType
  category: ItemCategory | string
  title: string
  description: string
  color?: string | null
  brand?: string | null
  location: string
  /** ISO 8601 带时区：失物的丢失时间 / 拾物的拾获时间 */
  event_time: string
  /** 公开特征：后端为 JSON 数组，归一化后以换行拼接的字符串展示 */
  public_features?: string | null
  status: ItemStatus
  ai_status: AiStatus
  owner_user_id?: number | null
  /** 列表缩略图（后端字段 thumbnail_url） */
  thumbnail_url?: string | null
  /** 页面统一使用的封面图字段（由 thumbnail_url / images 归一化而来） */
  cover_image_url?: string | null
  created_at: string
  updated_at?: string | null
}

/** 详情：按角色脱敏（契约 §3.5 的字段可见性表格） */
export interface ItemDetail extends ItemSummary {
  images?: ItemImage[]
  /** 仅发布者与管理员可见；其他角色后端返回 null，归一化后为 null */
  hidden_features?: string | null
  /** 当前账号是否可读隐藏特征（后端未显式返回时由 hidden_features 是否有值推断） */
  can_view_hidden?: boolean
  /** 脱敏后的联系方式（后端字段 contact_id，例如 138****8888） */
  contact_masked?: string | null
  ai_attributes?: AiAttributes | null
}

/** 发布物品入参（POST /items，multipart/form-data） */
export interface ItemCreatePayload {
  post_type: PostType
  category: ItemCategory
  title: string
  description: string
  color?: string
  brand?: string
  location: string
  /** ISO 8601 字符串，由日期选择器转换 */
  event_time: string
  public_features?: string
  /** 必填：仅发布者与管理员可见，用于认领验证提问 */
  hidden_features: string
  /** 联系方式标识（学号 / 手机号 / 微信号），后端按脱敏规则存储与展示 */
  contact_id: string
  images: File[]
}

/** 发布结果（契约 §3.3：id / status / ai_status / images / ai_attributes） */
export interface ItemCreateResult {
  item_id: number
  status: ItemStatus
  image_keys: string[]
  ai_status: AiStatus
  /** AI 提取到的属性；ai_status=failed 时为 null，此时以用户手填属性为准 */
  ai_attributes?: AiAttributes | null
}

/** 列表查询参数（GET /items） */
export interface ItemListQuery extends PageQuery {
  post_type?: PostType | ''
  category?: ItemCategory | ''
  status?: ItemStatus | ''
  keyword?: string
}
