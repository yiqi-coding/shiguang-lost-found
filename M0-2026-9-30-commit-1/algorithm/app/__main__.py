"""``python -m app`` 入口：等价于 ``python app/main.py``。

有些 Python 发行版/环境开启了 safe-path（``python -c`` 与 ``python -m`` 不把当前
目录加入 ``sys.path``），此时 ``python -m app.main`` 会报 ``No module named 'app'``。
本模块用 ``runpy.run_path`` 执行 ``app/main.py``——``run_path`` 会把该脚本所在目录
加入 ``sys.path``，因此下面两种写法都能稳定启动服务：

    python -m app
    python app/main.py
"""

from __future__ import annotations

import os
import runpy
import sys

_MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

if __name__ == "__main__":
    sys.argv = [ _MAIN ] + sys.argv[1:]
    runpy.run_path(_MAIN, run_name="__main__")
