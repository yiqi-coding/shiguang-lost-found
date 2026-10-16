# frontend/scripts —— 前端构建辅助脚本

## 这个目录放什么

前端开发期的辅助脚本（构建、检查、数据生成），与 `package.json` 的 `scripts` 配合使用。

| 计划脚本 | 用途 | 状态 |
|---|---|---|
| `check-api-contract.mjs` | 从 `docs/api-contract.md` 提取接口清单，与 `src/api/*.ts` 比对，防止契约漂移 | ⏳ M1 |
| `gen-demo-fixtures.mjs` | 由 `algorithm/data/*.json` 生成前端 Mock 联调数据 | ⏳ M1 |
| `check-forbidden-words.mjs` | 扫描源码，拦截禁用词（`确认失主`、`自动归还`、`已确权` 等，见 [../../docs/prototypes.md](../../docs/prototypes.md) §12） | ⏳ M1 |

> 当前前端骨架**尚未**包含这些脚本，M1 阶段补齐。
>
> 契约漂移检查（第一个脚本）优先级最高：本项目已冻结 `docs/api-contract.md`，
> 前后端字段不一致是需求 §18 明确列出的风险项。
