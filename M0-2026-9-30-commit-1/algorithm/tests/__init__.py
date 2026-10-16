"""tests 包：标准库 unittest 用例。

运行方式（三种都已验证）::

    cd E:\\ICT\\algorithm
    python -m unittest discover -s tests -t . -v      # 推荐
    python -m unittest discover -s tests              # 亦可（见 tests/_bootstrap.py）
    python -m unittest discover                       # 在 algorithm/ 目录下直接运行

路径引导由 ``tests/_bootstrap.py`` 负责：它会把 ``algorithm/`` 根目录加入
``sys.path``，因此无论 unittest 以「包内模块」还是「顶层模块」方式导入用例，
``import app`` 都能成功。
"""

from __future__ import annotations

from ._bootstrap import PROJECT_ROOT, ensure_project_root

ensure_project_root()

__all__ = ["PROJECT_ROOT", "ensure_project_root"]
