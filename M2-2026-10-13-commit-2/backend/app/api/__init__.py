"""API 层包。

- `app/api/deps.py`：认证 / 权限 / 客户端 IP / 限流依赖；
- `app/api/v1/`：按契约 §3 划分的路由模块，最后由 `v1/__init__.py` 汇总。
"""

from __future__ import annotations

__all__ = ["deps"]
