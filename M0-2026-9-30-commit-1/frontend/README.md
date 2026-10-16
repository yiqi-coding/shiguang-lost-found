# 拾光 · 校园多模态失物检索与可信认领平台（前端）

华为云 AI 创新应用竞赛作品。用户发布失物 / 拾物信息，系统做图文、时间、地点多模态匹配，
再通过「隐藏特征问答 + 管理员审核」完成可信认领。

> 当前仓库状态：**M0/M1 前端骨架**。页面结构、类型契约、表单字段与校验规则、接口调用函数均已完整实现，
> 可直接进入联调；列表与详情在无后端时展示空状态 / 错误提示，不内置假数据。

## 一、技术栈

| 类别 | 选型 |
| --- | --- |
| 框架 | Vue 3（全部使用 `<script setup lang="ts">`） |
| 构建 | Vite 5 + TypeScript 5（`vue-tsc` 做类型检查） |
| UI | Element Plus 2（全量引入 + 中文语言包） |
| 路由 | vue-router 4（history 模式 + 全局守卫） |
| 状态 | pinia 2 |
| 请求 | axios 1（统一封装：请求追踪 ID、token、响应解包、错误码提示、401 处理） |

## 二、安装与启动

```bash
# 1. 进入前端目录
cd frontend

# 2. 安装依赖（Node >= 18）
npm install
# 或 pnpm install

# 3. 启动开发服务器（默认 http://127.0.0.1:5173）
npm run dev

# 4. 类型检查
npm run typecheck

# 5. 生产构建（先 vue-tsc 类型检查，再 vite build，产物在 dist/）
npm run build
npm run preview
```

开发环境默认把 `/api` 代理到本地后端 `http://127.0.0.1:8000`（见 `.env.development` 的 `VITE_PROXY_TARGET`）。
后端未启动时前端仍可正常打开：列表、详情、匹配等页面会展示空状态或错误提示。

## 三、环境变量

所有变量必须以 `VITE_` 开头。`.env.development` / `.env.production` 不含密钥，需提交仓库；
`.env.local` 与 `.env.*.local` 已被 `.gitignore` 忽略。

| 变量 | 说明 | 开发默认值 | 生产默认值 |
| --- | --- | --- | --- |
| `VITE_APP_TITLE` | 应用标题（注入 `index.html` 标题与 `document.title`） | 拾光 · 校园失物检索与可信认领平台 | 同左 |
| `VITE_API_BASE_URL` | 接口前缀，axios `baseURL` | `/api/v1` | `/api/v1` |
| `VITE_API_TIMEOUT` | 默认请求超时（毫秒） | `15000` | `20000` |
| `VITE_PROXY_TARGET` | 仅开发环境：devServer 代理目标 | `http://127.0.0.1:8000` | — |

修改后可参考 `.env.example`。

## 四、目录结构

