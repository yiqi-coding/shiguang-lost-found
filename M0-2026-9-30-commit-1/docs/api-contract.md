# 「拾光」API 契约（Baseline v1.0 · Frozen）

> 状态：**已冻结**（M0 里程碑交付物 D2）
>
> 冻结日期：2026-09-30
>
> 唯一事实来源：本文件 + 后端 OpenAPI（`/docs`、`/openapi.json`）。两者冲突时以本文件为准，并立即修正代码。
>
> 变更规则：任何接口变更必须在本文件末尾「附录 C · 变更记录」追加一行（版本号 / 日期 / 变更人 / 影响范围 / 是否破坏兼容），并同步通知前端与算法负责人。**字段名、枚举值、错误码属于破坏性变更，需三方确认。**

---

## 1. 通用约定

### 1.1 基础

| 项 | 约定 |
|---|---|
| 前缀 | `/api/v1`（算法服务独立前缀 `/algorithm/v1`） |
| 数据格式 | JSON（`Content-Type: application/json; charset=utf-8`） |
| 上传接口 | `multipart/form-data` |
| 时间格式 | ISO 8601 带时区，例：`2026-09-28T15:00:00+08:00`；入库统一 UTC 存储、按客户端时区展示 |
| 字符编码 | UTF-8 |
| 认证方式 | `Authorization: Bearer <access_token>`（JWT，HS256，有效期 2h） |
| 请求追踪 | 服务端为每个请求生成 `X-Request-ID`（客户端可传入，服务端透传或覆盖），响应头与响应体 `request_id` 一致 |
| 分页参数 | `page`（从 1 开始）、`page_size`（默认 20，**上限 50**） |
| 排序参数 | `sort_by`、`order`（`asc`/`desc`），未指定时按 `created_at desc` |
| 幂等 | 上传、认领、审核接口需带 `X-Idempotency-Key`（可选但推荐） |
| 限流 | 返回 `429` + 错误码 `RATE_LIMITED`；重点限流：登录 5 次/分/IP、认领发起 10 次/时/用户、AI 调用 30 次/时/用户 |

### 1.2 HTTP 状态码使用

| 状态码 | 场景 |
|---|---|
| 200 | 成功（含查询、更新） |
| 201 | 创建成功（发布物品、发起认领） |
| 400 | 参数格式错误 → `VALIDATION_ERROR` |
| 401 | 未登录 / Token 失效 → `UNAUTHORIZED` |
| 403 | 已登录但无权限 → `FORBIDDEN` |
| 404 | 对象不存在 → `*_NOT_FOUND` |
| 409 | 状态冲突（如非法状态流转、重复认领） → `STATE_CONFLICT` |
| 413 | 上传文件过大 → `VALIDATION_ERROR` |
| 422 | 请求体语义校验失败（Pydantic） → `VALIDATION_ERROR` |
| 429 | 触发限流 → `RATE_LIMITED` |
| 500 | 未预期异常 → `INTERNAL_ERROR` |
| 502/503 | 依赖服务故障 → `AI_SERVICE_ERROR` / `STORAGE_SERVICE_ERROR` / `ALGORITHM_SERVICE_ERROR` |

> **红线**：参数错误一律 4xx，**不允许返回 500**。

