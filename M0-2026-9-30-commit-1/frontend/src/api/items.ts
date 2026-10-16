/**
 * 物品相关接口（契约 §3.3 - §3.5、§3.10）：
 *   POST /items             发布物品（multipart/form-data，image 字段最多 3 张）
 *   GET  /items             列表（post_type / category / status / keyword + page / page_size）
 *   GET  /items/{item_id}   详情（按角色脱敏）
 *   GET  /me/items          我的发布记录
 *
 * 本文件同时承担「后端字段 → 前端模型」的归一化职责：
 * - 主键 id → item_id；thumbnail_url → cover_image_url；
 * - public_features / hidden_features 后端为 JSON 数组，前端统一转成换行文本展示；
 * - 列表与详情都不会返回其他用户的 hidden_features（契约 §3.4 红线）。
 */
import { createIdempotencyKey, httpGet, httpPostForm } from './http'
import type { PageQuery, PageResult } from '@/types/api'
import { clampPage, clampPageSize, toPageResult } from '@/types/api'
import type {
  AiAttributes,
  AiStatus,
  ItemCreatePayload,
  ItemCreateResult,
  ItemDetail,
  ItemImage,
  ItemListQuery,
  ItemStatus,
  ItemSummary,
  PostType,
} from '@/types/item'
import { MAX_IMAGE_COUNT } from '@/types/item'

/** 把后端可能返回的字符串 / JSON 字符串 / 数组统一成展示文本（多行用 \n 拼接） */
export function toDisplayText(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (Array.isArray(value)) {
    const parts = value
      .map((item) => (typeof item === 'string' ? item.trim() : JSON.stringify(item)))
      .filter((item) => Boolean(item))
    return parts.length > 0 ? parts.join('\n') : null
  }
  if (typeof value === 'string') {
    const text = value.trim()
    if (!text) return null
    // 后端字段类型为 string(JSON)，可能直接给出 "[\"a\",\"b\"]"
    if (text.startsWith('[') || text.startsWith('{')) {
      try {
        return toDisplayText(JSON.parse(text))
      } catch {
        return text
      }
    }
    return text
  }
  return String(value)
}

/** 表单里的多行文本 → 后端要求的 JSON 字符串数组（hidden_features / public_features） */
export function toJsonArrayText(value: string): string {
  const lines = value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  return JSON.stringify(lines.length > 0 ? lines : [value.trim()])
}

/** 列表项归一化 */
export function normalizeItemSummary(raw: unknown): ItemSummary {
  const source = (raw ?? {}) as Record<string, unknown>
  const rawImages = Array.isArray(source.images) ? (source.images as Array<Record<string, unknown>>) : []
  const primaryImage = rawImages.find((image) => image.is_primary === true) ?? rawImages[0]
  const thumbnail = typeof source.thumbnail_url === 'string' ? source.thumbnail_url : null
  const primaryUrl = primaryImage && typeof primaryImage.url === 'string' ? primaryImage.url : null

  return {
    item_id: Number(source.item_id ?? source.id ?? 0),
    post_type: (source.post_type === 'found' ? 'found' : 'lost') as PostType,
    category: typeof source.category === 'string' && source.category ? source.category : '其他',
    title: typeof source.title === 'string' ? source.title : '',
    description: typeof source.description === 'string' ? source.description : '',
    color: typeof source.color === 'string' ? source.color : null,
    brand: typeof source.brand === 'string' ? source.brand : null,
    location: typeof source.location === 'string' ? source.location : '',
    event_time: typeof source.event_time === 'string' ? source.event_time : '',
    public_features: toDisplayText(source.public_features),
    status: (typeof source.status === 'string' ? source.status : 'published') as ItemStatus,
    ai_status: (typeof source.ai_status === 'string' ? source.ai_status : 'pending') as AiStatus,
    owner_user_id:
      source.owner_user_id === undefined || source.owner_user_id === null
        ? null
        : Number(source.owner_user_id),
    thumbnail_url: thumbnail,
    cover_image_url: thumbnail ?? primaryUrl,
    created_at: typeof source.created_at === 'string' ? source.created_at : '',
    updated_at: typeof source.updated_at === 'string' ? source.updated_at : null,
  }
}

