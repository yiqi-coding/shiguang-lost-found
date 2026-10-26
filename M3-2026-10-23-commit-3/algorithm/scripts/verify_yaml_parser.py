"""校验内置极简 YAML 解析器与 PyYAML 的解析结果一致。

用途：``python scripts/verify_yaml_parser.py``
（PyYAML 不在环境里时，本脚本会跳过对比，仅打印降级解析器的摘要。）
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.config_loader import CONFIG_DIR, _parse_minimal_yaml  # noqa: E402

FILES = ("matching.yaml", "locations.yaml", "questions.yaml")


def _diff(a, b, path="$"):
    """返回两组结构的差异描述列表（空列表表示完全一致）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{path}.{key}: 仅 pyyaml 有")
            elif key not in b:
                out.append(f"{path}.{key}: 仅 minimal 有")
            else:
                out.extend(_diff(a[key], b[key], f"{path}.{key}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: 列表长度不同 pyyaml={len(a)} minimal={len(b)}"]
        out = []
        for idx, (x, y) in enumerate(zip(a, b)):
            out.extend(_diff(x, y, f"{path}[{idx}]"))
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return [] if abs(float(a) - float(b)) < 1e-12 else [f"{path}: 数值不同 pyyaml={a!r} minimal={b!r}"]
    return [] if a == b else [f"{path}: pyyaml={a!r} minimal={b!r}"]


def main() -> int:
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None  # type: ignore

    failures = 0
    for name in FILES:
        path = os.path.join(CONFIG_DIR, name)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        try:
            minimal = _parse_minimal_yaml(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {name}: 极简解析器抛异常 {exc.__class__.__name__}: {exc}")
            failures += 1
            continue
        if yaml is None:
            print(f"[SKIP] {name}: 未安装 PyYAML，仅确认极简解析器可用（顶层键 {len(minimal)} 个）")
            continue
        reference = yaml.safe_load(text)
        diffs = _diff(reference, minimal)
        if diffs:
            failures += 1
            print(f"[FAIL] {name}: {len(diffs)} 处差异")
            for item in diffs[:15]:
                print("        " + item)
        else:
            print(f"[OK]   {name}: 极简解析器与 PyYAML 结果一致（顶层键 {len(minimal)} 个）")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