### 1.3 统一成功响应

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "req_7f3a9c21b0d4"
}
```

- `data` 为对象或数组；分页时结构固定为：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "list": [],
    "pagination": { "page": 1, "page_size": 20, "total": 137, "total_pages": 7 }
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

- 无返回内容的操作（如重算匹配的异步触发）返回 `data: null`，`message` 说明具体结果。

### 1.4 统一错误响应

```json
{
  "code": "ITEM_NOT_FOUND",
  "message": "物品记录不存在",
  "detail": null,
  "request_id": "req_7f3a9c21b0d4"
}
```

- `detail`：仅在校验失败时填充字段级错误，结构为 `[{"field": "title", "reason": "长度需在 5~80 字之间"}]`；生产环境不返回堆栈。
- `message`：面向用户的中文可读文案，前端可直接 `ElMessage.error(message)`。

### 1.5 错误码全表（冻结）

| 错误码 | HTTP | 含义 | 前端建议处理 |
|---|---|---|---|
| `VALIDATION_ERROR` | 400/413/422 | 参数或文件校验失败 | 高亮对应表单字段 |
| `UNAUTHORIZED` | 401 | 未登录 / Token 失效 | 清除会话 → 跳登录 |
| `FORBIDDEN` | 403 | 越权访问（含读取他人 `hidden_features`） | 提示无权限，不重试 |
| `ITEM_NOT_FOUND` | 404 | 物品不存在 | 返回列表页 |
| `CLAIM_NOT_FOUND` | 404 | 认领记录不存在 | 返回我的记录 |
| `MATCH_NOT_FOUND` | 404 | 匹配记录不存在 | 提示先计算匹配 |
| `STATE_CONFLICT` | 409 | 非法状态流转 / 重复认领 | 刷新页面获取最新状态 |
| `ALGORITHM_SERVICE_ERROR` | 502 | 算法服务不可用 | 展示「已降级为规则匹配」标识 |
| `STORAGE_SERVICE_ERROR` | 502 | OBS/存储不可用 | 允许无图发布或提示重试 |
| `AI_SERVICE_ERROR` | 502 | MaaS/模型服务不可用 | 提示可手动填写属性，**不阻断发布** |
| `RATE_LIMITED` | 429 | 触发限流 | 提示稍后重试，禁用提交按钮 |
| `INTERNAL_ERROR` | 500 | 未预期错误 | 提示联系管理员，附带 `request_id` |

### 1.6 认证与角色

| 角色 | 说明 | 获取 token |
|---|---|---|
| `student` | 普通用户（失主 / 拾到者） | `POST /api/v1/auth/login` |
| `admin` | 管理员 | 同上，账号由演示数据预置 |

**权限硬性要求（来自需求说明书 4.1.2，实现时必须逐条对齐）**

1. 普通用户**不能**读取其他物品的 `hidden_features`（序列化层按 `viewer_id == owner_user_id or role == admin` 决定是否输出该字段）；
2. 普通用户**不能**直接改物品状态为 `claimed`（无该接口，状态只能经审核流转）；
3. 认领者**不能**看到标准验证答案（`questions` 与 `answer_summary` 分离存储，接口永不返回答案）；
4. 管理员操作**必须**写入 `audit_logs`（含 `request_id`）；
5. 前端隐藏字段**不属于**安全措施，权限必须由后端 API 实施。

---

## 2. 枚举字典（冻结，前后端必须一致）

### 2.1 `post_type`

| 值 | 中文 |
|---|---|
| `lost` | 我丢失了物品 |
| `found` | 我拾到了物品 |

### 2.2 `category`

`无线耳机`、`水杯`、`书籍`、`校园卡`、`雨伞`、`背包`、`其他`

### 2.3 `item.status`（物品业务状态）

| 值 | 中文 | 允许的下一状态 |
|---|---|---|
| `published` | 已发布 | `matched`, `suspended`, `closed` |
| `matched` | 已匹配 | `claiming`, `suspended`, `closed` |
| `claiming` | 认领中 | `pending_review`, `matched`(撤回), `suspended` |
| `pending_review` | 待审核 | `claimed`, `rejected`, `request_more_info`, `disputed` |
| `request_more_info` | 待补充信息 | `pending_review`, `rejected`, `closed` |
| `claimed` | 已认领 | `closed` |
| `rejected` | 已驳回 | `claiming`(重新认领), `closed` |
| `suspended` | 已暂停 | `published`, `matched` |
| `disputed` | 争议中 | `claimed`, `rejected`, `closed` |
| `closed` | 已关闭 | —（终态） |

> 非法跳转一律返回 `409 STATE_CONFLICT`。状态机由后端集中实现（`app/services/state_machine.py`），**禁止在路由里散写状态赋值**。

### 2.4 `claim.status`

`claiming`（认领中）→ `pending_review`（待审核）→ `approved`（已通过）/ `rejected`（已驳回）；管理员可置 `request_more_info`（待补充信息）。

### 2.5 `ai_status`

`pending`、`success`、`failed`

### 2.6 `risk_level`

`low`（可信度 ≥ 0.75）、`medium`（0.45 ≤ 分 < 0.75）、`high`（< 0.45）
> 阈值必须来自配置 `config/matching.yaml`，不得硬编码。

> ⚠ **命名歧义警告（务必区分两个同名字段）**
>
> | 名称 | 所在位置 | 含义 | 阈值 |
> |---|---|---|---|
> | `risk_level` | 认领响应 / `claims.risk_level` | **整份认领申请的风险等级** | 0.75 / 0.45（本节） |
> | `answer_matching.fuzzy.confident_threshold` | `algorithm/config/questions.yaml` | **单道题的答案相似度是否算命中** | 0.50 |
> | `match_level` | 匹配响应 | **候选匹配等级** | 0.75 / 0.50（见 §3.6） |
>
> 三者阈值数值不同、语义完全不同，**不得互相套用**。`questions.yaml` 中 `scoring.risk_levels`
> 必须与本节的 0.75 / 0.45 保持一致（已有单元测试 `TestRiskThresholdsAlignWithContract` 守护）。

> **防冒领保障**：当隐藏特征答案匹配分 < 0.30 时，必须通过可配置风险规则
> （`questions.yaml → behavior.rules → id: very_low_answer_match`）扣减行为分，
> 使「几乎全部答错」的申请落到 `high` 风险等级，而非停在 `medium`。

### 2.7 管理员审核动作 `action`

| 值 | 中文 | 结果状态 |
|---|---|---|
| `approve` | 通过认领 | `claimed` |
| `reject` | 驳回认领 | `rejected` |
| `request_more_info` | 要求补充信息 | `request_more_info` |

### 2.8 审计日志 `action` 取值

`item.create`、`item.update`、`item.status_change`、`match.compute`、`claim.create`、`claim.verify`、`claim.verify_failed`、`claim.review`、`user.login`、`user.disable`、`config.update`

---

## 3. 接口详情

### 3.1 健康检查

```http
GET /health
```

**说明**：无需鉴权。返回各依赖的配置状态与连通性，**不得返回任何密钥、Token、密码、完整连接串**。

**200 响应**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "app": { "status": "ok", "env": "production", "version": "1.0.0", "uptime_seconds": 8421 },
    "database": { "status": "ok", "driver": "mysql+pymysql", "latency_ms": 3, "host_masked": "rm-****.mysql.rds.myhuaweicloud.com" },
    "redis": { "status": "ok", "latency_ms": 1, "degraded": false },
    "obs": { "status": "ok", "bucket": "shiguang-****", "endpoint_masked": "https://obs.****.myhuaweicloud.com" },
    "algorithm_service": { "status": "ok", "url_masked": "http://algorithm:9000", "model_version": "baseline-1.0" },
    "maas": { "status": "not_configured", "configured": false }
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

- `status` 取值：`ok` / `degraded` / `down` / `not_configured`
- 任一依赖 `down` 时，整体 `data.app.status` 仍为 `ok`（进程存活），但 HTTP 状态码返回 **503**，便于探针区分。K8s/负载均衡 liveness 使用 `GET /health/live`（仅测进程），readiness 使用 `GET /health/ready`。

---

### 3.2 认证

#### 3.2.1 登录

```http
POST /api/v1/auth/login
Content-Type: application/json
```

```json
{ "username": "demo_student", "password": "******" }
```

**200 响应**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "access_token": "eyJhbGciOi...",
    "token_type": "bearer",
    "expires_in": 7200,
    "user": { "id": 12, "username": "demo_student", "role": "student", "campus_id": "main", "contact_id": "138****8888" }
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**异常**：`401 UNAUTHORIZED`（账号或密码错误，**不区分提示**，防用户枚举）、`429 RATE_LIMITED`。

#### 3.2.2 当前用户

```http
GET /api/v1/auth/me
```

返回与登录响应中 `user` 相同结构。用于前端刷新页面后恢复会话。

#### 3.2.3 演示用户快捷登录（仅 `APP_ENV=demo` 可用）

```http
POST /api/v1/auth/demo-login
Content-Type: application/json