/** 详情归一化：补齐 images / 隐藏特征可见性 / 脱敏联系方式 */
export function normalizeItemDetail(raw: unknown): ItemDetail {
  const source = (raw ?? {}) as Record<string, unknown>
  const summary = normalizeItemSummary(source)

  const images: ItemImage[] = Array.isArray(source.images)
    ? (source.images as Array<Record<string, unknown>>).map((image) => ({
        image_key: String(image.key ?? image.image_key ?? ''),
        url: typeof image.url === 'string' ? image.url : null,
        is_primary: image.is_primary === true,
      }))
    : []

  const hiddenFeatures = toDisplayText(source.hidden_features)

  return {
    ...summary,
    images,
    hidden_features: hiddenFeatures,
    // 后端未显式返回 can_view_hidden 时，以「是否下发隐藏特征」作为可见性依据
    can_view_hidden: source.can_view_hidden === true || Boolean(hiddenFeatures),
    contact_masked:
      typeof source.contact_id === 'string'
        ? source.contact_id
        : typeof source.contact_masked === 'string'
          ? source.contact_masked
          : null,
    ai_attributes: (source.ai_attributes as AiAttributes | null) ?? null,
  }
}

/** 发布结果归一化：后端返回 id + images[{key,url,size}] */
export function normalizeItemCreateResult(raw: unknown): ItemCreateResult {
  const source = (raw ?? {}) as Record<string, unknown>
  const images = Array.isArray(source.images) ? (source.images as Array<Record<string, unknown>>) : []

  return {
    item_id: Number(source.item_id ?? source.id ?? 0),
    status: (typeof source.status === 'string' ? source.status : 'published') as ItemStatus,
    image_keys: images
      .map((image) => String(image.key ?? image.image_key ?? ''))
      .filter((key) => Boolean(key)),
    ai_status: (typeof source.ai_status === 'string' ? source.ai_status : 'pending') as AiStatus,
    ai_attributes: (source.ai_attributes as AiAttributes | null) ?? null,
  }
}

/** 把筛选条件整理成后端可识别的查询参数（去掉空值，收敛分页上限） */
export function buildItemListParams(query: ItemListQuery = {}): Record<string, unknown> {
  const params: Record<string, unknown> = {
    page: clampPage(query.page),
    page_size: clampPageSize(query.page_size),
  }
  if (query.post_type) params.post_type = query.post_type
  if (query.category) params.category = query.category
  if (query.status) params.status = query.status
  if (query.keyword && query.keyword.trim()) params.keyword = query.keyword.trim()
  return params
}

/**
 * 发布物品。
 * - 图片字段名固定为 image，多张时重复 append（≤ MAX_IMAGE_COUNT 张，前端已校验）；
 * - hidden_features / public_features 按契约序列化为 JSON 字符串数组；
 * - 携带 X-Idempotency-Key，避免网络重试导致重复发布。
 * @param onProgress 上传进度回调（0~99；AI 分析进度体现在返回的 ai_status）
 */
export async function createItem(
  payload: ItemCreatePayload,
  onProgress?: (percent: number) => void,
): Promise<ItemCreateResult> {
  const form = new FormData()
  form.append('post_type', payload.post_type)
  form.append('category', payload.category)
  form.append('title', payload.title.trim())
  form.append('description', payload.description.trim())
  form.append('location', payload.location.trim())
  form.append('event_time', payload.event_time)
  form.append('hidden_features', toJsonArrayText(payload.hidden_features))
  form.append('contact_id', payload.contact_id.trim())

  const color = payload.color?.trim()
  const brand = payload.brand?.trim()
  const publicFeatures = payload.public_features?.trim()
  if (color) form.append('color', color)
  if (brand) form.append('brand', brand)
  if (publicFeatures) form.append('public_features', toJsonArrayText(publicFeatures))

  payload.images.slice(0, MAX_IMAGE_COUNT).forEach((file) => form.append('image', file, file.name))

  const data = await httpPostForm<unknown>('/items', form, {
    // 图片上传 + 触发 AI 分析耗时较长，单独放宽超时
    timeout: 60000,
    idempotencyKey: createIdempotencyKey(),
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) return
      onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)))
    },
  })

  return normalizeItemCreateResult(data)
}

/** 物品列表（分页 + 筛选） */
export async function listItems(query: ItemListQuery = {}): Promise<PageResult<ItemSummary>> {
  const data = await httpGet<unknown>('/items', buildItemListParams(query))
  const page = toPageResult<unknown>(data, clampPage(query.page), clampPageSize(query.page_size))
  return { ...page, items: page.items.map(normalizeItemSummary) }
}

/** 物品详情 */
export async function getItem(itemId: number): Promise<ItemDetail> {
  const data = await httpGet<unknown>(`/items/${itemId}`)
  return normalizeItemDetail(data)
}

/** 我的发布记录（本人视角，可能包含 hidden_features） */
export async function getMyItems(query: PageQuery = {}): Promise<PageResult<ItemSummary>> {
  const page = clampPage(query.page)
  const pageSize = clampPageSize(query.page_size)
  const data = await httpGet<unknown>('/me/items', { page, page_size: pageSize })
  const result = toPageResult<unknown>(data, page, pageSize)
  return { ...result, items: result.items.map(normalizeItemSummary) }
}
