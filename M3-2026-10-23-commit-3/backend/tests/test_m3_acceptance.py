"""M3 可信认领 · 端到端验收测试（真实数据库 + 真实路由）。

与既有的单元测试区别：
    既有测试（`test_claim_access.py`、`test_questions_security.py`）主要验证
    **函数级**行为，用内存对象构造数据、不触达数据库与 HTTP 层。
    本文件补上端到端链路：**真实 SQLite 数据库 + 真实 FastAPI 路由 + 真实序列化**，
    直接对应需求 §15「M3 验收：正确、错误、重复提交和越权场景均通过测试」。

为什么用 SQLite 内存库：
    测试需要真实执行 SQL（唯一约束、外键、事务回滚都要真的生效），
    但又不能依赖开发者本机装 MySQL。SQLite + `StaticPool` 可以在进程内
    建一个共享连接的内存库，语义足够覆盖本项目的约束。

覆盖的四类场景（对应 M3 DoD）
    1. **正确**：答案与隐藏特征吻合 → 可信度高、风险低、进入待审核
    2. **错误**：答案不吻合 → 可信度低、风险高、**不得通过**
    3. **重复提交**：超次数被拒、重复发起认领被拒
    4. **越权**：非本人读认领、普通用户审核、非管理员读审核台、读他人隐藏特征

运行：
    python scripts\\run_backend_tests.py -k test_m3_acceptance
    或 cd backend && python -m unittest tests.test_m3_acceptance -v
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

import conftest


# ---------------------------------------------------------------------------
# 数据库夹具：每个测试类一个独立的内存库
# ---------------------------------------------------------------------------
def _make_engine():
    """创建一个进程内共享的 SQLite 内存引擎。

    ⚠ 必须先注册 MySQL 专有类型的 SQLite 适配：
        模型里用 `mysql.TINYINT`（`is_primary`、`is_degraded`、
        `attributes_edited_by_user` 等布尔列），SQLite 方言无法渲染这个类型，
        直接 `create_all()` 会抛 `CompileError`。
        这里把 TINYINT 映射为 `SMALLINT`，语义上足够（0/1 布尔）。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.dialects import mysql as mysql_dialect
    from sqlalchemy.dialects.sqlite import base as sqlite_base
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.pool import StaticPool

    # 注册一次即可；重复注册同一函数是幂等的
    @compiles(mysql_dialect.TINYINT, "sqlite")
    def _compile_tinyint_sqlite(element, compiler, **kw):  # noqa: ANN001, ARG001
        return "SMALLINT"

    # ⚠ BIGINT 主键在 SQLite 下不会自增：
    #   SQLite 只有 `INTEGER PRIMARY KEY` 才是 rowid 的别名（真正的自增），
    #   `BIGINT PRIMARY KEY` 只是一个普通列，插入时不填 id 会违反 NOT NULL。
    #   本项目所有主键都是 BigInteger，因此必须映射为 INTEGER。
    from sqlalchemy import BigInteger

    @compiles(BigInteger, "sqlite")
    def _compile_bigint_sqlite(element, compiler, **kw):  # noqa: ANN001, ARG001
        return "INTEGER"

    _ = sqlite_base  # 仅用于确保方言模块已加载

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _reset_global_session(engine) -> None:
    """把后端全局会话工厂指向测试引擎。

    `app.db.session` 用模块级单例缓存引擎，因此必须显式替换，
    否则路由会去连真实数据库（本机没有）。
    """
    from sqlalchemy.orm import sessionmaker

    import app.db.session as session_module

    session_module._engine = engine
    session_module._SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


def _create_all(engine) -> None:
    from app.models.base import Base

    # 引入所有模型，确保 metadata 完整
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)


def _reset_business_state(engine, ids: dict) -> None:
    """清理认领记录、复位物品与匹配状态（供 `setUp` 保证测试隔离）。

    只删 `claims` 与 `audit_logs`、复位 `items.status` / `matches`，
    不动用户与物品本身 —— 这样每条用例都从「刚发布、已匹配、无认领」的干净状态开始。
    """
    from sqlalchemy import delete, update
    from sqlalchemy.orm import Session

    from app.core.enums import ItemStatus
    from app.models.audit_log import AuditLog
    from app.models.claim import Claim
    from app.models.item import Item

    with Session(engine) as db:
        db.execute(delete(Claim))
        db.execute(delete(AuditLog))
        db.execute(
            update(Item)
            .where(Item.id.in_([ids["lost"], ids["found"]]))
            .values(status=ItemStatus.MATCHED)
        )
        db.commit()


