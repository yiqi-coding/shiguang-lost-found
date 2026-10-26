"""测试路径引导（自包含）：把 ``algorithm/`` 根目录加入 ``sys.path``。

为什么需要：`python -m unittest discover -s tests` 会把 **tests 目录**放进
``sys.path``，测试模块因此以顶层模块身份导入，``import app`` 会失败。
各测试文件在导入 ``app.*`` 之前会 import 本模块完成引导。

处理方式（两种 unittest 发现命令都验证通过）::

    python -m unittest discover -s tests -t .      # 推荐：tests 是包
    python -m unittest discover -s tests           # tests/ 在 sys.path 上
    python -m unittest discover                    # 在 algorithm/ 目录下运行
"""

from __future__ import annotations

import os
import sys

#: 仓库根目录标记：这些文件/目录同时存在即认为当前目录就是 algorithm/
MARKERS = ("app", "config", "data", "tests")


def _looks_like_root(path: str) -> bool:
    return all(os.path.exists(os.path.join(path, marker)) for marker in MARKERS)


def ensure_project_root() -> str:
    """向上查找并加入 algorithm/ 根目录；返回该路径。重复调用安全（幂等）。"""
    here = os.path.dirname(os.path.abspath(__file__))          # algorithm/tests
    candidates = [os.path.dirname(here), os.getcwd(), os.path.dirname(os.getcwd())]
    for candidate in candidates:
        if _looks_like_root(candidate):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    parent = os.path.dirname(here)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return parent


PROJECT_ROOT = ensure_project_root()

__all__ = ["PROJECT_ROOT", "ensure_project_root"]