```
frontend/
├─ index.html                 入口 HTML（标题取自 VITE_APP_TITLE）
├─ package.json               依赖与脚本
├─ vite.config.ts             别名 @ → src、/api 代理、分包配置
├─ tsconfig.json              TS 配置（strict、@ 路径映射、element-plus 全局组件类型）
├─ tsconfig.node.json         vite.config.ts 专用 TS 配置
├─ .env.development           开发环境变量
├─ .env.production            生产环境变量
├─ .env.example               环境变量模板
├─ .gitignore
├─ public/favicon.svg
└─ src/
   ├─ main.ts                 应用入口：注册 Element Plus（中文）、图标、pinia、router
   ├─ App.vue                 根组件：AppHeader + RouterView + AppFooter + 全局基础样式
   ├─ env.d.ts                .vue 声明与环境变量类型
   ├─ api/                    HTTP 出口层（页面只调这里，不直接用 axios）
   │  ├─ http.ts              axios 封装：X-Request-ID、token、响应解包、ApiError、401 回调
   │  ├─ items.ts             POST /items、GET /items、GET /items/{id}、GET /me/items
   │  ├─ matches.ts           GET /items/{id}/matches、POST /items/{id}/match
   │  ├─ claims.ts            POST /claims、POST /claims/{id}/verify、GET /claims/{id}、GET /me/claims
   │  ├─ admin.ts             GET /admin/claims、GET /admin/claims/{id}、POST /admin/claims/{id}/review、GET /admin/audit-logs
   │  ├─ auth.ts              登录 / 登出 / 当前用户（占位，路径待后端确认）
   │  └─ health.ts            GET /health
   ├─ types/                  类型契约 + 该领域的中文文案常量（状态映射、免责声明等）
   │  ├─ api.ts               统一响应包装、错误码、分页、健康检查、免责声明
   │  ├─ item.ts              物品类型、分类、状态文案、图片与表单阈值
   │  ├─ match.ts             匹配候选、6 个维度、维度释义、分数归一化
   │  ├─ claim.ts             认领问题 / 作答 / 验证结果 / 审核动作 / 审核台聚合视图
   │  └─ user.ts              用户、角色、审计日志
   ├─ router/index.ts         路由表 + 守卫（requiresAuth / requiresAdmin）
   ├─ stores/
   │  ├─ session.ts           token、登录时间、资料缓存、待提交认领暂存（与框架无关的函数 + store）
   │  └─ user.ts              登录态、角色判断、登录 / 登出 / 恢复会话
   ├─ components/             公共组件
   │  ├─ AppHeader.vue        页头：导航、发布入口、登录弹窗（骨架阶段无独立登录页）
   │  ├─ AppFooter.vue        页脚：快捷入口 + 免责声明
   │  ├─ ItemForm.vue         失物 / 拾物共用发布表单（字段、校验规则、隐私提示）
   │  ├─ ImageUploader.vue    图片选择与预览（格式 / 大小 / 张数校验）
   │  ├─ MatchScoreCard.vue   候选匹配卡片（总分 + 6 维分 + 依据 + 不确定因素 + 风险提示）
   │  ├─ PrivacyHint.vue      隐私与合规提示条
   │  ├─ StatusTag.vue        统一状态标签（物品 / 认领 / AI / 风险等级）
   │  └─ EmptyState.vue       空状态与错误占位
   ├─ views/                  10 个页面 + 404
   ├─ styles/variables.scss   SCSS 变量 → CSS 自定义属性（--sg-*）+ 全局布局工具类
   └─ utils/format.ts         时间与文本格式化（需求未要求该目录，用于统一展示格式）
```

## 五、页面与路由对照表

| # | 路由路径 | 路由名 | 视图文件 | 职责 | 主要依赖接口 | 权限 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `/` | `home` | `HomeView.vue` | 项目介绍、核心能力、认领流程、免责声明、后端健康状态 | `GET /health` | 公开 |
| 2 | `/items` | `item-list` | `ItemListView.vue` | 物品列表（post_type / category / status / keyword + 分页） | `GET /items` | 公开 |
| 3 | `/items/new/lost` | `item-create-lost` | `ItemCreateLostView.vue` | 发布失物（含隐藏特征、图片上传、AI 状态反馈） | `POST /items` | 需登录 |
| 4 | `/items/new/found` | `item-create-found` | `ItemCreateFoundView.vue` | 发布拾物（拾获经过与保管方式说明） | `POST /items` | 需登录 |
| 5 | `/items/:id` | `item-detail` | `ItemDetailView.vue` | 物品详情（按角色脱敏展示隐藏特征）、重算匹配入口 | `GET /items/{id}`、`POST /items/{id}/match` | 公开 |
| 6 | `/items/:id/matches` | `item-matches` | `MatchResultView.vue` | 候选匹配：总分 + 6 维分 + 依据 + 不确定因素 + 风险提示，发起认领 | `GET /items/{id}/matches`、`POST /items/{id}/match`、`POST /claims` | 公开（重算 / 认领需登录） |
| 7 | `/claims/:id/verify` | `claim-verify` | `ClaimVerifyView.vue` | 认领验证：2~3 个问题作答 + 可信度 / 风险等级 / 状态 | `GET /claims/{id}`、`POST /claims/{id}/verify` | 需登录 |
| 8 | `/me` | `my-records` | `MyRecordsView.vue` | 我的记录：我的发布（el-tabs）+ 我的认领 | `GET /me/items`、`GET /me/claims` | 需登录 |
| 9 | `/admin/review` | `admin-review` | `AdminReviewView.vue` | 管理员审核台：匹配维度分 / 依据 / 验证结果 / 可信度 / 风险等级 / 脱敏联系方式 / 历史日志 + approve / reject / request_more_info | `GET /admin/claims`、`GET /admin/claims/{id}`、`POST /admin/claims/{id}/review` | 需管理员 |
| 10 | `/admin/logs` | `admin-logs` | `AdminLogsView.vue` | 管理员日志：按 target_type / target_id 查询审计日志 | `GET /admin/audit-logs` | 需管理员 |
| — | `/:pathMatch(.*)*` | `not-found` | `NotFoundView.vue` | 404 兜底 | 无 | 公开 |