def _seed(engine):
    """写入最小可用的业务数据：3 个用户 + 一对失物/拾物 + 一条匹配。"""
    from sqlalchemy.orm import Session

    from app.core.enums import AIStatus, ItemStatus, PostType, UserRole, UserStatus
    from app.core.security import hash_password
    from app.models.claim import Claim  # noqa: F401 - 确保表已注册
    from app.models.item import Item, ItemImage
    from app.models.match import Match
    from app.models.user import User

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        student = User(
            username="m3_student", role=UserRole.STUDENT, campus_id="east",
            contact_id="138****8888", password_hash=hash_password("Demo@2026"),
            status=UserStatus.ACTIVE,
        )
        finder = User(
            username="m3_finder", role=UserRole.STUDENT, campus_id="east",
            contact_id="139****6666", password_hash=hash_password("Demo@2026"),
            status=UserStatus.ACTIVE,
        )
        admin = User(
            username="m3_admin", role=UserRole.ADMIN, campus_id="east",
            contact_id="137****0001", password_hash=hash_password("Admin@2026"),
            status=UserStatus.ACTIVE,
        )
        outsider = User(
            username="m3_outsider", role=UserRole.STUDENT, campus_id="east",
            contact_id="136****2222", password_hash=hash_password("Demo@2026"),
            status=UserStatus.ACTIVE,
        )
        db.add_all([student, finder, admin, outsider])
        db.flush()

        lost = Item(
            owner_user_id=student.id, post_type=PostType.LOST, category="无线耳机",
            title="丢失一副黑色无线耳机",
            description="在图书馆三楼自习时落在座位上，充电盒有划痕。",
            color="黑色", brand="Apple", location="图书馆三楼",
            event_time=now - timedelta(hours=2),
            public_features=["椭圆形充电盒"],
            hidden_features=["盒盖内侧有一道3厘米划痕", "蓝牙名称 YiQi-AirPods"],
            contact_id="138****8888",
            ai_attributes={
                "category": "无线耳机", "color": "黑色", "brand": "Apple",
                "visible_features": ["椭圆形充电盒"], "keywords": ["蓝牙耳机", "黑色"],
                "uncertainties": [],
            },
            ai_status=AIStatus.SUCCESS,
            status=ItemStatus.MATCHED,
        )
        found = Item(
            owner_user_id=finder.id, post_type=PostType.FOUND, category="无线耳机",
            title="在图书馆捡到黑色耳机",
            description="在三楼靠窗座位发现一副黑色无线耳机，已交至前台。",
            color="黑色", brand="Apple", location="图书馆三楼",
            event_time=now - timedelta(hours=1),
            public_features=["椭圆形充电盒"],
            hidden_features=["耳机盒右侧有一道划痕", "蓝牙名称 YiQi-AirPods"],
            contact_id="139****6666",
            ai_attributes={
                "category": "无线耳机", "color": "黑色", "brand": "Apple",
                "visible_features": ["椭圆形充电盒"], "keywords": ["蓝牙耳机", "黑色"],
                "uncertainties": [],
            },
            ai_status=AIStatus.SUCCESS,
            status=ItemStatus.MATCHED,
        )
        db.add_all([lost, found])
        db.flush()

        match = Match(
            lost_item_id=lost.id, found_item_id=found.id,
            category_score=1.0, attribute_score=0.92, image_score=0.81,
            time_score=0.95, location_score=1.0, brand_model_score=1.0,
            total_score=0.92,
            evidence=["物品类别一致", "颜色一致"], uncertainties=[], risk_tips=[],
            model_version="baseline-1.0", is_degraded=0,
        )
        db.add(match)
        db.commit()

        return {
            "student": student.id, "finder": finder.id,
            "admin": admin.id, "outsider": outsider.id,
            "lost": lost.id, "found": found.id, "match": match.id,
            "credentials": {
                "student": ("m3_student", "Demo@2026"),
                "finder": ("m3_finder", "Demo@2026"),
                "admin": ("m3_admin", "Admin@2026"),
                "outsider": ("m3_outsider", "Demo@2026"),
            },
        }


