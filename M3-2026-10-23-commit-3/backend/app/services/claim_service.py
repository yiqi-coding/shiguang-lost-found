"""认领业务编排服务（契约 §3.8 / §3.9 / §3.10）。

本模块集中承载三条安全红线：

1. **问题安全校验（`validate_questions`）**：MaaS 生成的问题必须逐条通过
   「非空 / 不含答案原文及其连续 4 字以上子串 / 不含是非问 / 不重复 / 不含敏感词」，
   不通过则整体回退到 `algorithm/config/questions.yaml` 模板库；
2. **答案处理**：标准答案只能用 `app.core.security.hash_answer()` / `verify_answer()`
   处理（加盐、常量时间比对）；`answer_summary.reason` **绝不回显答案原文**，
   只输出「关键特征吻合」「设备名称完全一致」「未提及相关特征」这类模糊理由；
3. **状态流转**：验证通过后状态只能进入 `pending_review`（**绝不自动 claimed**），
   次数耗尽才置 `rejected` 并写风险标记 + `claim.verify_failed` 审计。

可信度公式（契约 §3.9，权重来自 `algorithm/config/questions.yaml`）::

    可信度分 = 0.60 × 隐藏特征答案匹配 + 0.20 × 候选物品综合匹配分
             + 0.10 × 发布信息完整度 + 0.10 × 行为/风险规则分
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients.maas_client import get_maas_client
from app.core.config import settings
from app.core.enums import AuditAction, AuditTargetType, ClaimStatus, ItemStatus, RiskLevel, UserFacingText
from app.core.errors import (
    ClaimNotFoundError,
    ForbiddenError,
    MatchNotFoundError,
    StateConflictError,
)
from app.core.security import hash_answer, normalize_answer, verify_answer
from app.models.claim import Claim
from app.models.item import Item
from app.models.match import Match
from app.models.user import User
from app.services import audit_service, serializers
from app.services.cache import cache
from app.services.scoring import match_level_for
from app.services.state_machine import assert_claim_transition, assert_item_transition

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 常量与配置
# ----------------------------------------------------------------------
# 可信度权重：与 docs/api-contract.md §3.9 及 algorithm/config/questions.yaml 一致
CREDIBILITY_WEIGHTS: dict[str, float] = {
    "answer_match": 0.60,
    "candidate_match": 0.20,
    "completeness": 0.10,
    "behavior": 0.10,
}

# 各分项缺数据时的中性分（绝不抛异常）
NEUTRAL_ANSWER_MATCH = 0.50
NEUTRAL_CANDIDATE_MATCH = 0.50
NEUTRAL_COMPLETENESS = 0.30
NEUTRAL_BEHAVIOR = 0.60

# 风险等级阈值：**以冻结的 API 契约 §2.6 为准**（≥0.75 low / 0.45~0.75 medium / <0.45 high）。
# 注意 algorithm/config/questions.yaml 中写的是 medium_min=0.50，与契约不一致；
# 契约是唯一事实来源，故此处取契约值，并在自检测试中断言。
RISK_LOW_MIN = 0.75
RISK_MEDIUM_MIN = 0.45

# 单次验证「通过」的判定线（隐藏特征答案匹配分）
ANSWER_PASS_THRESHOLD = 0.50

# 认领详情固定免责声明（契约 §3.10.1）
CLAIM_DETAIL_DISCLAIMER = "可信度仅为辅助审核依据，不构成所有权认定。"

# 答案模糊匹配阈值（与 algorithm/config/questions.yaml 的 fuzzy 段保持一致）
FUZZY_CONFIDENT_THRESHOLD = 0.62
FUZZY_PARTIAL_THRESHOLD = 0.34
EXACT_MATCH_SCORE = 1.0
EXACT_PARTIAL_SCORE = 0.60
FUZZY_CONFIDENT_SCORE = 1.0
FUZZY_PARTIAL_SCORE = 0.50
MIN_ANSWER_LENGTH = 2
MAX_REASON_LENGTH = 60

# 同义词归一表（需求 §4.5.4「语义匹配应容忍合理同义表达」）
#
# 为什么需要它：
#   标准答案是「盒体右侧有一道划痕」，而用户很可能答「盒子右边刮了一道」。
#   两者语义一致，但字符 bigram 相似度很低（实测 ~0.19，低于 0.34 的部分命中线），
#   不归一化就会被判定为「未提及相关特征」—— 真实失主反而通不过验证，
#   属于**误拒绝**，对业务是致命的。
#
# 设计原则：
#   1. 只做**语义等价**替换，不做近义扩展（避免把「没有划痕」也判成命中）；
#   2. 替换目标统一为「标准表述」，保证与参考答案落在同一词形上；
#   3. 长词优先（"右上方" 先于 "右"），避免短词先把长词切碎；
#   4. 全部为纯字符串替换，确定性、可复现，便于测试断言。
#
# 扩展方式：直接往对应元组里加词，**必须**同步补一条测试用例。
SYNONYM_GROUPS: dict[str, tuple[str, ...]] = {
    # 方位
    "右侧": ("右边", "右面", "右手边", "靠右"),
    "左侧": ("左边", "左面", "左手边", "靠左"),
    "上方": ("上面", "上边", "顶部", "顶端"),
    "下方": ("下面", "下边", "底部", "底端"),
    "内侧": ("里面", "内面", "内壁", "里头"),
    "外侧": ("外面", "外壁", "外头"),
    "正面": ("前面",),
    "背面": ("后面", "反面"),
    # 物品部件
    "盒体": ("盒子", "充电盒", "耳机盒", "电池仓", "充电仓"),
    "杯体": ("杯子", "杯身"),
    # 损伤类型
    "划痕": ("刮痕", "划伤", "刮花", "刮了一道", "划了一道", "滑痕", "擦痕", "刮的", "划的"),
    "磨损": ("磨痕", "磨掉", "磨花"),
    "磕碰": ("磕痕", "碰伤", "撞痕", "磕了一下", "碰了一下"),
    "裂纹": ("裂缝", "裂口", "裂痕"),
    "污渍": ("污点", "脏污", "渍迹", "茶渍", "水渍"),
    # 程度 / 度量
    "厘米": ("cm", "CM", "公分"),
    "大约": ("大概", "差不多", "约", "左右"),
    # 配件
    "耳塞": ("耳帽", "耳套"),
    "充电线": ("数据线", "充电绳"),
}

# 否定词：出现这些词说明用户在**否认**某特征，不能因为共享名词就判命中。
# 例如「没有任何划痕」包含「划痕」，若只做相似度会误判为命中。
NEGATION_WORDS: tuple[str, ...] = (
    "没有", "没", "无", "不含", "不缺", "不见", "未", "并非", "不是",
    "完好", "无损", "干净", "未发现", "没发现",
)

# 问题安全校验
MIN_ANSWER_FRAGMENT_LENGTH = 4  # 「答案连续 4 字以上子串」判定阈值（契约 §3.8 红线 2）
QUESTION_MIN_LENGTH = 5
QUESTION_MAX_LENGTH = 200

# 是非问句式（必须拒绝：可被猜测或用「是/否」蒙对）
YES_NO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"是不是"),
    re.compile(r"有没有"),
    re.compile(r"是否"),
    re.compile(r"对不[对好]"),
    re.compile(r"[好行]不[好行]"),
    re.compile(r"能不[能可]"),
    re.compile(r"是.{0,6}吗[？?]?$"),
    re.compile(r"吗[？?]?$"),
    re.compile(r"吧[？?]?$"),
    re.compile(r"对不对"),
    re.compile(r"正不正确"),
    re.compile(r"有没有.{0,6}[？?]"),
)

# 敏感词（问题中不得询问身份信息、凭证、密钥等）
SENSITIVE_WORDS: tuple[str, ...] = (
    "密码", "口令", "验证码", "身份证", "学号", "银行卡", "手机号", "电话号码",
    "宿舍号", "微信号", "qq号", "邮箱", "密钥", "令牌", "token", "password",
    "secret", "api key", "apikey", "答案", "标准答案",
)

# 兜底模板（`questions.yaml` 不可读时使用；**均为开放式提问**，不含是非问句式）
BUILTIN_QUESTION_TEMPLATES: tuple[str, ...] = (
    "请描述这件物品上最不容易被外人注意到的一个细节（位置、形状或内容）。",
    "请说明该物品的损伤、磨损或污渍情况，并指出具体位置。",
    "请描述物品上的贴纸、标记、刻字或挂件，并说明其内容。",
    "请列出物品丢失或拾取时附带的所有配件。",
)

# 模板库路径：仓库根目录下的 algorithm/config/questions.yaml（只读，不修改算法侧文件）
QUESTIONS_YAML_CANDIDATES: tuple[Path, ...] = (
    Path(__file__).resolve().parents[3] / "algorithm" / "config" / "questions.yaml",
    Path.cwd() / "algorithm" / "config" / "questions.yaml",
    Path.cwd().parent / "algorithm" / "config" / "questions.yaml",
)

MAX_QUESTIONS = 3
MIN_QUESTIONS = 2


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------
@dataclass
class QuestionDraft:
    """候选题（生成阶段使用，`standard_answer` **仅存在于内存中**）。"""

    question_id: str
    question_text: str
    source: str = "template"  # maas / template
    feature_ref: int | None = None
    standard_answer: str = ""
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """转为可入库/可校验的结构。

        ⚠ **绝不包含 `standard_answer`**：`claims.questions` 只存题目元数据，
        标准答案只以加盐哈希形式保存在 `claims.answers_hash`。
        """
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "source": self.source,
            "feature_ref": self.feature_ref,
            "weight": self.weight,
        }


@dataclass
class QuestionValidation:
    """问题安全校验结果。"""

    ok: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class QuestionScore:
    """单题评分结果（写入 `claims.answer_summary`）。"""

    question_id: str
    matched: bool
    score: float
    reason: str


@dataclass
class VerificationOutcome:
    """一次验证提交的完整结果（供路由组装契约 §3.9 响应）。"""

    claim_id: int
    verification_score: float
    credibility_score: float
    risk_level: str
    status: str
    per_question: list[QuestionScore] = field(default_factory=list)
    risk_tips: list[str] = field(default_factory=list)
    attempts_left: int = 0
    passed: bool = False
    next_step: str = ""

    @property
    def per_question_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "question_id": item.question_id,
                "matched": item.matched,
                "score": round(item.score, 5),
                "reason": item.reason,
            }
            for item in self.per_question
        ]


# ----------------------------------------------------------------------
# 模板库读取
# ----------------------------------------------------------------------
def load_template_questions() -> list[str]:
    """读取 `algorithm/config/questions.yaml` 的问题模板（只读；失败则用内置兜底）。

    ⚠ 模板中的 `{hidden_feature}` 占位符在使用时会替换为**泛化描述**，
    绝不把隐藏特征原文写进题目（否则等于泄露答案）。
    """
    for path in QUESTIONS_YAML_CANDIDATES:
        try:
            if not path.is_file():
                continue
            import yaml  # 延迟导入：未安装 PyYAML 时走内置模板

            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            templates = data.get("question_templates") or []
            questions = [
                str(item.get("question") or "").strip()
                for item in templates
                if isinstance(item, dict) and str(item.get("question") or "").strip()
            ]
            if questions:
                return questions
        except Exception as exc:  # noqa: BLE001 - 配置不可读绝不阻断认领流程
            logger.warning("读取问题模板库失败（将使用内置模板）：%s", type(exc).__name__)
    return list(BUILTIN_QUESTION_TEMPLATES)


# ----------------------------------------------------------------------
# 问题安全校验（红线）
# ----------------------------------------------------------------------
def _iter_answer_texts(item: Item) -> list[str]:
    """物品的隐藏特征原文列表（**仅服务端内存使用，禁止写日志/响应**）。"""
    texts: list[str] = []
    for feature in item.hidden_features or []:
        if isinstance(feature, dict):
            value = feature.get("value") or feature.get("text") or ""
        else:
            value = feature
        text = str(value).strip()
        if text:
            texts.append(text)
    return texts


def _answer_fragments(answer: str, size: int = MIN_ANSWER_FRAGMENT_LENGTH) -> set[str]:
    """答案的连续 N 字子串集合（用于检测题目是否复述了答案）。"""
    normalized = normalize_answer(answer)
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


# 属性标签词：这些词是「问什么」而不是「答案是什么」，属于公开知识
# （例如隐藏特征是「蓝牙名称 YiQi-AirPods」，问题里出现「蓝牙名称」不构成泄露，
#  真正的答案是「YiQi-AirPods」）。因此做片段比对前先在两侧剔除标签词。
ATTRIBUTE_LABEL_WORDS: tuple[str, ...] = (
    "蓝牙设备名", "蓝牙名称", "设备名称", "蓝牙名", "名称", "设备", "颜色", "品牌", "型号",
    "材质", "位置", "形状", "长度", "大小", "特征", "痕迹", "细节", "配件", "内容",
    "贴纸", "刻字", "标记", "磨损", "划痕", "污渍", "损伤",
)


def _strip_attribute_labels(text: str) -> str:
    """剔除属性标签词（仅在内存中使用，不改变原始数据）。"""
    result = text
    for word in ATTRIBUTE_LABEL_WORDS:
        result = result.replace(word, "")
    return result


def question_leaks_answer(question_text: str, answer_text: str) -> bool:
    """判断题面是否泄露答案：包含答案整体，或包含其任意连续 4 字以上子串。

    比对前会先剔除「属性标签词」（见 `ATTRIBUTE_LABEL_WORDS`）：
    提问「该设备的蓝牙名称是什么」不会因为隐藏特征里写了「蓝牙名称 XXX」而误判为泄露，
    但一旦题目里出现答案本体（如「YiQi-AirPods」）或其片段（如「盒盖内侧」），即判定泄露。
    """
    question_norm = _strip_attribute_labels(normalize_answer(question_text))
    answer_norm = _strip_attribute_labels(normalize_answer(answer_text))
    if not question_norm or not answer_norm:
        return False

    # 中文按字、英文数字按字符统一处理：答案较短（<4 字）时要求整体不出现
    if answer_norm in question_norm:
        return True
    if len(answer_norm) < MIN_ANSWER_FRAGMENT_LENGTH:
        return False
    for fragment in _answer_fragments(answer_norm):
        if fragment and fragment in question_norm:
            return True
    return False


def _is_yes_no_question(question_text: str) -> bool:
    """是否是「是不是 / 有没有」这类是非问（可猜、无验证价值）。"""
    text = (question_text or "").strip()
    return any(pattern.search(text) for pattern in YES_NO_PATTERNS)


def _contains_sensitive_word(question_text: str) -> bool:
    lowered = (question_text or "").lower()
    return any(word.lower() in lowered for word in SENSITIVE_WORDS)


def validate_questions(questions: list[dict[str, Any]], item: Item) -> QuestionValidation:
    """问题安全校验（契约 §3.8 红线 2，**必须实现**）。

    逐条检查：

    1. 题目文本非空、长度合理（5~200 字）；
    2. 不含答案原文，也不含其**连续 4 字以上子串**（含则等于送答案）；
    3. 不含「是不是 / 有没有 / 是否 / ……吗」这类是非问；
    4. 不同问题之间不重复（按归一化文本与 question_id 双重判重）；
    5. 不含敏感词（密码、身份证、学号、验证码、token 等）。

    :return: `QuestionValidation(ok, problems)`；`problems` 只描述违规类型，
        **不包含答案原文**（避免校验日志反而造成泄露）。
    """
    problems: list[str] = []

    if not questions:
        return QuestionValidation(False, ["未生成任何问题"])

    answers = _iter_answer_texts(item)
    seen_texts: set[str] = set()
    seen_ids: set[str] = set()

    for index, raw in enumerate(questions):
        text = str(raw.get("question_text") or "").strip()
        question_id = str(raw.get("question_id") or f"q{index + 1}")
        label = f"第 {index + 1} 题"

        if not text:
            problems.append(f"{label}题目文本为空")
            continue
        if len(text) < QUESTION_MIN_LENGTH:
            problems.append(f"{label}题目过短（少于 {QUESTION_MIN_LENGTH} 字）")
            continue
        if len(text) > QUESTION_MAX_LENGTH:
            problems.append(f"{label}题目过长（超过 {QUESTION_MAX_LENGTH} 字）")

        if question_id in seen_ids:
            problems.append(f"{label}question_id 重复（{question_id}）")
        seen_ids.add(question_id)

        normalized = normalize_answer(text)
        if normalized in seen_texts:
            problems.append(f"{label}与其它问题重复")
        seen_texts.add(normalized)

        if _is_yes_no_question(text):
            problems.append(f"{label}属于是非问（可被猜测），必须改为开放式提问")

        if _contains_sensitive_word(text):
            problems.append(f"{label}包含敏感词（涉及身份信息或凭证）")

        for answer in answers:
            if question_leaks_answer(text, answer):
                problems.append(f"{label}泄露了隐藏特征原文或其片段")
                break

    return QuestionValidation(not problems, problems)


# ----------------------------------------------------------------------
# 问题生成（MaaS 优先，失败/不通过回退模板）
# ----------------------------------------------------------------------
def generate_questions(db: Session, *, item: Item, count: int = MAX_QUESTIONS) -> list[QuestionDraft]:
    """生成验证问题（契约 §3.8 红线 2）。

    流程：MaaS 生成 → `validate_questions` → 不通过/不可用则回退模板库 → 再次校验
    → 仍不通过则使用内置通用问题（保证一定能出题）。
    """
    answers = _iter_answer_texts(item)
    count = max(MIN_QUESTIONS, min(count, MAX_QUESTIONS))

    drafts = _questions_from_maas(item, count)
    if drafts:
        validation = validate_questions([d.to_dict() for d in drafts], item)
        if validation.ok:
            return _finalize_ids(drafts, answers)
        logger.warning(
            "MaaS 生成的问题未通过安全校验（已回退模板）：%s", "；".join(validation.problems[:3])
        )

    drafts = _questions_from_templates(item, count)
    validation = validate_questions([d.to_dict() for d in drafts], item)
    if validation.ok:
        return _finalize_ids(drafts, answers)

    logger.warning("模板问题未通过安全校验（已回退内置通用问题）：%s", "；".join(validation.problems[:3]))
    drafts = _questions_from_templates(item, MIN_QUESTIONS, templates=list(BUILTIN_QUESTION_TEMPLATES))
    return _finalize_ids(drafts, answers)


def _questions_from_maas(item: Item, count: int) -> list[QuestionDraft]:
    """调用 MaaS 生成问题（未启用/失败返回空列表，绝不抛异常）。"""
    try:
        generated = get_maas_client().generate_verification_questions(
            hidden_features=_iter_answer_texts(item), category=item.category, count=count
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("MaaS 生成问题失败（将回退模板）：%s", type(exc).__name__)
        return []
    if not generated:
        return []

    drafts: list[QuestionDraft] = []
    for index, entry in enumerate(generated[:count]):
        text = str(entry.get("question_text") or "").strip()
        if not text:
            continue
        feature_ref = entry.get("feature_ref")
        drafts.append(
            QuestionDraft(
                question_id=str(entry.get("question_id") or f"q{index + 1}"),
                question_text=text,
                source="maas",
                feature_ref=int(feature_ref) if isinstance(feature_ref, (int, float, str)) and str(feature_ref).isdigit() else None,
            )
        )
    return drafts


def _questions_from_templates(
    item: Item, count: int, *, templates: list[str] | None = None
) -> list[QuestionDraft]:
    """从模板库构造问题（模板中的 `{hidden_feature}` 用**泛化占位文本**替换）。

    关键安全点：

    1. 模板占位符绝不能被隐藏特征原文填充，否则题目本身就成了答案；
    2. 模板库中可能混有「物品表面是否有划痕……？」这类**是非问**，
       因此逐条过一遍 `validate_questions`，只保留安全模板（不安全的直接丢弃，
       而不是把风险带进正式流程）。
    """
    pool = templates if templates is not None else load_template_questions()
    if not pool:
        pool = list(BUILTIN_QUESTION_TEMPLATES)

    safe_texts: list[str] = []
    for template in pool:
        text = str(template).replace("{hidden_feature}", "该特征").strip()
        if not text:
            continue
        probe = [{"question_id": "probe", "question_text": text}]
        if validate_questions(probe, item).ok and text not in safe_texts:
            safe_texts.append(text)

    if not safe_texts:
        safe_texts = [str(text) for text in BUILTIN_QUESTION_TEMPLATES]

    drafts: list[QuestionDraft] = []
    for index in range(count):
        drafts.append(
            QuestionDraft(
                question_id=f"q{index + 1}",
                question_text=safe_texts[index % len(safe_texts)],
                source="template",
                feature_ref=index,
            )
        )
    return drafts


def _finalize_ids(drafts: list[QuestionDraft], answers: list[str]) -> list[QuestionDraft]:
    """统一 question_id（q1/q2/q3）并绑定标准答案（仅内存）。"""
    finalized: list[QuestionDraft] = []
    for index, draft in enumerate(drafts):
        draft.question_id = f"q{index + 1}"
        if draft.feature_ref is None and answers:
            draft.feature_ref = index % len(answers)
        if answers:
            draft.standard_answer = answers[draft.feature_ref % len(answers)]
        finalized.append(draft)
    return finalized


# ----------------------------------------------------------------------
# 答案比对
# ----------------------------------------------------------------------
def normalize_synonyms(text: str) -> str:
    """把同义表达归一为标准表述（需求 §4.5.4「容忍合理同义表达」）。

    - 按**长词优先**替换，避免「右上方」被「右」先切碎；
    - 只做等价替换，不做近义扩展；
    - 输入为空时原样返回。
    """
    if not text:
        return text
    result = text
    # 收集所有 (别名 → 标准词)，按别名长度降序，保证长词优先
    pairs: list[tuple[str, str]] = []
    for canonical, aliases in SYNONYM_GROUPS.items():
        for alias in aliases:
            if alias and alias != canonical:
                pairs.append((alias, canonical))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    for alias, canonical in pairs:
        result = result.replace(alias, canonical)
    return result


def has_negation(text: str) -> bool:
    """判断文本是否包含否定表述（如「没有划痕」「完好无损」）。"""
    normalized = normalize_answer(text or "")
    # 归一化会去掉空白与标点，因此直接用子串匹配即可
    return any(normalize_answer(word) and normalize_answer(word) in normalized for word in NEGATION_WORDS)


def score_answer(
    *,
    question: dict[str, Any],
    user_answer: str,
    standard_answer: str,
    stored_hash: str | None,
) -> QuestionScore:
    """单题评分。

    判定顺序（**标准答案只经由 hash/verify 处理**）：

    1. `verify_answer()` 常量时间比对上已存储的加盐哈希 → 完全一致（1.0）；
    2. **否定检测**：用户答案包含否定表述且与标准答案不共享肯定语义 → 不命中（0.0）；
    3. 规范化 + 同义归一后的包含关系（一方包含另一方）→ 部分命中（0.6 / 0.5）；
    4. 同义归一后的字符 bigram Dice 相似度 ≥ 0.62 → 命中（1.0）；≥ 0.34 → 部分命中（0.5）；
    5. 其余 → 未命中（0.0）。

    `reason` 一律为模糊文案，**不含答案原文**。
    """
    question_id = str(question.get("question_id") or "")
    text = (user_answer or "").strip()

    if len(normalize_answer(text)) < MIN_ANSWER_LENGTH:
        return QuestionScore(question_id, False, 0.0, "该问题未作答或答案过短，无法验证")

    # 1) 哈希精确比对（标准答案的唯一权威路径）
    if stored_hash and verify_answer(text, stored_hash):
        return QuestionScore(question_id, True, EXACT_MATCH_SCORE, _exact_reason(question))

    # 2) 否定检测
    #
    # 为什么必须在相似度之前：
    #   「没有任何划痕，很干净」与标准答案「盒盖内侧有一道3厘米划痕」共享「划痕」，
    #   相似度足以越过部分命中线，于是被误判为「部分命中」（0.5 分）。
    #   但用户表达的是**否认**，语义完全相反 —— 这是冒领者的典型答法。
    #
    # 判定方式：用户答案含否定词，且**肯定语义部分**（去掉否定词后）与标准答案
    # 的相似度过低，则判为否认。这样既拦住「没有划痕」，
    # 又不误伤「没有划痕，但盒子上有贴纸」这类包含否定词的**有效**回答
    # （后者与标准答案的相似度仍然很低，但如果标准答案问的是贴纸，就会命中）。
    if has_negation(text):
        positive_part = text
        for word in NEGATION_WORDS:
            positive_part = positive_part.replace(word, " ")
        positive_norm = normalize_answer(normalize_synonyms(positive_part))
        standard_norm_for_neg = normalize_answer(normalize_synonyms(standard_answer))
        similarity_positive = _dice_similarity(positive_norm, standard_norm_for_neg)
        if similarity_positive < FUZZY_PARTIAL_THRESHOLD:
            return QuestionScore(question_id, False, 0.0, "回答与登记特征不一致")

    # 3) 规范化 + 同义归一后的包含关系
    user_norm = normalize_answer(normalize_synonyms(text))
    standard_norm = normalize_answer(normalize_synonyms(standard_answer))
    if standard_norm and (standard_norm in user_norm or user_norm in standard_norm):
        return QuestionScore(question_id, True, EXACT_PARTIAL_SCORE, "关键特征基本吻合")

    # 4) 同义归一后的模糊相似度
    similarity = _dice_similarity(user_norm, standard_norm)
    if similarity >= FUZZY_CONFIDENT_THRESHOLD:
        return QuestionScore(question_id, True, FUZZY_CONFIDENT_SCORE, _exact_reason(question))
    if similarity >= FUZZY_PARTIAL_THRESHOLD:
        return QuestionScore(question_id, True, FUZZY_PARTIAL_SCORE, "部分特征吻合，建议补充细节")
    return QuestionScore(question_id, False, 0.0, "未提及相关特征")


def _exact_reason(question: dict[str, Any]) -> str:
    """按题目类型给出**不泄露答案**的命中理由（契约 §3.9 红线 1）。"""
    question_type = str(question.get("type") or "")
    question_text = str(question.get("question_text") or "")
    if question_type == "field" or "名称" in question_text:
        return "设备名称完全一致"
    if "位置" in question_text or "哪里" in question_text:
        return "关键位置描述吻合"
    return "关键特征吻合"


def _dice_similarity(text_a: str, text_b: str, n: int = 2) -> float:
    """字符 n-gram Dice 相似度（确定性算法，无随机量）。"""
    if not text_a or not text_b:
        return 0.0
    if len(text_a) < n or len(text_b) < n:
        return 1.0 if text_a == text_b else 0.0
    grams_a = {text_a[i : i + n] for i in range(len(text_a) - n + 1)}
    grams_b = {text_b[i : i + n] for i in range(len(text_b) - n + 1)}
    if not grams_a or not grams_b:
        return 0.0
    overlap = len(grams_a & grams_b)
    return 2 * overlap / (len(grams_a) + len(grams_b))


# ----------------------------------------------------------------------
# 可信度计算
# ----------------------------------------------------------------------
def risk_level_for(score: float) -> str:
    """可信度分 → 风险等级（阈值来自契约 §2.6：0.75 / 0.45）。"""
    if score >= RISK_LOW_MIN:
        return RiskLevel.LOW
    if score >= RISK_MEDIUM_MIN:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def compute_credibility(
    *,
    answer_match: float,
    candidate_match: float,
    completeness: float,
    behavior: float,
) -> float:
    """可信度加权和（契约 §3.9 冻结公式，权重和为 1.0）。"""
    total = (
        CREDIBILITY_WEIGHTS["answer_match"] * _clamp(answer_match)
        + CREDIBILITY_WEIGHTS["candidate_match"] * _clamp(candidate_match)
        + CREDIBILITY_WEIGHTS["completeness"] * _clamp(completeness)
        + CREDIBILITY_WEIGHTS["behavior"] * _clamp(behavior)
    )
    return round(_clamp(total), 5)


def completeness_score(item: Item) -> float:
    """发布信息完整度（0~1）：字段权重来自 `questions.yaml` 的 completeness 段。"""
    fields: dict[str, float] = {
        "title": 0.10,
        "description": 0.15,
        "category": 0.10,
        "color": 0.10,
        "brand": 0.05,
        "location": 0.15,
        "event_time": 0.15,
        "image_url": 0.05,
        "public_features": 0.15,
    }
    present = {
        "title": bool(item.title),
        "description": bool(item.description and len(item.description) >= 12),
        "category": bool(item.category),
        "color": bool(item.color),
        "brand": bool(item.brand),
        "location": bool(item.location),
        "event_time": item.event_time is not None,
        "image_url": bool(item.images),
        "public_features": bool(item.public_features),
    }
    total_weight = sum(fields.values())
    if total_weight <= 0:
        return NEUTRAL_COMPLETENESS
    earned = sum(weight for name, weight in fields.items() if present.get(name))
    return round(_clamp(earned / total_weight), 5)


def behavior_score(
    *,
    claim: Claim,
    match: Match | None,
    recent_claim_count: int = 0,
    description_length: int = 0,
) -> tuple[float, list[str]]:
    """行为/风险规则分（从 1.0 起扣分），返回 (分数, 风险提示)。

    规则与 `algorithm/config/questions.yaml` 的 behavior 段一致（仅保留可在后端判定的规则）。
    """
    score = 1.0
    tips: list[str] = []

    # 规则 1：提交过快（3 分钟内）→ -0.25
    created_at = _as_naive(claim.created_at)
    if created_at is not None:
        elapsed_minutes = (datetime.now(timezone.utc).replace(tzinfo=None) - created_at).total_seconds() / 60
        if 0 <= elapsed_minutes < 3:
            score -= 0.25
            tips.append("认领申请提交过快，建议人工核验认领人身份")

    # 规则 2：短期内多次认领 → -0.20
    if recent_claim_count >= 3:
        score -= 0.20
        tips.append("同一用户短期内多次认领申请，建议人工复核")

    # 规则 3：历史验证失败次数较多 → -0.20
    if claim.attempts_used >= 3:
        score -= 0.20
        tips.append("历史验证失败次数较多")

    # 规则 4：描述信息较模糊 → -0.10
    if 0 < description_length < 8:
        score -= 0.10
        tips.append("描述信息较模糊，关键特征缺失")

    # 规则 5：候选匹配分偏低 → 额外提示（不扣分，已在 candidate_match 体现）
    if match is not None and float(match.total_score or 0) < settings.MATCH_MIN_DISPLAY_SCORE:
        tips.append("候选物品综合匹配分偏低，注意是否认领了错误物品")

    return round(_clamp(score), 5), tips


def _clamp(value: float | None, low: float = 0.0, high: float = 1.0) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return low
    return max(low, min(high, float(value)))


def _as_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


# ----------------------------------------------------------------------
# 发起认领
# ----------------------------------------------------------------------
def get_claim_or_404(db: Session, claim_id: int) -> Claim:
    """按 ID 取认领记录，不存在抛 `404 CLAIM_NOT_FOUND`。"""
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise ClaimNotFoundError("认领记录不存在")
    return claim


def create_claim(db: Session, *, applicant: User, match_id: int) -> tuple[Claim, datetime]:
    """发起认领（契约 §3.8）。

    权限（契约 §3.8 红线 4）：

    - 只有匹配中**拾物方**（`found` 记录发布者）或管理员可发起；
    - 申请人**不能**认领自己发布的物品（自己不能认领自己的失物）。

    状态：被认领的物品（`lost` 记录）按状态机流转 `matched → claiming`
    （若尚未 matched，则先 `published → matched`）。
    """
    match = db.get(Match, match_id)
    if match is None:
        raise MatchNotFoundError("匹配记录不存在，请先计算匹配")

    lost_item = match.lost_item
    found_item = match.found_item
    if lost_item is None or found_item is None:
        raise MatchNotFoundError("匹配记录缺少关联物品")

    # 权限校验
    if not applicant.is_admin and found_item.owner_user_id != applicant.id:
        raise ForbiddenError("只有拾物方（拾到该物品的用户）可以发起认领")
    if lost_item.owner_user_id == applicant.id:
        raise ForbiddenError("不能认领自己发布的物品")

    # 重复认领（同一用户对同一 match 只允许一个未关闭的认领）
    existing_stmt = (
        select(Claim)
        .where(Claim.match_id == match_id)
        .where(Claim.applicant_id == applicant.id)
        .where(Claim.status.notin_([ClaimStatus.APPROVED, ClaimStatus.REJECTED]))
    )
    existing = db.execute(existing_stmt).scalars().first()
    if existing is not None:
        raise StateConflictError("你已对该匹配发起过认领，请先完成验证或等待审核结果")

    # 生成问题（MaaS 优先，失败回退模板；全部经过安全校验）
    drafts = generate_questions(db, item=lost_item, count=MAX_QUESTIONS)

    claim = Claim(
        match_id=match_id,
        applicant_id=applicant.id,
        questions=[draft.to_dict() for draft in drafts],
        answers_hash={draft.question_id: hash_answer(draft.standard_answer) for draft in drafts if draft.standard_answer},
        answer_summary=None,
        answers_plain=None,  # Baseline 不存明文（契约 §5 + 数据库设计 §3.4）
        status=ClaimStatus.CLAIMING,
        attempts_used=0,
        max_attempts=settings.CLAIM_MAX_ATTEMPTS,
    )
    db.add(claim)
    db.flush()

    # 状态流转：published → matched（若需要）→ claiming，全部经状态机
    if lost_item.status == ItemStatus.PUBLISHED:
        assert_item_transition(ItemStatus.PUBLISHED, ItemStatus.MATCHED)
        lost_item.status = ItemStatus.MATCHED
        audit_service.log_item_status_change(
            db,
            item_id=lost_item.id,
            operator=applicant,
            from_status=ItemStatus.PUBLISHED,
            to_status=ItemStatus.MATCHED,
            reason="发起认领前确认匹配状态",
        )
    assert_item_transition(lost_item.status, ItemStatus.CLAIMING)
    lost_item.status = ItemStatus.CLAIMING
    db.add(lost_item)

    audit_service.log_claim_create(
        db,
        claim_id=claim.id,
        operator=applicant,
        match_id=match_id,
        question_count=len(claim.questions or []),
    )
    db.commit()
    db.refresh(claim)

    expires_at = _claim_expires_at(claim)
    _store_claim_session(claim, expires_at=expires_at)
    cache.invalidate_item(lost_item.id)  # 状态变化 → 清理匹配缓存
    logger.info("认领已发起 claim_id=%s match_id=%s applicant=%s", claim.id, match_id, applicant.id)
    return claim, expires_at


def _claim_expires_at(claim: Claim) -> datetime:
    """认领会话过期时间（Redis `claim:session:{id}` TTL 30min，契约 §5）。"""
    created = _as_naive(claim.created_at) or datetime.now(timezone.utc).replace(tzinfo=None)
    return created + timedelta(seconds=settings.REDIS_TTL_CLAIM_SESSION)


def _store_claim_session(claim: Claim, *, expires_at: datetime) -> None:
    """把认领会话写入 Redis：**只存题目元数据与答案哈希，绝不存答案明文**。"""
    payload = {
        "claim_id": claim.id,
        "match_id": claim.match_id,
        "applicant_id": claim.applicant_id,
        "question_ids": [str(q.get("question_id")) for q in claim.questions or []],
        "answers_hash": claim.answers_hash or {},  # 已是加盐哈希
        "attempts_used": claim.attempts_used,
        "max_attempts": claim.max_attempts,
        "expires_at": expires_at.isoformat(),
    }
    cache.set_json(cache.claim_session_key(claim.id), payload, settings.REDIS_TTL_CLAIM_SESSION)


# ----------------------------------------------------------------------
# 提交验证
# ----------------------------------------------------------------------
def verify_claim(
    db: Session,
    *,
    claim: Claim,
    applicant: User,
    answers: dict[str, str],
    ip: str | None = None,
) -> VerificationOutcome:
    """提交验证（契约 §3.9）。

    - 仅申请人本人（或管理员）可提交；
    - 状态必须为 `claiming` / `request_more_info`，否则 `409 STATE_CONFLICT`；
    - 次数用尽仍继续提交 → `409 STATE_CONFLICT`；
    - 验证通过 → `claiming → pending_review`（**绝不自动 claimed**）；
    - 失败且次数耗尽 → `claiming → rejected` + 风险标记 + `claim.verify_failed` 审计。
    """
    if not applicant.is_admin and claim.applicant_id != applicant.id:
        raise ForbiddenError("只能提交本人的认领验证")
    if claim.status in (ClaimStatus.APPROVED, ClaimStatus.REJECTED):
        raise StateConflictError(f"认领已结束（{claim.status}），无法再次提交验证")
    if claim.is_exhausted:
        raise StateConflictError("验证次数已用尽，请联系管理员进行人工审核")

    match = claim.match
    lost_item = match.lost_item if match else None
    if lost_item is None:
        raise MatchNotFoundError("认领关联的匹配记录缺少物品信息")

    standard_answers = _standard_answers_for(claim, lost_item)

    # ---------- 1) 逐题评分（标准答案仅在内存中参与比对）----------
    scores: list[QuestionScore] = []
    total_weight = 0.0
    weighted_sum = 0.0
    for question in claim.questions or []:
        question_id = str(question.get("question_id") or "")
        weight = float(question.get("weight") or 1.0)
        user_answer = answers.get(question_id, "")
        score = score_answer(
            question=question,
            user_answer=user_answer,
            standard_answer=standard_answers.get(question_id, ""),
            stored_hash=(claim.answers_hash or {}).get(question_id),
        )
        scores.append(score)
        weighted_sum += weight * score.score
        total_weight += weight

    verification_score = round(_clamp(weighted_sum / total_weight if total_weight else 0.0), 5)

    # ---------- 2) 可信度四要素 ----------
    candidate_match = float(match.total_score or 0) if match else NEUTRAL_CANDIDATE_MATCH
    completeness = completeness_score(lost_item)
    recent_claims = _recent_claim_count(db, applicant_id=claim.applicant_id)
    behavior, behavior_tips = behavior_score(
        claim=claim,
        match=match,
        recent_claim_count=recent_claims,
        description_length=len(lost_item.description or ""),
    )
    credibility = compute_credibility(
        answer_match=verification_score,
        candidate_match=candidate_match,
        completeness=completeness,
        behavior=behavior,
    )
    risk_level = risk_level_for(credibility)
    risk_tips = _build_claim_risk_tips(
        level=risk_level, answer_match=verification_score, candidate_match=candidate_match, extra=behavior_tips
    )

    # ---------- 3) 状态推进（状态机集中流转）----------
    passed = verification_score >= ANSWER_PASS_THRESHOLD
    claim.attempts_used += 1
    claim.answer_summary = [
        {
            "question_id": item.question_id,
            "matched": item.matched,
            "score": round(item.score, 5),
            "reason": item.reason,  # 模糊理由，绝不含答案原文
        }
        for item in scores
    ]
    claim.verification_score = verification_score
    claim.credibility_score = credibility
    claim.risk_level = risk_level
    claim.risk_tips = risk_tips

    next_step: str
    if passed:
        assert_claim_transition(claim.status, ClaimStatus.PENDING_REVIEW)
        claim.status = ClaimStatus.PENDING_REVIEW
        next_step = "等待管理员审核"
    elif claim.attempts_left <= 0:
        assert_claim_transition(claim.status, ClaimStatus.REJECTED)
        claim.status = ClaimStatus.REJECTED
        claim.risk_tips = risk_tips + ["验证次数已用尽，系统已自动驳回并标记为高风险，请等待管理员复核"]
        next_step = "验证次数已用尽，认领已驳回；如有异议请联系管理员"
    else:
        next_step = f"还有 {claim.attempts_left} 次验证机会，请补充更准确的特征描述"

    db.add(claim)

    # 物品状态：验证通过 → claiming → pending_review；验证失败用尽 → 回到 matched 供他人认领
    if passed:
        assert_item_transition(lost_item.status, ItemStatus.PENDING_REVIEW)
        lost_item.status = ItemStatus.PENDING_REVIEW
        db.add(lost_item)
        audit_service.log_item_status_change(
            db,
            item_id=lost_item.id,
            operator=applicant,
            from_status=ItemStatus.CLAIMING,
            to_status=ItemStatus.PENDING_REVIEW,
            reason="隐藏特征验证通过，进入人工审核",
        )
    elif claim.status == ClaimStatus.REJECTED and lost_item.status == ItemStatus.CLAIMING:
        assert_item_transition(ItemStatus.CLAIMING, ItemStatus.MATCHED)
        lost_item.status = ItemStatus.MATCHED
        db.add(lost_item)
        audit_service.log_item_status_change(
            db,
            item_id=lost_item.id,
            operator=applicant,
            from_status=ItemStatus.CLAIMING,
            to_status=ItemStatus.MATCHED,
            reason="认领验证失败且次数用尽，物品回到可认领状态",
        )

    # ---------- 4) 审计 ----------
    audit_service.log_claim_verify(
        db,
        claim_id=claim.id,
        operator=applicant,
        verification_score=verification_score,
        credibility_score=credibility,
        risk_level=risk_level,
        passed=passed,
    )
    if not passed and claim.status == ClaimStatus.REJECTED:
        # 次数用尽的失败必须额外留痕（契约 §3.9 红线 3）
        audit_service.write_audit_log(
            db,
            action=AuditAction.CLAIM_VERIFY_FAILED,
            target_type=AuditTargetType.CLAIM,
            target_id=claim.id,
            operator=applicant,
            detail={
                "attempts_used": claim.attempts_used,
                "max_attempts": claim.max_attempts,
                "risk_level": risk_level,
                "reason": "验证次数用尽，自动驳回",
            },
            ip=ip,
        )

    db.commit()
    db.refresh(claim)

    cache.invalidate_item(lost_item.id)
    cache.invalidate_claim(claim.id)
    logger.info(
        "认领验证完成 claim_id=%s verification=%.5f credibility=%.5f risk=%s status=%s",
        claim.id,
        verification_score,
        credibility,
        risk_level,
        claim.status,
    )
    return VerificationOutcome(
        claim_id=claim.id,
        verification_score=verification_score,
        credibility_score=credibility,
        risk_level=risk_level,
        status=claim.status,
        per_question=scores,
        risk_tips=list(claim.risk_tips or []),
        attempts_left=claim.attempts_left,
        passed=passed,
        next_step=next_step,
    )


def _standard_answers_for(claim: Claim, lost_item: Item) -> dict[str, str]:
    """按题目的 `feature_ref` 取回标准答案（**仅内存**，来源是物品的隐藏特征）。

    设计说明：数据库里只保存答案的加盐哈希（`claims.answers_hash`），
    标准答案由服务端在验证时从 `items.hidden_features` 现场重建，
    既不落库明文，也不进入日志/响应。
    """
    answers = _iter_answer_texts(lost_item)
    result: dict[str, str] = {}
    if not answers:
        return result
    for index, question in enumerate(claim.questions or []):
        question_id = str(question.get("question_id") or "")
        feature_ref = question.get("feature_ref")
        try:
            reference = int(feature_ref) if feature_ref is not None else index
        except (TypeError, ValueError):
            reference = index
        result[question_id] = answers[reference % len(answers)]
    return result


def _recent_claim_count(db: Session, *, applicant_id: int, hours: int = 24) -> int:
    """最近 N 小时内的认领次数（行为风险规则输入）。"""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    stmt = (
        select(func.count())
        .select_from(Claim)
        .where(Claim.applicant_id == applicant_id)
        .where(Claim.created_at >= since)
    )
    return int(db.execute(stmt).scalar_one() or 0)


def _build_claim_risk_tips(
    *, level: str, answer_match: float, candidate_match: float, extra: list[str]
) -> list[str]:
    """按风险等级与分项情况生成风险提示（文案来自 questions.yaml 的 risk_tips 段）。"""
    tips: list[str] = []
    if level == RiskLevel.HIGH:
        tips.extend(["可信度偏低，必须线下核验证件后再交付物品", "建议由管理员人工复核全部验证问答记录"])
    elif level == RiskLevel.MEDIUM:
        tips.append("存在部分关键特征未确认，建议补充一次线下核验")
    else:
        tips.append("验证结果较好，可按常规流程交付并留存记录")

    if answer_match < ANSWER_PASS_THRESHOLD:
        tips.append("隐藏特征回答匹配度较低，建议追加提问")
    if candidate_match < settings.MATCH_MIN_DISPLAY_SCORE:
        tips.append("候选物品综合匹配分偏低，注意是否认领了错误物品")
    for tip in extra:
        if tip not in tips:
            tips.append(tip)
    return tips


def claim_detail_for_applicant(claim: Claim, *, viewer: User) -> dict[str, Any]:
    """认领详情（认领人视角，契约 §3.10.1）。

    权限：**申请人本人或管理员**，他人访问抛 `403 FORBIDDEN`。

    红线：

    1. 绝不包含标准答案、`answers_hash`、`answers_plain`；
    2. 未提交验证前 `verification_score` / `credibility_score` / `risk_level`
       **不出现**（而非返回 null）——这一点由 `serializers.serialize_claim_for_applicant()`
       的「仅在有值时输出」实现，本函数不得把它们补成 None；
    3. `questions` 只含 `question_id` + `question_text`。
    """
    if not viewer.is_admin and claim.applicant_id != viewer.id:
        raise ForbiddenError("只能查看本人发起的认领")

    data = serializers.serialize_claim_for_applicant(claim)
    data["status_text"] = UserFacingText.CLAIM_STATUS.get(claim.status, claim.status)
    data["match_summary"] = match_summary_for(claim)
    data["can_submit"] = can_submit(claim)
    data["disclaimer"] = CLAIM_DETAIL_DISCLAIMER
    return data


def can_submit(claim: Claim) -> bool:
    """是否还能提交验证（契约 §3.10.1 红线 4：false 表示次数用尽或状态已关闭）。"""
    return claim.status in (ClaimStatus.CLAIMING, ClaimStatus.REQUEST_MORE_INFO) and not claim.is_exhausted


def match_summary_for(claim: Claim) -> dict[str, Any]:
    """认领关联匹配的摘要（契约 §3.10.1 的 `match_summary`）。

    说明：契约示例中 `item_title` / `post_type` 取自**申请人所属的 found 记录**
    （例如 `{"item_title": "在图书馆捡到黑色耳机", "post_type": "found"}`），
    此处保持一致，避免与前端已实现字段语义冲突。
    """
    match = claim.match
    item = match.found_item if match else None
    total = float(match.total_score or 0) if match else 0.0
    return {
        "item_title": item.title if item else None,
        "post_type": item.post_type if item else None,
        "total_score": total,
        "match_level": match_level_for(total),
    }


def claim_list_item(claim: Claim, *, viewer: User) -> dict[str, Any]:
    """「我的认领」列表元素：认领摘要 + 关联物品摘要（**不含标准答案**）。"""
    data = serializers.serialize_claim_for_applicant(claim)
    match = claim.match
    related = match.lost_item if match else None
    data["item_summary"] = serializers.serialize_item(related, viewer=viewer, detail=False) if related else None
    return data


def list_my_claims(
    db: Session, *, applicant: User, statuses: list[str] | None, offset: int, limit: int
) -> tuple[list[Claim], int]:
    """我的认领列表（契约 §3.10）。"""
    conditions = [Claim.applicant_id == applicant.id]
    if statuses:
        conditions.append(Claim.status.in_(statuses))

    count_stmt = select(func.count()).select_from(Claim)
    stmt = select(Claim)
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        stmt = stmt.where(condition)

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = (
        db.execute(stmt.order_by(Claim.created_at.desc()).offset(offset).limit(limit))
        .scalars()
        .unique()
        .all()
    )
    return list(rows), total


__all__ = [
    "CREDIBILITY_WEIGHTS",
    "RISK_LOW_MIN",
    "RISK_MEDIUM_MIN",
    "ANSWER_PASS_THRESHOLD",
    "QuestionDraft",
    "QuestionValidation",
    "QuestionScore",
    "VerificationOutcome",
    "load_template_questions",
    "validate_questions",
    "question_leaks_answer",
    "ATTRIBUTE_LABEL_WORDS",
    "generate_questions",
    "score_answer",
    "risk_level_for",
    "compute_credibility",
    "completeness_score",
    "behavior_score",
    "get_claim_or_404",
    "create_claim",
    "verify_claim",
    "claim_list_item",
    "claim_detail_for_applicant",
    "can_submit",
    "match_summary_for",
    "list_my_claims",
]
