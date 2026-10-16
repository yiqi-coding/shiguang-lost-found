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
# 推荐：一键脚本（自动处理解释器与依赖路径）
python scripts/run_backend_tests.py
python scripts/run_backend_tests.py -v                 # 详细输出
python scripts/run_backend_tests.py -k test_scoring    # 只跑匹配用例

# 或手工指定（等价）
cd backend
python -m unittest discover -s tests -v
```

### 测试必须运行在**真实依赖**下（重要，勿回退）

> **本项目的测试已废弃「依赖替身」方案。**
>
> 早期版本用 `tests/_stubs.py` 注入自制的假 fastapi / sqlalchemy / pydantic，
> 在没有第三方依赖的机器上也能得到「237 个测试全绿」。但拿到真实依赖
> （FastAPI 0.143 / Starlette 1.3 / SQLAlchemy 2.0.51 / Pydantic 2.13）复跑时
> **暴露出 25 个失败**：
>
> | 被替身掩盖的真实缺陷 | 后果 |
> |---|---|
> | 新版 FastAPI 把 `include_router()` 的子路由包进 `_IncludedRouter` | 路由契约测试完全失效，「接口全部存在」是假象 |
> | `passlib` 1.7.4 无法驱动 `bcrypt` 5.0（`bcrypt.__about__` 被移除） | **密码哈希直接抛异常 → 登录接口 500**，而替身环境下测试是绿的 |
> | Starlette 1.x 移除 `Response.content`、`user_middleware` 元素结构变化 | 相关测试在真依赖下报 AttributeError / 解包错误 |
>
> **结论：替身制造的确定性是假的，代价是掩盖真实缺陷。**
> 现在 `tests/conftest.py` 会**强制校验真实依赖**，缺失时直接失败并给出获取指引，
> 而不是静默退回替身。

### 准备依赖（本机 pip 受限时）

本机 `pip install` 有两个障碍：默认索引源不可达、pip 临时目录与用户级
site-packages 位于工作区外被沙箱拒绝。改用解包方式（免权限提升）：

```powershell
python scripts/vendor_deps.py                          # 解包到 .vendor/（含传递依赖）
$env:PYTHONPATH = "E:\ICT\.vendor;E:\ICT\backend"
cd backend; python -m unittest discover -s tests
```

`.vendor/` 已在 `.gitignore` 中排除，不会进入版本库或交付压缩包。
其他环境可直接 `pip install -r requirements.txt`，或 `docker compose run --rm backend python -m unittest discover -s tests`。

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