class M3TestBase(unittest.TestCase):
    """提供：真实 DB + 真实路由的 TestClient + 已登录的 token。

    ⚠ 测试隔离：每条用例开始前必须清理 `claims` 并复位物品状态。
       原因：业务规则「同一用户对同一 match 只能有一个未关闭的认领」
       （契约 §3.8 红线 3）是**正确行为**，但会让「同类的第二条用例」
       在创建认领时拿到 409。若不清理，测试之间会互相污染，
       表现为一堆与业务无关的 STATE_CONFLICT 失败。
    """

    @classmethod
    def setUpClass(cls):
        conftest.require_app()
        cls.engine = _make_engine()
        _reset_global_session(cls.engine)
        _create_all(cls.engine)
        cls.ids = _seed(cls.engine)

        from fastapi.testclient import TestClient

        from app.main import app

        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.tokens = {}
        for role, (username, password) in cls.ids["credentials"].items():
            cls.tokens[role] = cls._login(username, password)

    def setUp(self):
        """每条用例前清理认领记录并复位物品 / 匹配状态。"""
        _reset_business_state(self.engine, self.ids)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.engine.dispose()
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def _login(cls, username: str, password: str) -> str:
        response = cls.client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        if response.status_code != 200:
            raise AssertionError(f"登录失败 {username}: {response.status_code} {response.text[:200]}")
        return response.json()["data"]["access_token"]

    def auth(self, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    # -- 便捷断言 --------------------------------------------------------
    def assertErrorCode(self, response, code: str, *, status: int | None = None):
        body = response.json()
        self.assertEqual(body.get("code"), code, msg=f"实际响应：{response.text[:400]}")
        if status is not None:
            self.assertEqual(response.status_code, status, msg=f"实际响应：{response.text[:400]}")
        return body


# ---------------------------------------------------------------------------
# 场景 1：正确 —— 答案吻合 → 高可信度、低风险、进入待审核
# ---------------------------------------------------------------------------
class TestCorrectAnswers(M3TestBase):
    """正确答案必须提高可信度，且**不能**自动确权。"""

    def test_create_claim_returns_questions_without_answers(self):
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        self.assertEqual(response.status_code, 201, msg=response.text[:400])
        data = response.json()["data"]

        self.assertIsNotNone(data.get("claim_id"))
        questions = data.get("questions") or []
        self.assertGreaterEqual(len(questions), 2, "问题数量应为 2~3 个")
        self.assertLessEqual(len(questions), 3)

        # 安全红线：响应中不得出现任何答案线索
        blob = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in ("reference_answer", "standard_answer", "answers_hash", "answer_summary"):
            self.assertNotIn(forbidden, blob, f"认领响应泄露字段 {forbidden}")
        for secret in ("盒盖内侧有一道3厘米划痕", "YiQi-AirPods", "盒盖"):
            self.assertNotIn(secret, blob, f"认领响应泄露标准答案：{secret}")

    def test_correct_answers_yield_high_credibility(self):
        claim_id, questions = self._create_claim()

        answers = [
            {"question_id": q["question_id"], "answer": "盒盖内侧有一道3厘米划痕"}
            for q in questions
        ]
        response = self.client.post(
            f"/api/v1/claims/{claim_id}/verify", json={"answers": answers}, headers=self.auth("finder")
        )
        self.assertEqual(response.status_code, 200, msg=response.text[:400])
        data = response.json()["data"]

        self.assertGreaterEqual(float(data.get("verification_score") or 0), 0.5,
                                f"正确回答的验证分应较高，实际 {data.get('verification_score')}")
        self.assertIn(data.get("risk_level"), ("low", "medium"))

        # 红线：验证通过只能进入待审核，绝不自动 claimed
        self.assertIn(data.get("status"), ("pending_review", "claiming"),
                      f"验证后状态应为 pending_review，实际 {data.get('status')}")
        self.assertNotEqual(data.get("status"), "approved")

        # 红线：响应不得回显答案
        blob = json.dumps(response.json(), ensure_ascii=False)
        self.assertNotIn("盒盖内侧有一道3厘米划痕", blob, "验证响应回显了标准答案")

    def test_verification_per_question_reason_has_no_answer(self):
        claim_id, questions = self._create_claim()
        answers = [
            {"question_id": q["question_id"], "answer": "盒盖内侧有一道3厘米划痕"}
            for q in questions
        ]
        response = self.client.post(
            f"/api/v1/claims/{claim_id}/verify", json={"answers": answers}, headers=self.auth("finder")
        )
        data = response.json()["data"]
        for item in data.get("per_question") or []:
            self.assertIsInstance(item.get("matched"), bool)
            self.assertIsInstance(item.get("score"), (int, float))
            reason = item.get("reason") or ""
            self.assertNotIn("3厘米", reason, "理由文本回显了答案片段")

    def _create_claim(self):
        """每条测试用例用独立匹配，避免 unique 约束冲突。"""
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        self.assertEqual(response.status_code, 201, msg=response.text[:400])
        data = response.json()["data"]
        return data["claim_id"], data["questions"]


# ---------------------------------------------------------------------------
# 场景 2：错误 —— 答案不吻合 → 风险升高，绝不通过
# ---------------------------------------------------------------------------
class TestWrongAnswers(M3TestBase):
    """错误答案必须被判为较高风险，且**不得**产生已认领状态。"""

    def test_wrong_answers_do_not_pass(self):
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        self.assertEqual(response.status_code, 201, msg=response.text[:400])
        data = response.json()["data"]
        claim_id, questions = data["claim_id"], data["questions"]

        wrong = [
            {"question_id": q["question_id"], "answer": "完全不记得了，好像没有划痕"}
            for q in questions
        ]
        response = self.client.post(
            f"/api/v1/claims/{claim_id}/verify", json={"answers": wrong}, headers=self.auth("finder")
        )
        self.assertEqual(response.status_code, 200, msg=response.text[:400])
        result = response.json()["data"]

        self.assertLessEqual(float(result.get("verification_score") or 0), 0.5,
                             f"错误回答的验证分应较低，实际 {result.get('verification_score')}")
        self.assertIn(result.get("risk_level"), ("medium", "high"),
                      f"错误回答不应是低风险，实际 {result.get('risk_level')}")
        self.assertNotEqual(result.get("status"), "approved", "错误答案绝不能通过")

        # 复核物品状态未被改动
        response = self.client.get(
            f"/api/v1/items/{self.ids['found']}", headers=self.auth("admin")
        )
        self.assertNotEqual(response.json()["data"].get("status"), "claimed")

    def test_contradicting_answer_is_not_matched(self):
        """明确否认（「没有划痕」）应判为不匹配，而不是因字面相似而误判。"""
        from app.core.security import hash_answer
        from app.services.claim_service import score_answer

        standard = "盒盖内侧有一道3厘米划痕"
        question = {"question_id": "q1", "question_text": "请描述充电盒上的特殊痕迹。"}
        result = score_answer(
            question=question,
            user_answer="没有任何划痕，很干净",
            standard_answer=standard,
            stored_hash=hash_answer(standard),
        )
        self.assertFalse(bool(result.matched), f"矛盾回答被误判为命中：{result}")

    def test_paraphrased_answer_matches(self):
        """同义改写（「盒子右边刮了一道」）应判为命中 —— 容忍合理同义表达。"""
        from app.core.security import hash_answer
        from app.services.claim_service import score_answer

        standard = "盒体右侧有一道划痕"
        result = score_answer(
            question={"question_id": "q1", "question_text": "请描述充电盒上的特殊痕迹。"},
            user_answer="盒子右边刮了一道",
            standard_answer=standard,
            stored_hash=hash_answer(standard),
        )
        self.assertTrue(bool(result.matched), f"同义表达未命中：{result}")


# ---------------------------------------------------------------------------
# 场景 3：重复提交
# ---------------------------------------------------------------------------
class TestDuplicateSubmission(M3TestBase):
    """重复提交必须被拒绝，不能无限重试。"""

    def test_duplicate_claim_on_same_match_rejected(self):
        first = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        self.assertEqual(first.status_code, 201, msg=first.text[:400])

        second = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        self.assertIn(second.status_code, (400, 409),
                      f"重复发起认领应被拒绝，实际 {second.status_code}")
        self.assertIn(second.json().get("code"), ("STATE_CONFLICT", "VALIDATION_ERROR"))

    def test_exceed_max_attempts_rejected(self):
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        self.assertEqual(response.status_code, 201, msg=response.text[:400])
        data = response.json()["data"]
        claim_id, questions = data["claim_id"], data["questions"]
        max_attempts = int(data.get("max_attempts") or 3)

        wrong = [{"question_id": q["question_id"], "answer": "不知道"} for q in questions]
        statuses = []
        for _ in range(max_attempts + 1):
            r = self.client.post(
                f"/api/v1/claims/{claim_id}/verify", json={"answers": wrong}, headers=self.auth("finder")
            )
            statuses.append(r.status_code)

        # 超出次数后必须拒绝（409/400），而不是继续允许提交
        self.assertIn(statuses[-1], (400, 409),
                      f"超过最大次数后仍接受提交，状态码序列={statuses}")

    def test_claim_own_item_rejected(self):
        """不能认领自己发布的物品。"""
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("student")
        )
        self.assertIn(response.status_code, (400, 403, 409),
                      f"认领自己的物品应被拒绝，实际 {response.status_code}: {response.text[:200]}")