## 六、接口契约约定

唯一事实来源：`docs/api-contract.md`（《拾光 API 契约 Baseline v1.0 · Frozen》）+ 后端 OpenAPI。
前端不得单方面变更字段名与枚举值；如契约变更，先改 `src/types/*`，再改 `src/api/*` 的归一化函数。

- 统一成功响应：`{ "code": "OK", "message": "success", "data": {}, "request_id": "req_xxx" }`
- 统一错误响应：`{ "code": "ITEM_NOT_FOUND", "message": "物品记录不存在", "detail": null, "request_id": "req_xxx" }`
- 业务错误码：`VALIDATION_ERROR`、`UNAUTHORIZED`、`FORBIDDEN`、`ITEM_NOT_FOUND`、`CLAIM_NOT_FOUND`、
  `MATCH_NOT_FOUND`、`STATE_CONFLICT`、`ALGORITHM_SERVICE_ERROR`、`STORAGE_SERVICE_ERROR`、
  `AI_SERVICE_ERROR`、`RATE_LIMITED`、`INTERNAL_ERROR`
- 分页：请求 `page`（从 1 开始）、`page_size`（默认 20、最大 50，前端自动收敛）；
  响应 `data.list` + `data.pagination`，由 `toPageResult()` 归一化为扁平的 `{ items, total, page, page_size, total_pages }`
- 请求头：自动携带 `X-Request-ID`（`crypto.randomUUID()`，非安全上下文自动降级）、
  `Authorization: Bearer <access_token>`；发布 / 认领 / 审核额外携带 `X-Idempotency-Key`
- 响应拦截器解包 `data`；`code !== 'OK'` 时 `ElMessage.error(message)` 并抛出 `ApiError`；401 清理会话并回首页；
  健康检查在依赖故障时返回 503 但业务体为 `OK`，拦截器识别后按成功处理

### 鉴权与角色

- 登录：`POST /api/v1/auth/login`（`{ username, password }`）→ `{ access_token, token_type, expires_in, user }`，
  JWT 有效期 2 小时；`GET /api/v1/auth/me` 用于刷新页面后恢复会话。
- 演示快捷登录：`POST /api/v1/auth/demo-login`（`{ role }`，仅后端 `APP_ENV=demo` 可用），
  页头登录弹窗提供「学生用户 / 管理员」两个按钮，供答辩时一键切换身份。
- 角色只有 `student` 与 `admin`：`/items/new/*`、`/claims/:id/verify`、`/me` 需要登录；
  `/admin/*` 需要管理员。路由守卫只是体验层拦截，真正的权限由后端实施（契约 §1.6）。
- JWT 无状态，契约未定义登出接口，`logout()` 只清理本地会话与资料缓存。

### 字段归一化（`src/api/*` 的适配层）

后端契约字段与页面模型之间做了一层显式归一化，页面只依赖归一化后的模型：

