# data/images —— 演示图片素材

## 这个目录放什么

存放演示与答辩用的图片素材（物品照片、界面截图等的原图）。

## 为什么默认不提交图片

1. **体积**：`.gitignore` 已排除本目录下的图片（`data/images/*` 仅保留本 README），避免仓库膨胀；
2. **版权与隐私**：图片是最容易夹带真实个人信息的载体；
3. **可替代性**：演示数据集的物品图片使用**占位 URL**
   （`https://cdn.example-campus.invalid/demo/...`，`.invalid` 为保留域名，永远不会解析成功），
   前端会自动降级为类别占位图，不影响功能演示。

## 使用约定

| 项 | 要求 |
|---|---|
| 格式 | JPG / PNG / WEBP（与上传限制一致，见 [../../docs/api-contract.md](../../docs/api-contract.md) §3.3） |
| 单张大小 | ≤ 10 MB |
| 命名 | `{category}-{post_type}-{序号}.jpg`，如 `earbuds-lost-01.jpg` |
| 来源 | 自绘、无版权素材库，或完全虚构的合成图 |
| 禁止 | 真实他人照片、可识别的人脸、带真实姓名/学号/手机号的证件照片 |

## 交接方式

演示图片**不通过 Git 交付**，改为：

1. 打包为独立压缩包随交付物提交，或
2. 上传至 OBS 并在 [../../docs/cloud-resources.md](../../docs/cloud-resources.md) 中记录对象前缀与访问方式。

无论哪种方式，都必须在 `docs/test-report.md` 中说明图片来源与授权情况（需求 §9.1）。
