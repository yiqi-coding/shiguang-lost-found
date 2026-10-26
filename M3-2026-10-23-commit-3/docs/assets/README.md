# docs/assets —— 文档配图

## 这个目录放什么

文档中引用的截图与配图。

| 用途 | 命名约定 | 引用位置 |
|---|---|---|
| CodeArts 使用记录截图 | `codearts-001.png` ~ `codearts-00N.png` | [../codearts-usage.md](../codearts-usage.md) §6 |
| 云端资源截图 | `cloud-rds.png`、`cloud-obs.png`、`cloud-redis.png`、`cloud-ecs.png` | [../cloud-resources.md](../cloud-resources.md) |
| 系统界面截图 | `ui-{页面}.png`，如 `ui-match-result.png` | [../test-report.md](../test-report.md)、演示材料 |
| 架构与流程图 | `arch-overview.png`、`flow-claim.png` | [../algorithm.md](../algorithm.md)、答辩 PPT |

## 截图前必查（安全红线）

- [ ] 无 AK/SK、Token、数据库密码、完整连接串（含密码段）
- [ ] 无隐藏特征明文、无认领标准答案
- [ ] 无真实个人信息（姓名、学号、手机号、人脸）
- [ ] 控制台配置界面中的实例 ID / 账号 ID 已视需要打码
- [ ] 浏览器地址栏不含带签名参数的 OBS URL（会泄露访问凭证）

> 违反任一项的截图**不得**进入仓库、文档或答辩材料。

## 状态

⏳ **待填充**。M0 阶段未产生截图；M1 起按上述约定补充。