| 后端字段 | 前端模型 | 处理位置 |
| --- | --- | --- |
| `items[].id` | `item_id` | `normalizeItemSummary` |
| `thumbnail_url` | `cover_image_url` | `normalizeItemSummary` |
| `public_features` / `hidden_features`（JSON 字符串数组） | 换行拼接的展示文本 | `toDisplayText` / `toJsonArrayText` |
| `contact_id`（脱敏） | `contact_masked` | `normalizeItemDetail` |
| `results[]` / `candidate_item` / `computed_at` | `candidates[]` / `matched_item` / `generated_at` | `normalizeMatchList` |
| `admin/claims/{id}` 的 `claim` / `lost_item` / `found_item` / `verification` | 审核台聚合模型 | `normalizeAdminClaimDetail` |
| `audit-logs[].id` / `operator_username` | `id` / `operator_username` | `normalizeAuditLog` |

### 关键设计决策

1. **`api/http.ts` 是全站唯一 HTTP 出口**：页面不直接使用 axios，统一拿到解包后的 `data`；
   错误统一转成 `ApiError`（含 `code` / `requestId` / `httpStatus`），页面按 `code` 分支（如 `ITEM_NOT_FOUND` 展示空状态）。
   401 处理通过 `setUnauthorizedHandler` 由 router 注入，避免 `http ↔ router` 循环依赖。
2. **契约常量集中在 `src/types/`**：状态文案映射、分类枚举、校验阈值、免责声明只有一处定义，
   避免「已认领 / 已领取」这类文案漂移；`ItemSummary` 刻意不含 `hidden_features`，从类型层面防止越权展示。
3. **发布表单由 `ItemForm.vue` 共用**：失物 / 拾物两个页面的字段与校验规则完全一致，
   差异只在文案与提交后的提示；页面负责 `POST /items`、上传进度与 AI 状态反馈，职责清晰。
4. **AI 失败不阻塞发布**：`ai_status=failed` 时页面明确提示「描述与手填属性已保存、可继续匹配」，
   并在详情 / 匹配页提供「重新计算匹配」入口。
5. **匹配结果始终带合规文案**：`MatchScoreCard.vue` 与匹配结果页固定展示「候选匹配不等于确认归属」，
   同时呈现「匹配依据 + 不确定因素 + 风险提示」；`degraded=true` 时额外展示「降级结果」警示。
6. **认领验证页不展示任何答案**：类型定义里就没有标准答案字段；页面只渲染问题文本、申请人自己的作答，
   以及后端返回的「是否命中 + 得分 + 理由」，并显式提示「系统不会展示标准答案」。
7. **降级策略**：`GET /claims/{claim_id}` 与待审核列表接口 `GET /admin/claims` 未列入契约附录 A。
   - 认领验证页：接口优先，失败时回退到创建认领时写入 `sessionStorage` 的问题列表；
   - 审核台：列表接口 404 时自动切换为「输入认领 ID 查询详情」模式，审核功能不受影响。
8. **响应式**：Element Plus 栅格（`:xs` / `:sm` / `:md`）+ 媒体查询；
   窄屏（<768px）用卡片列表替代表格（`.sg-only-desktop` / `.sg-only-mobile` 纯 CSS 切换，不重复请求）。

## 七、下一步（联调前需要补齐）

- **后端侧待确认**：
  - `GET /api/v1/claims/{claim_id}`（认领详情 + 问题列表），否则刷新认领验证页只能走 `sessionStorage` 降级；
  - 待审核认领列表接口（当前按 `GET /api/v1/admin/claims` 预留，404 时审核台降级为按认领 ID 查询）；
  - `GET /health` 的挂载位置（`/api/v1/health` 与根路径 `/health` 前端都会尝试）。
- **前端侧**：
  - 后端就绪后补齐图片直传 OBS 的进度与失败重试；按需改为 Element Plus 按需引入以减小体积；
  - 增加单元测试（表单校验、`toScorePercent`、`toPageResult`、各归一化函数）与端到端冒烟用例；
  - 如需独立登录页，可新增 `/login` 路由并把 `AppHeader` 的登录弹窗迁移过去。

## 八、合规声明

> 本系统提供候选匹配和辅助审核建议，不构成物品所有权的自动认定。最终认领结果由平台管理员根据匹配依据、验证结果和实际交接情况确认。

该文案定义在 `src/types/api.ts` 的 `OWNERSHIP_DISCLAIMER`，由全局页脚与首页共同引用，请勿在页面中另行改写。