{ "role": "student" }
```

**说明**：为答辩演示提供一键切换身份（失主 / 拾到者 / 管理员）。`APP_ENV=production` 时该接口返回 `404`，避免线上风险。**演示账号必须来自脱敏数据集，不得使用真实账号。**

---

### 3.3 物品发布

```http
POST /api/v1/items
Content-Type: multipart/form-data
Authorization: Bearer <token>
```

**表单字段**

| 字段 | 类型 | 必填 | 校验规则 |
|---|---|:--:|---|
| `post_type` | string | 是 | `lost` / `found` |
| `category` | string | 是 | 见 2.2 |
| `title` | string | 是 | 5~80 字；去除首尾空格后校验 |
| `description` | string | 是 | 10~2000 字 |
| `location` | string | 是 | 1~128 字，校园地点 |
| `event_time` | string | 是 | ISO 8601；不得晚于当前时间 +5 分钟；不得早于当前时间 -2 年 |
| `hidden_features` | string(JSON) | 是 | 非空；序列化后 ≤ 2000 字；仅本人与管理员可见 |
| `color` | string | 否 | ≤ 32 字 |
| `brand` | string | 否 | ≤ 64 字 |
| `public_features` | string(JSON) | 否 | 对外展示特征，数组或对象 |
| `contact_id` | string | 否 | ≤ 128 字；**入库前脱敏**（手机号中间 4 位打码） |
| `image` | file | 否 | JPEG/PNG/WEBP；≤ 10 MB；单条记录最多 3 张（首版字段名为 `image`，多图时重复该字段名） |

**文件校验顺序（全部在服务端执行，不可仅靠前端）**

1. 扩展名白名单 → 2. HTTP `Content-Type` → 3. 魔数（MIME sniffing）→ 4. 大小 → 5. Pillow 可解析性（`Image.open().verify()`）→ 6. 拒绝 HTML/SVG/可执行文件。

**对象 Key 规则**：`items/{yyyy}/{mm}/{uuid4}.{ext}`，**禁止**使用原始文件名。

**201 响应**

```json
{
  "code": "OK",
  "message": "发布成功",
  "data": {
    "id": 1001,
    "post_type": "lost",
    "status": "published",
    "ai_status": "pending",
    "images": [{ "key": "items/2026/09/9f1c....jpg", "url": "https://obs.../signed?expires=...", "size": 1048576 }],
    "ai_attributes": null
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**降级行为（强制）**

- MaaS 不可用 → 物品**仍然创建成功**，`ai_status="failed"`，`data.ai_attributes=null`，`message` 提示「AI 分析失败，请手动补充物品属性」。**绝不允许因 AI 失败导致发布失败。**
- OBS 不可用 → 若请求含图片则返回 `502 STORAGE_SERVICE_ERROR`（并提示可去掉图片重试）；若不含图片则正常创建。

---

### 3.4 物品列表

```http
GET /api/v1/items?post_type=found&category=无线耳机&status=published&page=1&page_size=20
```

| 参数 | 必填 | 说明 |
|---|:--:|---|
| `post_type` | 否 | `lost`/`found` |
| `category` | 否 | 精确匹配 |
| `status` | 否 | 逗号分隔多值，如 `published,matched` |
| `keyword` | 否 | 在 `title` + `description` + `ai_attributes.keywords` 中模糊匹配 |
| `location` | 否 | 地点模糊匹配 |
| `time_from` / `time_to` | 否 | ISO 8601，过滤 `event_time` |
| `owner_user_id` | 否 | 仅管理员可用 |
| `page` / `page_size` | 否 | 默认 1 / 20，`page_size ≤ 50` |

**200 响应**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "list": [
      {
        "id": 1001,
        "post_type": "lost",
        "category": "无线耳机",
        "title": "丢失一副黑色无线耳机",
        "description": "在图书馆三楼自习时落在座位上……",
        "color": "黑色",
        "brand": "小米",
        "location": "图书馆三楼",
        "event_time": "2026-09-28T15:00:00+08:00",
        "public_features": ["椭圆形充电盒"],
        "thumbnail_url": "https://obs.../signed?...",
        "ai_status": "success",
        "status": "matched",
        "created_at": "2026-09-28T16:12:03+08:00"
      }
    ],
    "pagination": { "page": 1, "page_size": 20, "total": 137, "total_pages": 7 }
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**红线**：列表响应**必须剔除** `hidden_features`、`owner_user_id` 明文联系方式、`image_key` 原始对象 Key（仅给签名 URL）。此项有专项接口测试（F07）。

---

### 3.5 物品详情

```http
GET /api/v1/items/{item_id}
```

**按角色脱敏规则（后端序列化层实现）**

| 字段 | 游客 | 普通用户（非发布者） | 发布者本人 | 管理员 |
|---|:--:|:--:|:--:|:--:|
| 公开字段 | ✅ | ✅ | ✅ | ✅ |
| `hidden_features` | ❌ | ❌ | ✅ | ✅ |
| `contact_id` | ❌ | ❌ | ✅ | ✅（脱敏展示） |
| `image_key` | ❌ | ❌ | ✅ | ✅ |
| `ai_attributes` | 仅 `visible_features`/`keywords` | 同左 | 全部 | 全部 |
| `owner_user_id` | ❌ | ❌ | ✅ | ✅ |
| 状态变更按钮可用性 | — | 否 | 否（仅可撤回认领） | 是 |

**200 响应（以发布者本人视角为例）**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "id": 1001,
    "post_type": "lost",
    "category": "无线耳机",
    "title": "丢失一副黑色无线耳机",
    "description": "在图书馆三楼自习时落在座位上……",
    "color": "黑色",
    "brand": "小米",
    "location": "图书馆三楼",
    "event_time": "2026-09-28T15:00:00+08:00",
    "public_features": ["椭圆形充电盒"],
    "hidden_features": ["盒盖内侧有一道约3厘米划痕", "蓝牙名称 YiQi-AirPods"],
    "images": [{ "key": "items/2026/09/9f1c....jpg", "url": "https://obs.../signed?...", "is_primary": true }],
    "ai_attributes": { "category": "无线耳机", "color": "黑色", "visible_features": ["椭圆形充电盒"], "keywords": ["蓝牙耳机","黑色"], "uncertainties": ["品牌标识不清晰"] },
    "ai_status": "success",
    "status": "matched",
    "contact_id": "138****8888",
    "created_at": "2026-09-28T16:12:03+08:00",
    "updated_at": "2026-09-28T16:12:10+08:00"
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**异常**：`404 ITEM_NOT_FOUND`。

---

### 3.6 获取匹配结果

```http
GET /api/v1/items/{item_id}/matches?top_k=5
```

| 参数 | 必填 | 说明 |
|---|:--:|---|
| `top_k` | 否 | 默认 5，上限 20 |
| `min_score` | 否 | 低于该分数的候选不返回；默认取配置 `matching.min_display_score`（默认 0.35） |
| `refresh` | 否 | `true` 时忽略缓存重算（需鉴权） |

**200 响应**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "query_item_id": 1001,
    "model_version": "baseline-1.0",
    "computed_at": "2026-09-28T16:20:00+08:00",
    "cached": true,
    "degraded": false,
    "results": [
      {
        "match_id": 3001,
        "candidate_item": {
          "id": 2001, "post_type": "found", "category": "无线耳机",
          "title": "在图书馆捡到黑色耳机", "location": "图书馆三楼",
          "event_time": "2026-09-28T16:20:00+08:00",
          "thumbnail_url": "https://obs.../signed?...", "status": "matched"
        },
        "total_score": 0.88,
        "score_detail": {
          "category_score": 1.0,
          "attribute_score": 0.92,
          "image_score": 0.81,
          "time_score": 0.95,
          "location_score": 1.0,
          "brand_model_score": 0.5
        },
        "evidence": ["物品类别一致", "颜色一致（黑色）", "时间相差约 1 小时 20 分钟", "地点位于同一栋图书馆"],
        "uncertainties": ["品牌信息未确认", "两张图片拍摄角度不同"],
        "risk_tips": ["该类别为高仿冒风险类别，建议核验隐藏特征"],
        "match_level": "high"
      }
    ],
    "disclaimer": "候选匹配不等于确认归属，最终结果由管理员审核确认。"
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**字段说明**

- `match_level`：`high`（总分 ≥ 0.75）/ `medium`（0.5 ≤ 分 < 0.75）/ `low`（< 0.5），阈值来自配置。**`low` 不得展示为「高匹配」**。
- **字段命名映射（重要）**：后端响应中的 `attribute_score`（属性/文本分）与算法服务响应中的 `text_score` 是**同一维度**。后端落库字段名为 `matches.attribute_score`，因此对前端只暴露 `attribute_score`；后端在调用 `POST /algorithm/v1/match` 后负责把 `scores.text_score` 映射为 `attribute_score`。两侧命名不同是有意为之（数据库列名沿用需求说明书 §5.3），**不要**在任何一侧改名。
- `degraded=true` 表示算法服务不可用、已降级为规则/Mock 匹配，前端必须显式标注「降级结果」。
- `score_detail` 六项权重固定为 0.20 / 0.25 / 0.20 / 0.15 / 0.15 / 0.05，且 `total_score` 必须等于加权和（容差 1e-6），有单元测试断言。

**异常**：`404 ITEM_NOT_FOUND`；算法服务故障返回 `200` + `degraded=true`（若配置允许降级），否则 `502 ALGORITHM_SERVICE_ERROR`。

---

### 3.7 重新计算匹配

```http
POST /api/v1/items/{item_id}/match
Authorization: Bearer <token>
```

```json
{ "force": true, "top_k": 5 }
```

**202 响应**（异步）或 **200**（同步完成）

```json
{
  "code": "OK",
  "message": "匹配任务已提交",
  "data": { "task_id": "task_9c21b0d4", "status": "running", "estimated_seconds": 2 },
  "request_id": "req_7f3a9c21b0d4"
}
```

**副作用（强制）**：结果写入 `matches` 表 + 写 Redis 缓存 `match:item:{item_id}:{version}`（TTL 10min）+ 写 `audit_logs`（`action=match.compute`）。权限：仅物品发布者本人或管理员。

---

### 3.8 发起认领

```http
POST /api/v1/claims
Content-Type: application/json
Authorization: Bearer <token>
```

```json
{ "match_id": 3001 }
```

**201 响应**

```json
{
  "code": "OK",
  "message": "认领已发起，请回答验证问题",
  "data": {
    "claim_id": 4001,
    "match_id": 3001,
    "status": "claiming",
    "questions": [
      { "question_id": "q1", "question_text": "请描述充电盒上的特殊痕迹（位置、形状或长度）。" },
      { "question_id": "q2", "question_text": "请填写该设备设置的蓝牙名称。" },
      { "question_id": "q3", "question_text": "请说明耳机盒内是否缺失某个配件。" }
    ],
    "max_attempts": 3,
    "expires_at": "2026-09-28T17:00:00+08:00"
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**红线**

1. 响应**绝不包含**标准答案、答案摘要、答案哈希；
2. 问题数量 2~3 个，由 MaaS 生成后**必须经过程序校验**（非空、不含答案原文、不与其他问题重复、不含敏感词），校验不通过则回退到 `algorithm/config/questions.yaml` 模板；
3. 同一用户对同一 `match_id` 重复发起 → `409 STATE_CONFLICT`；
4. 只有匹配中的**拾物方**（`found` 记录的发布者）或管理员可发起对 `lost` 记录的认领；申请人不能认领自己发布的物品；
5. 认领会话写入 Redis `claim:session:{claim_id}`（TTL 30min），**不得在 Redis 中明文长期保存标准答案**（只存答案的加盐哈希与题目元数据）。

---

### 3.9 提交认领验证

```http
POST /api/v1/claims/{claim_id}/verify
Content-Type: application/json
Authorization: Bearer <token>
```

```json
{
  "answers": [
    { "question_id": "q1", "answer": "盒子右侧有一道划痕" },
    { "question_id": "q2", "answer": "YiQi-AirPods" }
  ]
}
```

**200 响应**

```json
{
  "code": "OK",
  "message": "验证完成",
  "data": {
    "claim_id": 4001,
    "verification_score": 0.86,
    "credibility_score": 0.79,
    "risk_level": "low",
    "status": "pending_review",
    "per_question": [
      { "question_id": "q1", "matched": true, "score": 0.9, "reason": "关键特征吻合" },
      { "question_id": "q2", "matched": true, "score": 1.0, "reason": "设备名称完全一致" },
      { "question_id": "q3", "matched": false, "score": 0.2, "reason": "未提及配件缺失情况" }
    ],
    "risk_tips": [],
    "attempts_left": 2,
    "next_step": "等待管理员审核"
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**可信度公式（冻结，权重来自 `config/matching.yaml`）**

```
可信度分 = 0.60 × 隐藏特征答案匹配 + 0.20 × 候选物品综合匹配分
         + 0.10 × 发布信息完整度 + 0.10 × 行为/风险规则分
```

**红线**

1. `reason` 文案**不得回显标准答案原文**；
2. 标准答案不写入普通应用日志（仅记录 `claim_id`、得分、是否命中）；
3. 连续失败：`attempts_left` 归零后状态置为 `rejected` 并写入风险标记，同时写 `audit_logs`（`action=claim.verify_failed`）通知管理员；
4. 验证通过**不自动确权**，状态只能进入 `pending_review`，`claimed` 只能由管理员审核产生；
5. `credibility_score` 仅作辅助依据，响应中必须带免责声明（`message` 或 `data.disclaimer`）。

**异常**：`404 CLAIM_NOT_FOUND`；超次数 `409 STATE_CONFLICT`；`403 FORBIDDEN`（非本人认领）。

---

### 3.10 我的记录

```http
GET /api/v1/me/items?page=1&page_size=20
GET /api/v1/me/claims?status=pending_review&page=1&page_size=20
```

- `/me/items`：返回当前用户发布的物品（**本人视角**，含 `hidden_features`），支持 `post_type`、`status` 过滤。
- `/me/claims`：返回当前用户发起的认领，含关联物品摘要、`verification_score`、`risk_level`、`status`、`admin_comment`、`created_at`。**不含标准答案。**

---

### 3.10.1 认领详情（认领人视角）

```http
GET /api/v1/claims/{claim_id}
Authorization: Bearer <token>
```

**用途**：认领验证页刷新/重新进入时恢复问题与作答状态，避免把答案草稿长期留在浏览器端。

**权限**：申请人本人或管理员。他人访问 → `403 FORBIDDEN`。

**200 响应**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "claim_id": 4001,
    "match_id": 3001,
    "status": "claiming",
    "status_text": "认领中",
    "questions": [
      { "question_id": "q1", "question_text": "请描述充电盒上的特殊痕迹（位置、形状或长度）。" },
      { "question_id": "q2", "question_text": "请填写该设备设置的蓝牙名称。" }
    ],
    "attempts_used": 0,
    "max_attempts": 3,
    "attempts_left": 3,
    "created_at": "2026-09-28T16:30:00+08:00",
    "match_summary": {
      "item_title": "在图书馆捡到黑色耳机",
      "post_type": "found",
      "total_score": 0.88,
      "match_level": "high"
    },
    "can_submit": true,
    "disclaimer": "可信度仅为辅助审核依据，不构成所有权认定。"
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**红线**

1. 响应**绝不包含**标准答案、`answers_hash`、`answers_plain`、`answer_summary` 中的答案线索；
2. 未提交验证前 `verification_score` / `credibility_score` / `risk_level` 字段**不出现**（而非返回 null），避免前端误判；
3. 已提交验证后返回 `per_question`（结构与 §3.9 一致）与各项分数；
4. `can_submit=false` 表示次数用尽或状态已关闭，前端应禁用提交。

**异常**：`404 CLAIM_NOT_FOUND`、`403 FORBIDDEN`。

---

### 3.10.2 管理员 · 待审核认领列表

```http
GET /api/v1/admin/claims?status=pending_review&risk_level=high&page=1&page_size=20
Authorization: Bearer <admin_token>
```

**用途**：审核台的左侧待办列表。**没有这个接口审核台无法工作**，属于必要接口。

| 参数 | 必填 | 说明 |
|---|:--:|---|
| `status` | 否 | 逗号分隔多值，默认 `pending_review,request_more_info`；传 `all` 表示不过滤 |
| `risk_level` | 否 | `low` / `medium` / `high`，逗号分隔多值 |
| `category` | 否 | 物品类别 |
| `keyword` | 否 | 在物品标题中模糊匹配 |
| `sort_by` | 否 | `risk_level`（默认，高风险优先）/ `created_at` / `verification_score` |
| `order` | 否 | `asc` / `desc`，默认 `desc` |
| `page` / `page_size` | 否 | 默认 1 / 20，`page_size ≤ 50` |

**200 响应**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "list": [
      {
        "claim_id": 4001,
        "status": "pending_review",
        "status_text": "待审核",
        "risk_level": "low",
        "risk_level_text": "低风险",
        "verification_score": 0.86,
        "credibility_score": 0.79,
        "attempts_used": 1,
        "applicant": { "id": 13, "username": "demo_finder", "contact_id": "139****6666" },
        "item": {
          "id": 2001,
          "title": "在图书馆捡到黑色耳机",
          "category": "无线耳机",
          "post_type": "found",
          "thumbnail_url": "https://obs.../signed?..."
        },
        "total_score": 0.88,
        "match_level": "high",
        "created_at": "2026-09-28T16:30:00+08:00"
      }
    ],
    "pagination": { "page": 1, "page_size": 20, "total": 3, "total_pages": 1 },
    "counts": { "pending_review": 3, "high_risk": 1, "request_more_info": 0 }
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**说明**

- `counts` 用于审核台顶部徽标（`●3`）与筛选项角标，避免前端额外请求；
- 排序默认**高风险优先**，同风险等级按 `created_at` 升序（先来先审）；
- 列表**不含**隐藏特征与答案线索，具体证据在 §3.12 的详情接口中按需获取（每一次查看都会被审计）。

**异常**：`403 FORBIDDEN`（非管理员）。

---

### 3.11 管理员 · 认领审核

```http
POST /api/v1/admin/claims/{claim_id}/review
Authorization: Bearer <admin_token>
Content-Type: application/json
```

```json
{ "action": "approve", "comment": "匹配依据和隐藏特征验证均通过" }
```

| 字段 | 必填 | 规则 |
|---|:--:|---|
| `action` | 是 | `approve` / `reject` / `request_more_info` |
| `comment` | 条件必填 | `reject` 与 `request_more_info` 时**必填**，10~500 字；`approve` 可选 |

**200 响应**

```json
{
  "code": "OK",
  "message": "审核完成",
  "data": {
    "claim_id": 4001,
    "action": "approve",
    "claim_status": "approved",
    "item_id": 2001,
    "item_status": "claimed",
    "reviewed_at": "2026-09-28T16:40:00+08:00",
    "audit_log_id": 9001
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**副作用（事务内原子完成）**

1. 更新 `claims.status`、`admin_comment`、`reviewed_at`；
2. 按状态机更新 `items.status`（`pending_review → claimed` / `rejected` / `request_more_info`）；
3. 写入 `audit_logs`：`operator_id`、`target_type=claim`、`target_id`、`action=claim.review`、`detail`（脱敏后的 action/comment 摘要）、`request_id`；
4. 清理相关缓存：`match:item:{item_id}:*`、`claim:session:{claim_id}`。

**异常**：`403 FORBIDDEN`（非管理员）、`409 STATE_CONFLICT`（当前状态不允许该动作）、`404 CLAIM_NOT_FOUND`。

---

### 3.12 管理员 · 审核详情视图

```http
GET /api/v1/admin/claims/{claim_id}
```

返回审核台所需的**全部**信息（普通用户接口不提供）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "claim": { "id": 4001, "status": "pending_review", "verification_score": 0.86, "credibility_score": 0.79, "risk_level": "low", "created_at": "..." },
    "match": {
      "match_id": 3001,
      "total_score": 0.88,
      "score_detail": { "category_score": 1.0, "attribute_score": 0.92, "image_score": 0.81, "time_score": 0.95, "location_score": 1.0, "brand_model_score": 0.5 },
      "evidence": ["物品类别一致", "颜色一致"],
      "uncertainties": ["品牌信息未确认"]
    },
    "lost_item": { "id": 1001, "title": "丢失一副黑色无线耳机", "hidden_features": ["盒盖内侧有一道约3厘米划痕"], "owner": { "id": 12, "username": "demo_student", "contact_id": "138****8888" } },
    "found_item": { "id": 2001, "title": "在图书馆捡到黑色耳机", "hidden_features": ["耳机盒右侧有一道划痕"], "owner": { "id": 13, "username": "demo_finder", "contact_id": "139****6666" } },
    "verification": {
      "per_question": [{ "question_id": "q1", "question_text": "请描述充电盒上的特殊痕迹", "matched": true, "score": 0.9, "reason": "关键特征吻合" }],
      "attempts_used": 1,
      "risk_tips": []
    },
    "history": [{ "action": "claim.create", "operator": "demo_finder", "created_at": "...", "detail": {} }]
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

> `data` 中**不得**包含标准答案原文，只含「是否命中 + 得分 + 理由」。

---

### 3.13 管理员 · 审计日志

```http
GET /api/v1/admin/audit-logs?target_type=claim&target_id=4001&page=1&page_size=20
```

| 参数 | 必填 | 说明 |
|---|:--:|---|
| `target_type` | 否 | `item` / `claim` / `user` / `config` |
| `target_id` | 否 | 对象 ID |
| `operator_id` | 否 | 操作人 |
| `action` | 否 | 见 2.8，支持逗号分隔多值 |
| `date_from` / `date_to` | 否 | ISO 8601 |

**200 响应**

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "list": [
      {
        "id": 9001,
        "operator_id": 1,
        "operator_username": "admin_demo",
        "target_type": "claim",
        "target_id": 4001,
        "action": "claim.review",
        "detail": { "action": "approve", "comment_length": 18 },
        "request_id": "req_7f3a9c21b0d4",
        "created_at": "2026-09-28T16:40:00+08:00"
      }
    ],
    "pagination": { "page": 1, "page_size": 20, "total": 1, "total_pages": 1 }
  },
  "request_id": "req_7f3a9c21b0d4"
}
```

**红线**：`detail` 必须已脱敏——不得出现联系方式明文、隐藏特征明文、密钥、`Authorization` 头。

---

### 3.14 AI 属性提取（同步接口，供发布后异步回填）

```http
POST /api/v1/items/{item_id}/extract-attributes
Authorization: Bearer <token>
```

**200 响应**：返回 4.3 定义的属性结构，并更新 `items.ai_attributes` 与 `ai_status`。MaaS 不可用时返回 `502 AI_SERVICE_ERROR`，物品本身不受影响。

---

## 4. 算法服务契约（独立服务，`/algorithm/v1`）

> **解耦要求**：后端**不得**依赖任何具体模型类名，只能通过 HTTP 客户端 `app/clients/algorithm_client.py` 调用；接口地址来自环境变量 `ALGORITHM_SERVICE_URL`。算法服务不可用时后端必须能降级到本地规则/Mock 实现，并在响应中标记 `degraded=true`。

### 4.1 匹配

```http
POST /algorithm/v1/match
```

请求：

```json
{
  "query_item": {
    "id": 1001,
    "image_url": "https://...",
    "category": "无线耳机",
    "description": "黑色无线耳机",
    "attributes": { "color": "黑色", "brand": null, "features": ["盒体右侧有划痕"] },
    "location": "图书馆三楼",
    "event_time": "2026-09-28T15:00:00+08:00"
  },
  "candidate_items": []
}
```

> `candidate_items` 为空时由算法服务侧自行调用后端内部接口或使用传入的候选集；**Baseline 约定由后端先做结构化过滤再传入候选集**，算法服务只做打分，降低耦合。

返回：

```json
{
  "model_version": "baseline-1.0",
  "results": [
    {
      "candidate_id": 2001,
      "total_score": 0.88,
      "scores": {
        "image_score": 0.81, "text_score": 0.92, "category_score": 1.0,
        "time_score": 0.95, "location_score": 1.0, "brand_model_score": 0.5
      },
      "evidence": ["物品类别一致", "颜色一致"],
      "uncertainties": ["品牌未确认"]
    }
  ]
}
```

### 4.2 属性提取

```http
POST /algorithm/v1/extract-attributes
```

必须返回符合 4.3 节 JSON Schema 的结构化结果；**输出必须经 Schema 校验**，校验失败视为服务错误。

### 4.3 可信度

```http
POST /algorithm/v1/claim-credibility
```

输入匹配结果、验证问题、用户答案摘要，输出可信度分与风险等级。响应结构与 3.9 的 `verification_score` / `credibility_score` / `risk_level` / `per_question` 保持一致。

### 4.4 Mock 硬性要求

真实模型未就绪时，算法服务必须提供 Mock 实现，且满足：

1. 后端可完整联调；
2. 前端不等待算法开发；
3. 测试可**重复运行**（确定性：同一输入必须得到同一输出，禁止随机数/时间戳影响结果）；
4. Mock 与真实接口**使用完全相同的 JSON 契约**，切换只改配置不改代码。

---

## 5. 缓存契约

| 用途 | Key 模板 | TTL | 失效触发 |
|---|---|---|---|
| AI 属性缓存 | `ai:attributes:item:{item_id}:{content_hash}` | 24h | 物品描述/图片变更 |
| 匹配结果缓存 | `match:item:{item_id}:{version}` | 10min | 物品信息或状态更新、审核完成 |
| 认领会话 | `claim:session:{claim_id}` | 30min | 验证提交完成 / 超时 |

**要求**

- 数据库是**最终事实源**，Redis 仅加速；
- Redis 不可用时**自动降级为直连数据库**（`degraded=true`），业务不中断、服务不崩溃；
- 物品描述、图片或状态变化时**必须**清理相关缓存；
- **不得**将隐藏答案明文长期写入 Redis（只存加盐哈希与元数据）。

---

## 6. 请求头与响应头

| 头 | 方向 | 说明 |
|---|---|---|
| `X-Request-ID` | 双向 | 每个请求唯一，贯穿日志、`audit_logs`、错误响应 |
| `Authorization` | 请求 | `Bearer <token>`；**禁止写入日志** |
| `X-Idempotency-Key` | 请求 | 发布、认领、审核防重复提交 |
| `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset` | 响应 | 限流信息 |
| `X-Model-Version` | 响应 | 匹配相关接口返回算法版本，便于复现 |

---

## 附录 A · 接口清单速查（与需求说明书 6.4 对齐）

| # | 方法 | 路径 | 鉴权 | 对应验收项 |
|---|---|---|---|---|
| 1 | GET | `/health` | 公开 | F13 |
| 2 | GET | `/health/live`、`/health/ready` | 公开 | 部署探针 |
| 3 | POST | `/api/v1/auth/login` | 公开 | — |
| 4 | GET | `/api/v1/auth/me` | 登录 | — |
| 5 | POST | `/api/v1/auth/demo-login` | 公开（仅 demo） | 答辩演示 |
| 6 | POST | `/api/v1/items` | 登录 | F01/F02/F03/F04 |
| 7 | GET | `/api/v1/items` | 公开 | F07 |
| 8 | GET | `/api/v1/items/{item_id}` | 公开（脱敏） | F07 |
| 9 | POST | `/api/v1/items/{item_id}/extract-attributes` | 登录 | F04 |
| 10 | GET | `/api/v1/items/{item_id}/matches` | 公开（脱敏） | F05/F06 |
| 11 | POST | `/api/v1/items/{item_id}/match` | 登录 | F05 |
| 12 | POST | `/api/v1/claims` | 登录 | F08 |
| 13 | POST | `/api/v1/claims/{claim_id}/verify` | 登录 | F09 |
| 14 | GET | `/api/v1/me/items` | 登录 | — |
| 15 | GET | `/api/v1/me/claims` | 登录 | — |
| 15a | GET | `/api/v1/claims/{claim_id}` | 登录（本人或管理员） | F08 |
| 15b | GET | `/api/v1/admin/claims` | 管理员 | F10（审核台待办列表） |
| 16 | GET | `/api/v1/admin/claims/{claim_id}` | 管理员 | F10 |
| 17 | POST | `/api/v1/admin/claims/{claim_id}/review` | 管理员 | F10/F11/F12 |
| 18 | GET | `/api/v1/admin/audit-logs` | 管理员 | F12 |
| 19 | POST | `/algorithm/v1/match` | 内网 | F05 |
| 20 | POST | `/algorithm/v1/extract-attributes` | 内网 | F04 |
| 21 | POST | `/algorithm/v1/claim-credibility` | 内网 | F09 |

## 附录 B · 状态流转图（与 2.3 一致）

```text
                   ┌──────────────┐
                   │  published   │
                   └──────┬───────┘
                          │ 计算匹配
                   ┌──────▼───────┐
       撤回 ┌──────│   matched    │
            │      └──────┬───────┘
            │             │ 发起认领
     ┌──────▼───────┐     │
     │   claiming   │◄────┘
     └──────┬───────┘
            │ 提交验证
     ┌──────▼────────┐
     │ pending_review│
     └──┬───┬────┬───┘
        │   │    │
 approve│   │reject│request_more_info
        │   │    │
  ┌─────▼┐ ┌▼───────┐ ┌──────────────────┐
  │claimed│ │rejected│ │request_more_info │
  └───┬───┘ └───┬────┘ └────────┬─────────┘
      │         │               │ 补充后
      │         │               └──► pending_review
      └─────────┴──► closed（终态）

任意状态（管理员）→ suspended / disputed
```

## 附录 C · 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 影响范围 | 破坏兼容 |
|---|---|---|---|---|---|
| 1.0 | 2026-09-30 | M0 需求冻结会议 | 首次冻结：通用约定、枚举字典、21 个接口、算法契约、缓存契约 | 前端 / 后端 / 算法 / 测试 | — |
| 1.0.1 | 2026-09-30 | 前端实现评审 | **补充 2 个接口**：`GET /api/v1/claims/{claim_id}`（认领详情，供认领页刷新恢复，替代把答案草稿留在浏览器）、`GET /api/v1/admin/claims`（待审核列表，审核台必需，含 `counts` 统计）；新增 `sort_by=risk_level` 高风险优先排序约定 | 后端（新增 2 个路由）、前端（移除 sessionStorage 降级方案） | 否（纯新增） |
| 1.0.2 | 2026-09-30 | 后端实现评审 | 明确 `attribute_score`（后端）与 `text_score`（算法）为同一维度的映射关系，见 §3.6 | 后端映射层 | 否 |

> 后续每次变更在本表追加一行。破坏性变更需三方（甲方 / 前端 / 后端）确认后生效，并同步更新 `docs/database-design.md` 与 OpenAPI。
