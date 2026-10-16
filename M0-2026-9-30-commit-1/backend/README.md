# 拾光 · 后端服务（FastAPI）

> 校园多模态失物检索与可信认领平台 —— Baseline v1.0 后端。
> 权威契约：[`docs/api-contract.md`](../docs/api-contract.md)、[`docs/database-design.md`](../docs/database-design.md)。

## 1. 目录结构

```text
backend/
├── alembic.ini                     # Alembic 配置（连接串来自环境变量，不写死）
├── alembic/
│   ├── env.py                      # 读取 settings.DATABASE_URL + Base.metadata
│   ├── script.py.mako
│   └── versions/0001_initial_schema.py   # 与 docs/database-design.md §4 / scripts/init_db.sql 对齐
├── app/
│   ├── main.py                     # 应用装配：中间件、异常处理器、静态资源、启动校验
│   ├── api/
│   │   ├── deps.py                 # 认证 / 权限 / 客户端 IP / 限流
│   │   └── v1/                     # auth items matches claims me admin health + __init__ 汇总
│   ├── schemas/                    # Pydantic 校验层（common auth item match claim admin health）
│   ├── services/                   # 业务编排层（含冻结的 state_machine/scoring/serializers）
│   ├── models/ + db/               # SQLAlchemy 2.x 模型与会话
│   ├── clients/                    # 算法服务 / MaaS HTTP 客户端
│   └── core/                       # 配置、错误码、统一响应、日志、安全工具
└── tests/                          # unittest 测试（纯逻辑，不连库、不启服务）
```

## 2. 本地运行

```bash
# 1) 安装依赖（完整运行**必须**先执行这一步）
python -m pip install -r requirements.txt

# 2) 建表（二选一）
mysql -h 127.0.0.1 -u root -p < ../scripts/init_db.sql
cd backend && alembic upgrade head

# 3) 启动（配置全部来自环境变量 / .env，见仓库根目录 .env.example）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后：`/docs`（OpenAPI）、`/health`（综合体检）、`/health/live`、`/health/ready`。

## 3. 运行测试

```bash
cd backend
python -m unittest discover -s tests -v
```

### 关于「未安装第三方依赖」的说明

本仓库交付时，运行环境**可能没有安装** `fastapi / sqlalchemy / pydantic-settings / PyJWT / passlib`
（离线、无 MySQL/Redis）。为此 `tests/_stubs.py` 会**仅为缺失的依赖**注入最小替身：

| 替身 | 行为 | 影响面 |
|---|---|---|
| `pydantic-settings` | 从环境变量读取配置，与真实实现语义一致 | 无（纯逻辑等价） |
| `sqlalchemy` | 仅让模型类可定义、对象可实例化，**不执行任何 SQL** | 无（测试本就不连库） |
| `fastapi` / `starlette` | 记录路由表，用于断言「路径/方法与契约逐字一致」 | 无 |
| `PyJWT` | `hmac`+`hashlib` 实现 HS256 | 无（仅测试 `decode_access_token` 之外的工具） |
| `passlib` | PBKDF2-HMAC-SHA256 模拟 bcrypt 语义（随机盐 + 常量时间比对） | 真实环境仍走 bcrypt |

因此：

- **只装标准库时**：`python -m unittest discover -s tests -v` 可以全绿（纯逻辑测试）；
- **完整运行**（真实 ORM 模型、真实路由、端到端联调）：请先 `pip install -r requirements.txt`。
  安装真实依赖后，打桩逻辑自动跳过，测试跑在真实库上。

## 4. 关键安全约定（改动代码前务必阅读）

1. **字段级权限唯一收口点**：`app/services/serializers.py`。
   任何面向接口的物品/认领输出都必须经过它，**禁止**在路由里手写 dict 拼装。
2. **状态变更唯一入口**：`app/services/state_machine.py`。
   禁止出现 `item.status = "claimed"` 这类散写（`claimed` 只能由管理员审核产生）。
3. **答案处理**：标准答案只能用 `core.security.hash_answer()` / `verify_answer()`；
   任何响应与日志都不得回显答案原文，`answer_summary.reason` 只能是模糊理由。
4. **联系方式**：`contact_id` 在 Pydantic 校验阶段就调用 `mask_contact()` 脱敏后入库。
5. **审计**：管理员操作必须在同一事务内写入 `audit_logs`（含 `request_id`），
   详情经 `audit_service.sanitize_detail()` 脱敏。
6. **降级**：AI 失败不阻断发布；存储失败仅阻断「含图片」的发布；Redis 不可用时限流直接放行。

## 5. 已实现接口（契约附录 A + §3.10.1 / §3.10.2）

| 方法 | 路径 | 鉴权 |
|---|---|---|
| GET | `/health`、`/health/live`、`/health/ready` | 公开 |
| POST | `/api/v1/auth/login` | 公开（5 次/分/IP） |
| GET | `/api/v1/auth/me` | 登录 |
| POST | `/api/v1/auth/demo-login` | 公开（仅非生产环境，生产 404） |
| POST | `/api/v1/items` | 登录（201） |
| GET | `/api/v1/items` | 公开（脱敏） |
| GET | `/api/v1/items/{item_id}` | 公开（按角色脱敏） |
| POST | `/api/v1/items/{item_id}/extract-attributes` | 登录（本人/管理员，30 次/时） |
| GET | `/api/v1/items/{item_id}/matches` | 公开（脱敏） |
| POST | `/api/v1/items/{item_id}/match` | 登录（本人/管理员） |
| POST | `/api/v1/claims` | 登录（拾物方/管理员，10 次/时，201） |
| GET | `/api/v1/claims/{claim_id}` | 登录（本人/管理员） |
| POST | `/api/v1/claims/{claim_id}/verify` | 登录（本人/管理员） |
| GET | `/api/v1/me/items`、`/api/v1/me/claims` | 登录 |
| GET | `/api/v1/admin/claims` | 管理员 |
| GET | `/api/v1/admin/claims/{claim_id}` | 管理员 |
| POST | `/api/v1/admin/claims/{claim_id}/review` | 管理员 |
| GET | `/api/v1/admin/audit-logs` | 管理员 |

路由表由 `tests/test_routes_contract.py` 与契约逐条断言，新增/删除接口会立刻暴露。
