"""确定性文本工具：归一化、字符 n-gram、Dice/Jaccard 相似度、模糊取值。

本模块**只用标准库**，且全部是纯函数（相同输入 → 相同输出）。
matcher.py / extractor.py / credibility.py / locations.py 共用这里的实现，
保证「颜色」「品牌」「地点」「答案」等判定口径一致。
"""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# 需要剥离的符号（中英文标点 + 常见全角符号）
_PUNCT_EXTRA = set("　`~!@#$%^&*()_-+=|\\}{[]'\";:/?.,<>！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～、。〃〈〉《》「」『』【】〔〕〖〗〝〞–—‘’‛“”„‟…‧﹏·・")

# 数字中文映射（楼层解析用）
CN_NUMERALS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def to_halfwidth(text: str) -> str:
    """全角 → 半角（主要处理 ASCII 可见字符与全角空格）。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def is_punctuation(ch: str) -> bool:
    if ch in _PUNCT_EXTRA:
        return True
    return unicodedata.category(ch).startswith("P")


def normalize(
    text: Any,
    *,
    fullwidth_to_halfwidth: bool = True,
    lowercase: bool = True,
    strip_whitespace: bool = True,
    strip_punctuation: bool = True,
    nfkc_form: bool = False,
    max_length: Optional[int] = None,
) -> str:
    """按配置归一化字符串；非字符串输入一律安全转成字符串（None → ""）。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if nfkc_form:
        text = nfkc(text)
    if fullwidth_to_halfwidth:
        text = to_halfwidth(text)
    if lowercase:
        text = text.lower()
    if strip_punctuation:
        text = "".join(ch for ch in text if not is_punctuation(ch))
    if strip_whitespace:
        text = "".join(ch for ch in text if not ch.isspace())
    else:
        text = " ".join(text.split())
    if max_length is not None and max_length > 0:
        text = text[:max_length]
    return text


def normalize_key(text: Any) -> str:
    """关键字段（蓝牙名称、学号等）的强归一化：NFKC + 全角转半角 + 去符号 + 小写。"""
    return normalize(text, nfkc_form=True, strip_punctuation=True, strip_whitespace=True, lowercase=True)


def ngrams(text: str, size: int) -> List[str]:
    """字符 n-gram；文本短于 n 时退化为整串，空串返回空列表。"""
    if not text:
        return []
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(len(text) - size + 1)]


def ngram_multiset(text: str, sizes: Sequence[int]) -> Dict[str, int]:
    bag: Dict[str, int] = {}
    for size in sizes:
        for gram in ngrams(text, int(size)):
            bag[gram] = bag.get(gram, 0) + 1
    return bag


def _counter(values: Iterable[str]) -> Dict[str, int]:
    bag: Dict[str, int] = {}
    for value in values:
        bag[value] = bag.get(value, 0) + 1
    return bag


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def dice(a: Iterable[str], b: Iterable[str]) -> float:
    """Dice 系数：2|A∩B| / (|A|+|B|)，支持多重集。"""
    bag_a, bag_b = _counter(a), _counter(b)
    if not bag_a and not bag_b:
        return 0.0
    overlap = sum(min(count, bag_b.get(gram, 0)) for gram, count in bag_a.items())
    total = sum(bag_a.values()) + sum(bag_b.values())
    if total == 0:
        return 0.0
    return 2.0 * overlap / total


def ngram_similarity(
    text_a: str,
    text_b: str,
    sizes: Sequence[int],
    method: str = "dice",
) -> float:
    """归一化后的字符 n-gram 相似度（0~1）。"""
    va, vb = ngram_multiset(text_a, sizes), ngram_multiset(text_b, sizes)
    if not va or not vb:
        return 0.0
    if method == "jaccard":
        set_a = {g for g, c in va.items() for _ in range(c)}
        set_b = {g for g, c in vb.items() for _ in range(c)}
        return jaccard(set_a, set_b)
    return dice(
        [g for g, c in va.items() for _ in range(c)],
        [g for g, c in vb.items() for _ in range(c)],
    )


def token_jaccard(tokens_a: Iterable[str], tokens_b: Iterable[str]) -> float:
    return jaccard(tokens_a, tokens_b)


def token_dice(tokens_a: Iterable[str], tokens_b: Iterable[str]) -> float:
    return dice(tokens_a, tokens_b)


def cn_number(token: str) -> Optional[int]:
    """把「三」「3」「十二」转成整数；失败返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = CN_NUMERALS.get(head, 1) if head else 1
        ones = CN_NUMERALS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(token) == 1 and token in CN_NUMERALS:
        value = CN_NUMERALS[token]
        return value if value > 0 else None
    return None


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, dict)) and len(value) == 0:
            continue
        return value
    return None


def as_text_list(value: Any) -> List[str]:
    """把任意输入安全地变成字符串列表（用于 features / keywords 字段）。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("；", ";").replace("，", ",").replace("、", ",").split(";")]
        result: List[str] = []
        for part in parts:
            result.extend([p.strip() for p in part.split(",") if p.strip()])
        return result
    if isinstance(value, dict):
        out: List[str] = []
        for key, item in value.items():
            if isinstance(item, (list, tuple)):
                out.extend([str(x).strip() for x in item if str(x).strip()])
            elif item:
                out.append(f"{key}{item}")
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                if item.strip():
                    out.append(item.strip())
            else:
                out.append(str(item))
        return out
    return [str(value)]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result or result in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return result


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def round_score(value: float, digits: int = 4) -> float:
    return round(clamp(value), digits)


__all__ = [
    "CN_NUMERALS",
    "nfkc",
    "to_halfwidth",
    "is_punctuation",
    "normalize",
    "normalize_key",
    "ngrams",
    "ngram_multiset",
    "jaccard",
    "dice",
    "ngram_similarity",
    "token_jaccard",
    "token_dice",
    "cn_number",
    "first_non_empty",
    "as_text_list",
    "safe_float",
    "clamp",
    "round_score",
]
