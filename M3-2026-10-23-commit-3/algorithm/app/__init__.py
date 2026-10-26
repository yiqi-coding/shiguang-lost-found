"""拾光 · 算法服务应用包。

包含：
- `config_loader`：唯一配置源加载（config/*.yaml），不可读时回退内置默认值
- `matcher`：多源融合评分（六维加权）
- `extractor`：物品属性提取（规则实现 + 可插拔真实模型）
- `credibility`：认领可信度评估与风险等级
- `locations`：校园地点层级解析
- `text_utils`：中文文本归一化与相似度工具
- `mock_data`：确定性脱敏演示数据集
- `schemas`：请求/响应契约模型
- `main`：FastAPI 应用（三个算法端点 + 健康检查）

⚠ 本包**不得**依赖 backend 包；与后端的耦合只通过 HTTP JSON 契约。
"""

from __future__ import annotations

__version__ = "1.0.0"
MODEL_VERSION = "baseline-1.0"

#: 对外导出的子模块（`from app import matcher` 等）
__all__ = [
    "__version__",
    "MODEL_VERSION",
    "config_loader",
    "text_utils",
    "locations",
    "extractor",
    "matcher",
    "credibility",
    "mock_data",
    "schemas",
]