# ---------------------------------------------------------------------------
# 场景 4：越权
# ---------------------------------------------------------------------------
class TestUnauthorizedAccess(M3TestBase):
    """四类越权必须全部被拦。"""

    # -- 4.1 读他人认领 ------------------------------------------------
    def test_outsider_cannot_read_claim(self):
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        claim_id = response.json()["data"]["claim_id"]

        response = self.client.get(f"/api/v1/claims/{claim_id}", headers=self.auth("outsider"))
        self.assertIn(response.status_code, (403, 404),
                      f"他人读取认领应被拒绝，实际 {response.status_code}")

    def test_outsider_cannot_verify_others_claim(self):
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        claim_id = response.json()["data"]["claim_id"]

        response = self.client.post(
            f"/api/v1/claims/{claim_id}/verify",
            json={"answers": [{"question_id": "q1", "answer": "随便答"}]},
            headers=self.auth("outsider"),
        )
        self.assertIn(response.status_code, (403, 404),
                      f"他人提交验证应被拒绝，实际 {response.status_code}")

    # -- 4.2 普通用户审核 ----------------------------------------------
    def test_student_cannot_review(self):
        response = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        claim_id = response.json()["data"]["claim_id"]

        for role in ("student", "finder", "outsider"):
            with self.subTest(role=role):
                r = self.client.post(
                    f"/api/v1/admin/claims/{claim_id}/review",
                    json={"action": "approve", "comment": "越权审核尝试"},
                    headers=self.auth(role),
                )
                self.assertIn(r.status_code, (401, 403),
                              f"{role} 越权审核应被拒绝，实际 {r.status_code}")

    # -- 4.3 普通用户读管理员接口 ---------------------------------------
    def test_student_cannot_access_admin_endpoints(self):
        endpoints = [
            "/api/v1/admin/claims",
            f"/api/v1/admin/claims/1",
            "/api/v1/admin/audit-logs",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                r = self.client.get(endpoint, headers=self.auth("student"))
                self.assertIn(r.status_code, (401, 403),
                              f"{endpoint} 应对普通用户返回 401/403，实际 {r.status_code}")

    # -- 4.4 读他人隐藏特征 ---------------------------------------------
    def test_student_cannot_read_others_hidden_features(self):
        response = self.client.get(
            f"/api/v1/items/{self.ids['lost']}", headers=self.auth("outsider")
        )
        self.assertEqual(response.status_code, 200)
        blob = json.dumps(response.json(), ensure_ascii=False)

        self.assertNotIn("hidden_features", blob, "普通用户读到了他人的隐藏特征字段")
        for secret in ("盒盖内侧有一道3厘米划痕", "YiQi-AirPods"):
            self.assertNotIn(secret, blob, f"普通用户读到了隐藏特征内容：{secret}")

    def test_owner_can_read_own_hidden_features(self):
        response = self.client.get(
            f"/api/v1/items/{self.ids['lost']}", headers=self.auth("student")
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIn("hidden_features", data, "发布者本人应能看到自己的隐藏特征")

    def test_admin_can_read_hidden_features(self):
        response = self.client.get(
            f"/api/v1/items/{self.ids['lost']}", headers=self.auth("admin")
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("hidden_features", response.json()["data"], "管理员应能看到隐藏特征")

    def test_no_token_rejected(self):
        for endpoint in ("/api/v1/me/items", "/api/v1/admin/claims"):
            with self.subTest(endpoint=endpoint):
                r = self.client.get(endpoint)
                self.assertEqual(r.status_code, 401, f"{endpoint} 未带 token 应返回 401")


# ---------------------------------------------------------------------------
# 完整闭环：认领 → 验证 → 管理员审核 → 状态归档 + 审计
# ---------------------------------------------------------------------------
class TestReviewClosure(M3TestBase):
    """M3 的最终闭环：审核通过后状态与日志都要正确。"""

    def test_approve_flow_updates_item_and_writes_audit(self):
        # 1) 发起认领并提交正确答案
        created = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        self.assertEqual(created.status_code, 201, msg=created.text[:400])
        data = created.json()["data"]
        claim_id = data["claim_id"]
        answers = [
            {"question_id": q["question_id"], "answer": "盒盖内侧有一道3厘米划痕"}
            for q in data["questions"]
        ]
        verified = self.client.post(
            f"/api/v1/claims/{claim_id}/verify", json={"answers": answers}, headers=self.auth("finder")
        )
        self.assertEqual(verified.status_code, 200, msg=verified.text[:400])

        # 2) 管理员审核台应能读到完整证据
        detail = self.client.get(f"/api/v1/admin/claims/{claim_id}", headers=self.auth("admin"))
        self.assertEqual(detail.status_code, 200, msg=detail.text[:400])
        payload = detail.json()["data"]
        for block in ("claim", "match"):
            self.assertIn(block, payload, f"审核详情缺少 {block} 区块")
        self.assertIn("lost_item", payload)
        self.assertIn("found_item", payload)
        self.assertIn("verification", payload)

        # 3) 审核通过
        reviewed = self.client.post(
            f"/api/v1/admin/claims/{claim_id}/review",
            json={"action": "approve", "comment": "匹配依据与隐藏特征验证均通过"},
            headers=self.auth("admin"),
        )
        self.assertEqual(reviewed.status_code, 200, msg=reviewed.text[:400])
        result = reviewed.json()["data"]
        self.assertEqual(result.get("claim_status"), "approved")
        self.assertEqual(result.get("item_status"), "claimed")

        # 4) 物品状态真的变了
        #
        # ⚠ 断言对象是 **lost** 记录而不是 found：
        #   契约 §3.8 规定「认领」针对的是**被认领的失物记录**
        #   （申请人认领「这是我丢的那件」），因此状态机流转的是 lost_item；
        #   found_item 由拾到者发布，审核通过后不需要改状态。
        #   这一点容易写错 —— 曾把断言放在 found 上，误以为状态没更新。
        lost = self.client.get(f"/api/v1/items/{self.ids['lost']}", headers=self.auth("admin"))
        self.assertEqual(lost.json()["data"].get("status"), "claimed",
                         "审核通过后应把被认领的失物记录置为 claimed")

        found = self.client.get(f"/api/v1/items/{self.ids['found']}", headers=self.auth("admin"))
        self.assertNotEqual(found.json()["data"].get("status"), "claimed",
                            "found 记录不应被置为 claimed（认领对象是 lost 记录）")

        # 5) 审计日志有记录，且不含敏感信息
        logs = self.client.get(
            f"/api/v1/admin/audit-logs?target_type=claim&target_id={claim_id}",
            headers=self.auth("admin"),
        )
        self.assertEqual(logs.status_code, 200)
        entries = logs.json()["data"]["list"]
        self.assertGreater(len(entries), 0, "管理员审核必须写入审计日志")
        actions = {e.get("action") for e in entries}
        self.assertIn("claim.review", actions, f"缺少 claim.review 审计记录，实际 {actions}")

        blob = json.dumps(entries, ensure_ascii=False)
        self.assertNotIn("answers_hash", blob, "审计日志泄露答案哈希")
        self.assertNotIn("3厘米", blob, "审计日志泄露隐藏特征原文")
        self.assertNotIn("Bearer ", blob, "审计日志泄露 Authorization")

    def test_illegal_transition_rejected(self):
        """非法状态跳转必须返回 409（不能从 published 直接到 claimed）。"""
        # 直接审核一条尚未验证的认领，应被状态机拦住
        created = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        claim_id = created.json()["data"]["claim_id"]

        reviewed = self.client.post(
            f"/api/v1/admin/claims/{claim_id}/review",
            json={"action": "approve", "comment": "跳过验证直接通过"},
            headers=self.auth("admin"),
        )
        self.assertIn(reviewed.status_code, (409, 400),
                      f"未验证就通过应被拒绝，实际 {reviewed.status_code}: {reviewed.text[:200]}")

    def test_reject_requires_comment(self):
        created = self.client.post(
            "/api/v1/claims", json={"match_id": self.ids["match"]}, headers=self.auth("finder")
        )
        claim_id = created.json()["data"]["claim_id"]

        for action in ("reject", "request_more_info"):
            with self.subTest(action=action):
                r = self.client.post(
                    f"/api/v1/admin/claims/{claim_id}/review",
                    json={"action": action},
                    headers=self.auth("admin"),
                )
                self.assertIn(r.status_code, (400, 422),
                              f"{action} 缺备注应被拒绝，实际 {r.status_code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
