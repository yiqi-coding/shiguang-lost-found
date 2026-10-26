"""状态机测试（契约 §2.3 / 附录 B）。

覆盖：

- 合法流转全部通过；
- 非法跳转（如 `published → claimed`）抛 `StateConflictError`；
- 终态 `closed` 无下一状态；
- `next_status_for_review` 三种审核动作映射正确。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.core.enums import ClaimStatus, ItemStatus, ReviewAction
from app.core.errors import StateConflictError
from app.services import state_machine
from app.services.state_machine import (
    ITEM_TRANSITIONS,
    allowed_next_statuses,
    assert_claim_transition,
    assert_item_transition,
    can_transition_item,
    is_terminal_item_status,
    next_status_for_review,
)


class TestItemTransitions(unittest.TestCase):
    """物品状态流转。"""

    def test_all_legal_transitions_pass(self) -> None:
        """契约 §2.3 中列出的每一条合法流转都必须通过。"""
        expected: dict[str, set[str]] = {
            ItemStatus.PUBLISHED: {ItemStatus.MATCHED, ItemStatus.SUSPENDED, ItemStatus.CLOSED},
            ItemStatus.MATCHED: {ItemStatus.CLAIMING, ItemStatus.SUSPENDED, ItemStatus.CLOSED},
            ItemStatus.CLAIMING: {
                ItemStatus.PENDING_REVIEW,
                ItemStatus.MATCHED,
                ItemStatus.SUSPENDED,
            },
            ItemStatus.PENDING_REVIEW: {
                ItemStatus.CLAIMED,
                ItemStatus.REJECTED,
                ItemStatus.REQUEST_MORE_INFO,
                ItemStatus.DISPUTED,
            },
            ItemStatus.REQUEST_MORE_INFO: {
                ItemStatus.PENDING_REVIEW,
                ItemStatus.REJECTED,
                ItemStatus.CLOSED,
            },
            ItemStatus.CLAIMED: {ItemStatus.CLOSED},
            ItemStatus.REJECTED: {ItemStatus.CLAIMING, ItemStatus.CLOSED},
            ItemStatus.SUSPENDED: {ItemStatus.PUBLISHED, ItemStatus.MATCHED},
            ItemStatus.DISPUTED: {ItemStatus.CLAIMED, ItemStatus.REJECTED, ItemStatus.CLOSED},
            ItemStatus.CLOSED: set(),
        }
        for current, targets in expected.items():
            with self.subTest(current=current):
                self.assertEqual(ITEM_TRANSITIONS[current], targets)
                for target in targets:
                    assert_item_transition(current, target)  # 不抛异常即通过

    def test_illegal_jump_published_to_claimed(self) -> None:
        """`published → claimed` 必须抛 409（禁止绕过审核直接确权）。"""
        self.assertFalse(can_transition_item(ItemStatus.PUBLISHED, ItemStatus.CLAIMED))
        with self.assertRaises(StateConflictError) as ctx:
            assert_item_transition(ItemStatus.PUBLISHED, ItemStatus.CLAIMED)
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(ctx.exception.code, "STATE_CONFLICT")

    def test_other_illegal_jumps(self) -> None:
        """其它典型非法跳转。"""
        cases = [
            (ItemStatus.CLOSED, ItemStatus.PUBLISHED),  # 终态不可复活
            (ItemStatus.PUBLISHED, ItemStatus.CLAIMING),  # 必须先 matched
            (ItemStatus.CLAIMED, ItemStatus.CLAIMING),  # 已认领不可再被认领
            (ItemStatus.REQUEST_MORE_INFO, ItemStatus.CLAIMED),  # 补充信息不能直接通过
        ]
        for current, target in cases:
            with self.subTest(current=current, target=target):
                with self.assertRaises(StateConflictError):
                    assert_item_transition(current, target)

    def test_same_status_is_noop(self) -> None:
        """相同状态的「流转」视为幂等，不抛异常。"""
        assert_item_transition(ItemStatus.PUBLISHED, ItemStatus.PUBLISHED)

    def test_closed_is_terminal(self) -> None:
        """终态 `closed` 没有下一状态。"""
        self.assertTrue(is_terminal_item_status(ItemStatus.CLOSED))
        self.assertEqual(allowed_next_statuses(ItemStatus.CLOSED), [])
        self.assertEqual(ITEM_TRANSITIONS[ItemStatus.CLOSED], set())
        self.assertFalse(is_terminal_item_status(ItemStatus.PUBLISHED))

    def test_allowed_next_statuses_sorted(self) -> None:
        """`allowed_next_statuses` 返回排序后的列表（供前端渲染可用操作）。"""
        self.assertEqual(allowed_next_statuses(ItemStatus.PUBLISHED), sorted(ITEM_TRANSITIONS["published"]))

    def test_unknown_status_has_no_transition(self) -> None:
        """未知状态不得被当作合法起点。"""
        self.assertFalse(can_transition_item("not_a_status", ItemStatus.MATCHED))
        with self.assertRaises(StateConflictError):
            assert_item_transition("not_a_status", ItemStatus.MATCHED)


class TestClaimTransitions(unittest.TestCase):
    """认领状态流转（契约 §2.4）。"""

    def test_legal_claim_transitions(self) -> None:
        legal = [
            (ClaimStatus.CLAIMING, ClaimStatus.PENDING_REVIEW),
            (ClaimStatus.CLAIMING, ClaimStatus.REJECTED),
            (ClaimStatus.PENDING_REVIEW, ClaimStatus.APPROVED),
            (ClaimStatus.PENDING_REVIEW, ClaimStatus.REJECTED),
            (ClaimStatus.PENDING_REVIEW, ClaimStatus.REQUEST_MORE_INFO),
            (ClaimStatus.REQUEST_MORE_INFO, ClaimStatus.PENDING_REVIEW),
            (ClaimStatus.REQUEST_MORE_INFO, ClaimStatus.REJECTED),
        ]
        for current, target in legal:
            with self.subTest(current=current, target=target):
                assert_claim_transition(current, target)

    def test_illegal_claim_transitions(self) -> None:
        illegal = [
            (ClaimStatus.CLAIMING, ClaimStatus.APPROVED),  # 未验证不得直接通过
            (ClaimStatus.APPROVED, ClaimStatus.REJECTED),  # 终态
            (ClaimStatus.REJECTED, ClaimStatus.APPROVED),  # 终态
        ]
        for current, target in illegal:
            with self.subTest(current=current, target=target):
                with self.assertRaises(StateConflictError):
                    assert_claim_transition(current, target)


class TestReviewActions(unittest.TestCase):
    """管理员审核动作 → 状态映射（契约 §2.7）。"""

    def test_next_status_for_review(self) -> None:
        self.assertEqual(
            next_status_for_review(ReviewAction.APPROVE),
            (ClaimStatus.APPROVED, ItemStatus.CLAIMED),
        )
        self.assertEqual(
            next_status_for_review(ReviewAction.REJECT),
            (ClaimStatus.REJECTED, ItemStatus.REJECTED),
        )
        self.assertEqual(
            next_status_for_review(ReviewAction.REQUEST_MORE_INFO),
            (ClaimStatus.REQUEST_MORE_INFO, ItemStatus.REQUEST_MORE_INFO),
        )

    def test_unknown_action_raises_conflict(self) -> None:
        with self.assertRaises(StateConflictError):
            next_status_for_review("delete_everything")

    def test_review_targets_are_reachable_from_pending_review(self) -> None:
        """三种动作的目标状态都必须是 `pending_review` 的合法下一状态。"""
        for action in (ReviewAction.APPROVE, ReviewAction.REJECT, ReviewAction.REQUEST_MORE_INFO):
            claim_target, item_target = next_status_for_review(action)
            with self.subTest(action=action):
                assert_claim_transition(ClaimStatus.PENDING_REVIEW, claim_target)
                assert_item_transition(ItemStatus.PENDING_REVIEW, item_target)

    def test_claimed_only_reachable_through_approve(self) -> None:
        """红线：`claimed` 只能由 `pending_review` 经 approve 产生。"""
        sources = [status for status, targets in ITEM_TRANSITIONS.items() if ItemStatus.CLAIMED in targets]
        self.assertEqual(sources, [ItemStatus.PENDING_REVIEW, ItemStatus.DISPUTED])
        self.assertEqual(next_status_for_review(ReviewAction.APPROVE)[1], ItemStatus.CLAIMED)


class TestModuleIntegrity(unittest.TestCase):
    """模块级一致性。"""

    def test_transition_table_covers_all_statuses(self) -> None:
        for status in ItemStatus:
            self.assertIn(status, state_machine.ITEM_TRANSITIONS)

    def test_no_transition_targets_unknown_status(self) -> None:
        known = {status.value for status in ItemStatus}
        for current, targets in ITEM_TRANSITIONS.items():
            for target in targets:
                self.assertIn(target, known, msg=f"{current} → {target} 目标状态未定义")


if __name__ == "__main__":
    unittest.main(verbosity=2)
