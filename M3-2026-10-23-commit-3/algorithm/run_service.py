"""在仓库根目录直接启动算法服务的入口脚本。

用法（在 ``E:\\ICT\\algorithm`` 下，或在任意目录用绝对路径）::

    python run_service.py
    python run_service.py --host 0.0.0.0 --port 8080

存在的原因：部分 Python 环境开启了 safe-path，``python -m app.main`` 不会把当前
目录加入 ``sys.path``。本脚本会先把 ``algorithm/`` 根目录加入 ``sys.path``，
再调用 ``app.main``，因此在任何环境下都能启动。

> 若环境里没有安装 fastapi，``app.main`` 会自动改用标准库 ``http.server``，
> 端点与返回结构与 FastAPI 版完全一致。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    from app.main import main as serve
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
