"""认领可信度评分。

::

    可信度分 = 0.60×隐藏特征答案匹配 + 0.20×候选物品综合匹配分
             + 0.10×发布信息完整度 + 0.10×行为/风险规则分

权重、阈值、同义词组、行为规则全部来自 ``config/questions.yaml``。

安全约束（硬性）
----------------
* **绝不返回标准答案**：响应里只有 ``question_id`` / ``matched`` / ``score`` /
  ``reason``，理由文本由固定模板生成，不回显答案原文，也不回显标准答案。
* 答案匹配容忍合理同义表达（「划痕」vs「刮痕」、「右侧」vs「右边」）；
  关键字段（蓝牙名称、学号等）走精确/规范化匹配（去空格、统一大小写、全角转半角）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config_loader import RuntimeConfig, load_config
from .text_utils import clamp, normalize, normalize_key, ngram_similarity, safe_float

# ---------------------------------------------------------------------------
# 文案模板：一律不含答案内容
# ---------------------------------------------------------------------------
_REASON_TEMPLATES = {
    "full_match": "回答与登记信息一致",
    "partial_match": "回答与登记信息部分一致，关键细节仍需核对",
    "mismatch": "回答与登记信息不一致",
    "unanswered": "该问题未作答或答案过短，无法验证",
    "field_full": "关键字段规范化后完全一致",
    "field_partial": "关键字段部分一致，需人工复核",
    "field_mismatch": "关键字段不一致",
    "error": "该问题处理异常，已按未命中处理",
}


def _field_value(payload: Optional[Dict[str, Any]], keys: Sequence[str], default: Any = None) -> Any:
    """从扁平 dict / 嵌套 dict（attributes、answer、ai_attributes）里取值。"""
    if not isinstance(payload, dict):
        return default
    for key in keys:
        if key in payload and payload[key] not in (None, "", [], {}):
            return payload[key]
    for nested in ("attributes", "answer", "ai_attributes", "item", "target_item", "candidate"):
        inner = payload.get(nested)
        if isinstance(inner, dict):
            value = _field_value(inner, keys, None)
            if value not in (None, "", [], {}):
                return value
    return default


@dataclass
class RiskTip:
    code: str
    text: str
    penalty: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "text": self.text, "penalty": self.penalty}


@dataclass
class CredibilityResult:
    credibility_score: float
    risk_level: str
    per_question: List[Dict[str, Any]] = field(default_factory=list)
    risk_tips: List[str] = field(default_factory=list)
    breakdown: Dict[str, float] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)
    answer_match_score: float = 0.0
    behavior_score: float = 0.0
    completeness_score: float = 0.0
    candidate_match_score: float = 0.0
    model_version: str = "baseline-1.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "credibility_score": round(self.credibility_score, 4),
            "risk_level": self.risk_level,
            "per_question": [dict(item) for item in self.per_question],
            "risk_tips": list(self.risk_tips),
            "breakdown": {key: round(value, 4) for key, value in self.breakdown.items()},
        }


class CredibilityScorer:
    """隐藏特征问答 → 可信度分与风险等级。"""

    def __init__(self, config: Optional[RuntimeConfig] = None) -> None:
        self.config = config or load_config()
        data = self.config.questions or {}
        weights = data.get("weights") or {}
        self.weights = {
            "answer_match": float(weights.get("answer_match", 0.60)),
            "candidate_match": float(weights.get("candidate_match", 0.20)),
            "completeness": float(weights.get("completeness", 0.10)),
            "behavior": float(weights.get("behavior", 0.10)),
        }
        neutrals = data.get("neutral_scores") or {}
        self.neutral = {
            "answer_match": float(neutrals.get("answer_match", 0.50)),
            "candidate_match": float(neutrals.get("candidate_match", 0.50)),
            "completeness": float(neutrals.get("completeness", 0.30)),
            "behavior": float(neutrals.get("behavior", 0.60)),
        }
        scoring = data.get("scoring") or {}
        self.clamp_min = float(scoring.get("clamp_min", 0.0))
        self.clamp_max = float(scoring.get("clamp_max", 1.0))
        self.round_digits = int(scoring.get("round_digits", 4))
        risk = scoring.get("risk_levels") or {}
        # 兜底默认值与 docs/api-contract.md §2.6 保持一致（契约优先于本地标定值）：
        # 可信度 ≥ 0.75 → low；0.45 ~ 0.75 → medium；< 0.45 → high
        self.low_min = float(risk.get("low_min", 0.75))
        self.medium_min = float(risk.get("medium_min", 0.45))

        matching = data.get("answer_matching") or {}
        self.normalization = matching.get("normalization") or {}
        fuzzy = matching.get("fuzzy") or {}
        self.fuzzy = {
            "ngram_size": int(fuzzy.get("ngram_size", 2)),
            "confident_threshold": float(fuzzy.get("confident_threshold", 0.62)),
            "partial_threshold": float(fuzzy.get("partial_threshold", 0.34)),
            "confident_score": float(fuzzy.get("confident_score", 1.0)),
            "partial_score": float(fuzzy.get("partial_score", 0.50)),
            "below_score": float(fuzzy.get("below_score", 0.0)),
        }
        exact = matching.get("exact") or {}
        self.exact = {
            "match_score": float(exact.get("match_score", 1.0)),
            "partial_score": float(exact.get("partial_score", 0.60)),
            "mismatch_score": float(exact.get("mismatch_score", 0.0)),
            "confusion_pairs": exact.get("confusion_pairs") or [["0", "o"], ["1", "l"], ["1", "i"]],
        }
        unanswered = matching.get("unanswered") or {}
        self.unanswered = {
            "score": float(unanswered.get("score", 0.0)),
            "reason": str(unanswered.get("reason", _REASON_TEMPLATES["unanswered"])),
        }
        self.min_answer_length = int(matching.get("min_answer_length", 2))
        self.max_reason_length = int(matching.get("max_reason_length", 60))
        self.fillers = [self.normalize_text(x) for x in (matching.get("strip_fillers") or [])]
        self.fillers = sorted({x for x in self.fillers if x}, key=len, reverse=True)

        self.synonym_groups = [g for g in (data.get("synonym_groups") or []) if isinstance(g, dict)]
        self._term_to_canonical, self._canonical_terms, self._field_terms = self._build_synonym_maps(self.synonym_groups)
        self.conflict_groups = self._build_conflict_groups(matching.get("conflict_groups") or [])
        self.completeness_cfg = data.get("completeness") or {}
        self.behavior_cfg = data.get("behavior") or {}
        self.risk_tips_cfg = data.get("risk_tips") or {}

    # -- 同义词 -----------------------------------------------------------
    def _build_conflict_groups(self, groups: Sequence[Dict[str, Any]]) -> List[List[str]]:
        """编译互斥取值组：先做同义归一，再按长度倒序去重，避免「左」误伤「左侧」。"""
        compiled: List[List[str]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            terms: List[str] = []
            for raw in (group.get("terms") or []):
                key = self.canonicalize(raw)
                if key and key not in terms:
                    terms.append(key)
            if len(terms) >= 2:
                compiled.append(sorted(terms, key=len, reverse=True))
        return compiled

    def has_conflict(self, answer: Any, reference: Any) -> bool:
        """两侧是否命中同一互斥组中的**不同**取值（如「左侧」vs「右侧」、「没有」vs「有」）。

        先用 canonicalize 归一，因此「右边」与「右侧」不会互相冲突。
        """
        if not self.conflict_groups:
            return False
        text_a = self.canonicalize(answer)
        text_b = self.canonicalize(reference)
        if not text_a or not text_b:
            return False
        for group in self.conflict_groups:
            hit_a = next((term for term in group if term in text_a), None)
            hit_b = next((term for term in group if term in text_b), None)
            if hit_a and hit_b and hit_a != hit_b:
                return True
        return False

    @staticmethod
    def _build_synonym_maps(groups: Sequence[Dict[str, Any]]):
        term_map: Dict[str, str] = {}
        canonical_terms: Dict[str, List[str]] = {}
        field_terms: List[str] = []
        for group in groups:
            canonical = normalize(group.get("canonical") or "")
            terms = [normalize(t) for t in (group.get("terms") or []) if normalize(t)]
            if canonical and canonical not in terms:
                terms.append(canonical)
            for term in terms:
                term_map[term] = canonical or term
            if canonical:
                canonical_terms[canonical] = terms
            if str(group.get("match_mode") or "") in ("field_exact", "exact", "normalized_exact"):
                field_terms.extend(terms)
        field_terms.sort(key=len, reverse=True)
        return term_map, canonical_terms, field_terms

    def normalize_text(self, value: Any) -> str:
        cfg = self.normalization
        return normalize(
            value,
            fullwidth_to_halfwidth=bool(cfg.get("fullwidth_to_halfwidth", True)),
            lowercase=bool(cfg.get("lowercase", True)),
            strip_whitespace=bool(cfg.get("strip_whitespace", True)),
            strip_punctuation=bool(cfg.get("strip_punctuation", True)),
            nfkc_form=bool(cfg.get("unicodedata_nfkc", True)),
        )

    def canonicalize(self, value: Any) -> str:
        """同义词归一：把「刮痕/右边/刮了一道」这类表达映射到规范词，并抹平填充词。"""
        text = self.normalize_text(value)
        if not text:
            return ""
        # 长词优先替换，避免「划痕」被更短的词抢先切碎
        for term in sorted(self._term_to_canonical, key=len, reverse=True):
            if term and term in text:
                text = text.replace(term, self._term_to_canonical[term])
        # 抹平口语填充词（数量词/语气词），让「有一道划痕」与「刮了一道」可比
        for filler in self.fillers:
            if filler and filler in text:
                text = text.replace(filler, "")
        return text

    def _canonical_ngrams(self, text: str) -> set:
        size = max(1, self.fuzzy["ngram_size"])
        canonical = self.canonicalize(text)
        if not canonical:
            return set()
        if len(canonical) <= size:
            return {canonical}
        return {canonical[i:i + size] for i in range(len(canonical) - size + 1)}

    def _fuzzy_similarity(self, answer: str, reference: str) -> float:
        """同义词替换后的 bigram Dice + 原始 bigram Dice 取较大值。"""
        size = max(1, self.fuzzy["ngram_size"])
        canon_a, canon_b = self.canonicalize(answer), self.canonicalize(reference)
        base = ngram_similarity(self.normalize_text(answer), self.normalize_text(reference), [size], "dice")
        boosted = ngram_similarity(canon_a, canon_b, [size], "dice") if (canon_a and canon_b) else 0.0
        return max(base, boosted)

    @staticmethod
    def _confusion_normalize(text: str, pairs: Iterable[Sequence[str]]) -> str:
        result = text
        for pair in pairs:
            if len(pair) < 2:
                continue
            first, second = str(pair[0]), str(pair[1])
            result = result.replace(first, second)
        return result

    def _exact_similarity(self, answer: str, reference: str) -> float:
        """关键字段精确/规范化匹配：完全相同、互相包含、允许常见混淆字符。"""
        key_a = normalize_key(answer)
        key_b = normalize_key(reference)
        if not key_a or not key_b:
            return 0.0
        if key_a == key_b:
            return 1.0
        confused_a = self._confusion_normalize(key_a, self.exact["confusion_pairs"])
        confused_b = self._confusion_normalize(key_b, self.exact["confusion_pairs"])
        if confused_a == confused_b:
            return 1.0
        if key_a in key_b or key_b in key_a:
            return 1.0
        return 0.0

    # -- 单题评分 ---------------------------------------------------------
    def score_answer(
        self,
        question: Any = None,
        answer_summary: Any = None,
        question_id: Any = None,
        match_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        qid = str(question_id or _field_value(question, ["id", "question_id"], "q_auto"))
        try:
            answer_text = _field_value(answer_summary, ["answer", "text", "summary", "content"], answer_summary)
            if isinstance(answer_text, (list, tuple)):
                answer_text = "，".join(str(x) for x in answer_text)
            answer_norm = self.normalize_text(answer_text)

            reference = _field_value(
                question,
                ["reference_answer", "expected_answer", "standard_answer", "answer", "hidden_feature", "value"],
            )
            keywords = [str(k) for k in (_field_value(question, ["keywords", "answer_keywords"], []) or [])]
            if reference is None and not keywords:
                return self._question_result(qid, False, None, _REASON_TEMPLATES["error"],
                                            detail={"reason": "missing_reference"})
            if len(answer_norm) < self.min_answer_length:
                return self._question_result(qid, False, self.unanswered["score"],
                                            self.unanswered["reason"], detail={"reason": "unanswered"})

            mode = str(match_mode or _field_value(question, ["match_mode", "mode"], "") or "fuzzy_with_synonyms")
            references = [str(reference)] if reference else []
            references.extend(keywords)
            if not references:
                return self._question_result(qid, False, None, _REASON_TEMPLATES["error"],
                                            detail={"reason": "missing_reference"})

            best_raw, best_similarity, best_is_field = 0.0, 0.0, False
            for item in references:
                reference_norm = self.normalize_text(item)
                if not reference_norm:
                    continue
                # 精确模式：只要出现关键字段（如蓝牙名）就按字段规则判分
                field_similarity = self._exact_similarity(answer_text, item)
                if mode in ("field_exact", "exact", "normalized_exact"):
                    similarity = field_similarity
                    raw = similarity
                    is_field = True
                else:
                    fuzzy_similarity = self._fuzzy_similarity(answer_text, item)
                    if self.has_conflict(answer_text, item):
                        # 互斥取值（颜色/左右/有无…）直接判不一致，不被 n-gram 相似度掩盖
                        fuzzy_similarity = 0.0
                    if field_similarity >= 1.0 and self._looks_like_key_field(item):
                        similarity, raw, is_field = field_similarity, field_similarity, True
                    else:
                        similarity, raw, is_field = fuzzy_similarity, fuzzy_similarity, False
                if raw > best_raw:
                    best_raw, best_similarity, best_is_field = raw, similarity, is_field

            if mode in ("field_exact", "exact", "normalized_exact") or best_is_field:
                if best_similarity >= self.exact["match_score"] - 1e-9:
                    return self._question_result(qid, True, self.exact["match_score"],
                                                 _REASON_TEMPLATES["field_full"],
                                                 detail={"mode": "field_exact", "similarity": round(best_similarity, 4)})
                if best_similarity >= self.exact["partial_score"] - 1e-9:
                    return self._question_result(qid, False, self.exact["partial_score"],
                                                 _REASON_TEMPLATES["field_partial"],
                                                 detail={"mode": "field_exact", "similarity": round(best_similarity, 4)})
                return self._question_result(qid, False, self.exact["mismatch_score"],
                                             _REASON_TEMPLATES["field_mismatch"],
                                             detail={"mode": "field_exact", "similarity": round(best_similarity, 4)})

            if best_raw >= self.fuzzy["confident_threshold"]:
                return self._question_result(qid, True, self.fuzzy["confident_score"],
                                             _REASON_TEMPLATES["full_match"],
                                             detail={"mode": "fuzzy", "similarity": round(best_raw, 4)})
            if best_raw >= self.fuzzy["partial_threshold"]:
                return self._question_result(qid, False, self.fuzzy["partial_score"],
                                             _REASON_TEMPLATES["partial_match"],
                                             detail={"mode": "fuzzy", "similarity": round(best_raw, 4)})
            return self._question_result(qid, False, self.fuzzy["below_score"],
                                         _REASON_TEMPLATES["mismatch"],
                                         detail={"mode": "fuzzy", "similarity": round(best_raw, 4)})
        except Exception as exc:  # noqa: BLE001 - 单题异常不影响整体
            return self._question_result(qid, False, 0.0, _REASON_TEMPLATES["error"],
                                         detail={"error": exc.__class__.__name__})

    def _looks_like_key_field(self, reference: str) -> bool:
        """判断参考值是否像「关键字段」（含拉丁字母/数字或命中关键字段词表）。"""
        text = str(reference)
        if re.search(r"[A-Za-z0-9]", text):
            return True
        normalized = self.normalize_text(text)
        return any(term and term in normalized for term in self._field_terms)

    def _question_result(self, question_id: str, matched: bool, score: Optional[float],
                         reason: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """统一的单题结果结构；reason 由模板生成，绝不含答案原文。"""
        value = self.neutral["answer_match"] if score is None else clamp(float(score), self.clamp_min, self.clamp_max)
        safe_reason = str(reason)[:self.max_reason_length]
        result = {
            "question_id": question_id,
            "matched": bool(matched),
            "score": round(value, self.round_digits),
            "reason": safe_reason,
        }
        if detail and detail.get("reason"):  # 内部诊断码，便于排查但不外泄答案
            result["diagnostic"] = str(detail["reason"])
        return result

    # -- 分项 -------------------------------------------------------------
    def score_answers(self, questions: Sequence[Any], answers: Sequence[Any]) -> Tuple[float, List[Dict[str, Any]]]:
        """按题目权重加权平均各题得分；无题可评 → 中性分。"""
        per_question: List[Dict[str, Any]] = []
        weighted_sum = 0.0
        weight_total = 0.0
        for index, question in enumerate(questions or []):
            answer = answers[index] if index < len(answers or []) else None
            question_id = _field_value(question, ["id", "question_id"], f"q_{index + 1}")
            mode = _field_value(question, ["match_mode", "mode"], None)
            if isinstance(answer, dict):
                question_id = answer.get("question_id") or answer.get("id") or question_id
                answer_summary = answer.get("answer", answer.get("answer_summary", answer.get("text")))
                mode = answer.get("match_mode") or mode
                if answer.get("reference_answer") and not _field_value(question, ["reference_answer", "standard_answer"]):
                    question = dict(question or {})
                    question["reference_answer"] = answer["reference_answer"]
            else:
                answer_summary = answer
            result = self.score_answer(question, answer_summary, question_id=question_id, match_mode=mode)
            per_question.append(result)
            weight = safe_float(_field_value(question, ["weight"], 1.0), 1.0) or 1.0
            weighted_sum += result["score"] * weight
            weight_total += weight
        if weight_total <= 0:
            return self.neutral["answer_match"], per_question
        return clamp(weighted_sum / weight_total, self.clamp_min, self.clamp_max), per_question

    def score_candidate_match(self, candidate_match: Optional[Dict[str, Any]]) -> float:
        if not isinstance(candidate_match, dict):
            return self.neutral["candidate_match"]
        for key in ("total_score", "score", "match_score"):
            if candidate_match.get(key) is not None:
                return clamp(safe_float(candidate_match.get(key), self.neutral["candidate_match"]),
                             self.clamp_min, self.clamp_max)
        return self.neutral["candidate_match"]

    def find_item_value(self, item: Optional[Dict[str, Any]], field_name: str) -> Any:
        aliases = (self.completeness_cfg.get("fields_aliases") or {}).get(field_name) or [field_name]
        return _field_value(item, list(aliases))

    def score_completeness(self, item: Optional[Dict[str, Any]]) -> Tuple[float, List[str]]:
        fields = self.completeness_cfg.get("fields") or {}
        if not isinstance(item, dict) or not fields:
            return self.neutral["completeness"], []
        min_length = int(self.completeness_cfg.get("description_min_length", 12))
        total_weight = sum(float(w) for w in fields.values()) or 1.0
        score = 0.0
        missing_labels: List[str] = []
        labels = {
            "title": "标题", "description": "描述", "category": "类别", "color": "颜色",
            "brand": "品牌", "location": "地点", "event_time": "时间", "image_url": "图片",
            "public_features": "公开特征",
        }
        for field_name, weight in fields.items():
            value = self.find_item_value(item, str(field_name))
            present = value not in (None, "", [], {})
            # 只有「描述」需要达到最小长度才算充分；标题天然很短，非空即视为完整
            if present and field_name == "description" and isinstance(value, str):
                present = len(value.strip()) >= min_length
            if present:
                score += float(weight)
            else:
                missing_labels.append(labels.get(str(field_name), str(field_name)))
        normalized = clamp(score / total_weight, self.clamp_min, self.clamp_max)
        return normalized, missing_labels

    def score_behavior(
        self,
        behavior_signals: Optional[Dict[str, Any]] = None,
        answer_score: Optional[float] = None,
    ) -> Tuple[float, List[RiskTip], List[str]]:
        """行为/风险规则分。

        `answer_score` 会作为信号 `answer_match_score` 注入规则求值上下文，
        使「隐藏特征答案匹配分极低」这一**强负信号**能够通过可配置规则
        （questions.yaml → behavior.rules → condition: answer_match_lt）拉低行为分，
        从而让「全部答错」的申请落到 high 风险等级，而不是停在 medium。

        ⚠ 规则即使在没有外部行为信号时也要参与求值：否则「全错」这种最典型的
        冒领场景会因为缺少行为信号而取中性分，反而被抬高可信度。
        """
        base = float(self.behavior_cfg.get("base_score", 1.0))
        signals: Dict[str, Any] = dict(behavior_signals) if isinstance(behavior_signals, dict) else {}
        if answer_score is not None:
            signals.setdefault("answer_match_score", float(answer_score))

        # 既无外部行为信号、也无答案信号 → 取中性分（保持原有语义）
        if not signals:
            return self.neutral["behavior"], [], []

        score = base
        tips: List[RiskTip] = []
        failed: List[str] = []
        for rule in self.behavior_cfg.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            try:
                triggered = self._evaluate_rule(rule, signals)
            except Exception:  # noqa: BLE001 - 规则异常只记录，不影响主流程
                failed.append(str(rule.get("id", "unknown")))
                triggered = False
            if triggered:
                penalty = float(rule.get("penalty", 0.0) or 0.0)
                score -= penalty
                tips.append(RiskTip(
                    code=str(rule.get("id", "rule")),
                    text=str(rule.get("tip", "存在风险信号")),
                    penalty=penalty,
                ))
        return clamp(score, self.clamp_min, self.clamp_max), tips, failed

    @staticmethod
    def _evaluate_rule(rule: Dict[str, Any], signals: Dict[str, Any]) -> bool:
        condition = str(rule.get("condition") or "")
        threshold = rule.get("threshold")
        if condition.endswith("_lt"):
            key = condition[:-3]
            value = signals.get(key)
            return value is not None and safe_float(value, float("inf")) < safe_float(threshold, 0.0)
        if condition.endswith("_lte"):
            key = condition[:-4]
            value = signals.get(key)
            return value is not None and safe_float(value, float("inf")) <= safe_float(threshold, 0.0)
        if condition.endswith("_gte"):
            key = condition[:-4]
            value = signals.get(key)
            return value is not None and safe_float(value, float("-inf")) >= safe_float(threshold, 0.0)
        if condition.endswith("_gt"):
            key = condition[:-3]
            value = signals.get(key)
            return value is not None and safe_float(value, float("-inf")) > safe_float(threshold, 0.0)
        if condition.endswith("_is_true"):
            return bool(signals.get(condition[:-len("_is_true")]))
        if condition.endswith("_is_false"):
            key = condition[:-len("_is_false")]
            return key in signals and not bool(signals.get(key))
        return False

    def risk_level_of(self, score: float) -> str:
        if score >= self.low_min:
            return "low"
        if score >= self.medium_min:
            return "medium"
        return "high"

    # -- 主入口 -----------------------------------------------------------
    def score(
        self,
        questions: Optional[Sequence[Any]] = None,
        answers: Optional[Sequence[Any]] = None,
        candidate_match: Optional[Dict[str, Any]] = None,
        target_item: Optional[Dict[str, Any]] = None,
        behavior_signals: Optional[Dict[str, Any]] = None,
    ) -> CredibilityResult:
        """计算可信度分；任何分支异常都会降级为中性分而不是抛错。"""
        try:
            answer_score, per_question = self.score_answers(list(questions or []), list(answers or []))
        except Exception:  # noqa: BLE001
            answer_score, per_question = self.neutral["answer_match"], []
        try:
            candidate_score = self.score_candidate_match(candidate_match)
            completeness_score, missing_labels = self.score_completeness(target_item)
            # 只有「确实评判过题目」时，答案分才是风险信号。
            # 没有任何题目（per_question 为空）时 answer_score 是本项的中性分，
            # 属于「无信号」而非「答得差」，不能据此触发冒领风险规则。
            answer_signal = answer_score if per_question else None
            behavior_score, behavior_tips, failed_rules = self.score_behavior(
                behavior_signals, answer_score=answer_signal
            )
        except Exception:  # noqa: BLE001
            candidate_score = self.neutral["candidate_match"]
            completeness_score, missing_labels = self.neutral["completeness"], []
            behavior_score, behavior_tips, failed_rules = self.neutral["behavior"], [], []

        total = (
            self.weights["answer_match"] * answer_score
            + self.weights["candidate_match"] * candidate_score
            + self.weights["completeness"] * completeness_score
            + self.weights["behavior"] * behavior_score
        )
        total = clamp(total, self.clamp_min, self.clamp_max)
        risk_level = self.risk_level_of(total)

        tips: List[str] = []
        for tip in behavior_tips:
            if tip.text not in tips:
                tips.append(tip.text)
        if missing_labels:
            tips.append("发布信息不完整，缺少：" + "、".join(missing_labels[:5]))
        if answer_score < 0.5:
            tips.extend(str(x) for x in (self.risk_tips_cfg.get("answer_match_low") or []))
        if candidate_score < 0.5:
            tips.extend(str(x) for x in (self.risk_tips_cfg.get("candidate_conflict") or []))
        tips.extend(str(x) for x in (self.risk_tips_cfg.get(risk_level) or []))
        if failed_rules:
            tips.append("部分风险规则未能评估（信号格式异常），已忽略")

        return CredibilityResult(
            credibility_score=total,
            risk_level=risk_level,
            per_question=per_question,
            risk_tips=_dedupe(tips),
            breakdown={
                "answer_match": answer_score,
                "candidate_match": candidate_score,
                "completeness": completeness_score,
                "behavior": behavior_score,
            },
            weights=dict(self.weights),
            answer_match_score=answer_score,
            behavior_score=behavior_score,
            completeness_score=completeness_score,
            candidate_match_score=candidate_score,
            model_version=self.config.model_version,
        )


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def compute_credibility(
    questions: Optional[Sequence[Any]] = None,
    answers: Optional[Sequence[Any]] = None,
    candidate_match: Optional[Dict[str, Any]] = None,
    target_item: Optional[Dict[str, Any]] = None,
    behavior_signals: Optional[Dict[str, Any]] = None,
    config: Optional[RuntimeConfig] = None,
) -> Dict[str, Any]:
    """便捷入口：返回可直接序列化的响应体。"""
    scorer = CredibilityScorer(config)
    return scorer.score(
        questions=questions, answers=answers, candidate_match=candidate_match,
        target_item=target_item, behavior_signals=behavior_signals,
    ).to_dict()


__all__ = [
    "RiskTip",
    "CredibilityResult",
    "CredibilityScorer",
    "compute_credibility",
]
