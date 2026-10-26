#!/usr/bin/env python
"""端到端验收脚本（部署后自动走完整业务流程）。

这是**云端部署后的核心验证工具**：部署完只跑一条命令，就能确认
「发布 → 匹配 → 认领 → 验证 → 审核 → 归档」整条业务链路真的通了。

覆盖需求 §12.4 部署验收与 §17 答辩脚本的关键步骤，纯标准库实现，
不需要在目标机器上安装任何依赖。

用法：
    python scripts/e2e_demo.py --base-url http://localhost:8000
    python scripts/e2e_demo.py --base-url https://demo.example.com --verbose
    python scripts/e2e_demo.py --base-url http://localhost:8000 --skip-write   # 只跑只读检查

前置条件：
    数据库已初始化（scripts/init_db.sql 或 alembic upgrade head）
    演示账号已导入（scripts/seed_demo_data.py --users-only 或 --all）

退出码：
    0  全流程通过
    1  有步骤失败
    2  环境不可达或前置条件不满足
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# 结果记录
# ---------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = True


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", *, critical: bool = True) -> bool:
        self.steps.append(Step(name, ok, detail, critical))
        icon = "✓" if ok else ("✗" if critical else "!")
        print(f"  {icon} {name}" + (f"  → {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if not s.ok and s.critical]

    @property
    def warnings(self) -> list[Step]:
        return [s for s in self.steps if not s.ok and not s.critical]


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        auth: bool = True,
    ) -> tuple[int, dict]:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json", "X-Request-ID": f"req_e2e_{uuid.uuid4().hex[:8]}"}
        data: bytes | None = None

        if raw_body is not None:
            data = raw_body
            headers["Content-Type"] = content_type or "application/octet-stream"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
                return response.status, _safe_json(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            return exc.code, _safe_json(payload)
        except Exception as exc:  # noqa: BLE001
            return 0, {"code": type(exc).__name__, "message": str(exc)}

    def get(self, path: str, *, auth: bool = True) -> tuple[int, dict]:
        return self._request("GET", path, auth=auth)

    def post(self, path: str, body: dict | None = None, *, auth: bool = True) -> tuple[int, dict]:
        return self._request("POST", path, body=body, auth=auth)

    def post_multipart(
        self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]] | None = None
    ) -> tuple[int, dict]:
        """构造 multipart/form-data 请求（不依赖 requests 库）。"""
        boundary = f"----shiguangE2E{uuid.uuid4().hex[:16]}"
        parts: list[bytes] = []

        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
                + value.encode("utf-8")
                + b"\r\n"
            )

        for name, (filename, content, mime) in (files or {}).items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
                + content
                + b"\r\n"
            )

        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return self._request(
            "POST", path, raw_body=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}"
        )


def _safe_json(text: str) -> dict:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    except Exception:  # noqa: BLE001
        return {"_raw": text[:300]}


def _tiny_png() -> bytes:
    """生成一张最小的合法 PNG（1×1 透明像素）。

    为什么不用固定字节：需要确保能被 Pillow 解析（后端第五重校验要求），
    这里用 zlib 现场构造，避免把二进制硬编码进源码。
    """
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    width = height = 64  # 后端要求 ≥32×32
    raw = b"".join(b"\x00" + b"\xff\xff\xff\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# 业务流程
# ---------------------------------------------------------------------------


def login(client: ApiClient, username: str, password: str) -> str | None:
    status, body = client.post(
        "/api/v1/auth/login", {"username": username, "password": password}, auth=False
    )
    if status == 200 and body.get("code") == "OK":
        return (body.get("data") or {}).get("access_token")
    return None


def run_flow(client: ApiClient, report: Report, *, skip_write: bool, verbose: bool) -> None:
    # ---- 1. 健康检查 ----
    status, body = client.get("/health", auth=False)
    data = body.get("data") or {}
    if status in (200, 503):
        components = {k: (v or {}).get("status") for k, v in data.items() if isinstance(v, dict)}
        report.add("健康检查可访问", True, f"组件状态 {components}")
        db_status = (data.get("database") or {}).get("status")
        report.add("数据库连通", db_status == "ok", f"database={db_status}")
        for name in ("redis", "obs", "algorithm_service", "maas"):
            state = (data.get(name) or {}).get("status")
            report.add(
                f"依赖 {name}",
                state == "ok",
                f"status={state}（可降级，不阻断）",
                critical=False,
            )
    else:
        report.add("健康检查可访问", False, f"HTTP {status}：{body.get('message', body.get('_raw', ''))}")
        return

    # ---- 2. 登录 ----
    admin_token = login(client, "admin_demo", "Admin@2026")
    student_token = login(client, "demo_student", "Demo@2026")
    finder_token = login(client, "demo_finder", "Demo@2026")

    report.add("管理员登录", admin_token is not None)
    report.add("失主账号登录", student_token is not None)
    report.add("拾到者账号登录", finder_token is not None)
    if not (admin_token and student_token and finder_token):
        report.add(
            "演示账号可用",
            False,
            "请先执行 python scripts/seed_demo_data.py --all 导入演示账号",
        )
        return

    # ---- 3. 列表与详情（只读） ----
    client.token = student_token
    status, body = client.get("/api/v1/items?page=1&page_size=50")
    items = (body.get("data") or {}).get("list") or []
    report.add("物品列表可读", status == 200 and isinstance(items, list), f"返回 {len(items)} 条")

    if items:
        item_id = items[0].get("id")
        status, body = client.get(f"/api/v1/items/{item_id}")
        report.add("物品详情可读", status == 200, f"item_id={item_id}")

        # 权限红线：普通用户视角不得含 hidden_features。
        # ⚠ 必须挑“别人的”物品来验：发布者本人看自己的记录是允许看到
        # hidden_features 的（需求 §4.5.1 明确“仅发布者和管理员可见”）。
        # 若直接取列表第一条，命中自己的记录就会误报为泄露。
        me = None
        status_me, body_me = client.get("/api/v1/auth/me")
        if status_me == 200:
            me = (body_me.get("data") or {}).get("id")

        # 演示数据里有两位发布者，但列表接口只返回“当前用户可见”的物品，
        # 翻页翻不到别人的记录属于正常隐私设计。因此改用拾到者账号
        # （demo_finder，owner=2）登录后取一条它自己的物品，再用失主账号去读——
        # 这样必然命中“他人的记录”，才能真正验证脱敏。
        other_item_id = None
        saved_token = client.token
        try:
            body_fd = {"username": "demo_finder", "password": "Demo@2026"}
            st_fd, bd_fd = client.post("/api/v1/auth/login", body_fd, auth=False)
            if st_fd == 200:
                client.token = (bd_fd.get("data") or {}).get("access_token")
                st_l, bd_l = client.get("/api/v1/me/items")
                lst_l = (bd_l.get("data") or {}).get("list") or []
                for it in lst_l:
                    if it.get("owner_user_id") not in (None, me):
                        other_item_id = it.get("id")
                        break
        finally:
            client.token = saved_token

        if other_item_id is None:
            report.add(
                "普通用户视角不含 hidden_features（安全红线）",
                False,
                "无法取得他人的物品 ID，未能验证脱敏",
            )
        else:
            status, body = client.get(f"/api/v1/items/{other_item_id}")
            payload_text = json.dumps(body, ensure_ascii=False)
            report.add(
                "普通用户视角不含 hidden_features（安全红线）",
                "hidden_features" not in payload_text,
                f"item_id={other_item_id}（他人记录，我={me}）",
            )

    if skip_write:
        report.add("跳过写操作（--skip-write）", True, "仅完成只读检查", critical=False)
        return

    # ---- 4. 发布失物 ----
    client.token = student_token
    # 契约 §1.1 要求带时区的 ISO 8601；不带时区会被按 UTC 解释导致校验歧义。
    # 失物：1 小时前丢失；拾物：30 分钟前捡到（都必须是过去时间）。
    now = datetime.now(timezone.utc)
    lost_fields = {
        "post_type": "lost",
        "category": "无线耳机",
        "title": "E2E 测试：丢失一副黑色无线耳机",
        "description": "端到端验收脚本自动创建：在图书馆三楼自习时落在座位上，充电盒有划痕。",
        "location": "图书馆三楼",
        "event_time": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
        "color": "黑色",
        "brand": "Apple",
        "public_features": json.dumps(["椭圆形充电盒"], ensure_ascii=False),
        "hidden_features": json.dumps(["盒盖内侧有一道3厘米划痕", "蓝牙名称 YiQi-AirPods"], ensure_ascii=False),
    }
    status, body = client.post_multipart(
        "/api/v1/items", lost_fields, files={"image": ("e2e-lost.png", _tiny_png(), "image/png")}
    )
    lost_id = (body.get("data") or {}).get("id")
    ai_status = (body.get("data") or {}).get("ai_status")
    report.add(
        "发布失物（含图片）",
        status == 201 and lost_id is not None,
        f"HTTP {status}, item_id={lost_id}, ai_status={ai_status}",
    )

    # ---- 5. 发布拾物 ----
    client.token = finder_token
    found_fields = {
        "post_type": "found",
        "category": "无线耳机",
        "title": "E2E 测试：在图书馆捡到黑色耳机",
        "description": "端到端验收脚本自动创建：在三楼靠窗座位发现一副黑色无线耳机，已交至前台。",
        "location": "图书馆三楼",
        "event_time": (now - timedelta(minutes=30)).isoformat(timespec="seconds"),
        "color": "黑色",
        "brand": "Apple",
        "public_features": json.dumps(["椭圆形充电盒"], ensure_ascii=False),
        "hidden_features": json.dumps(["耳机盒右侧有一道划痕", "蓝牙名称 YiQi-AirPods"], ensure_ascii=False),
    }
    status, body = client.post_multipart("/api/v1/items", found_fields)
    found_id = (body.get("data") or {}).get("id")
    report.add(
        "发布拾物（无图片）",
        status == 201 and found_id is not None,
        f"HTTP {status}, item_id={found_id}",
    )

    if not (lost_id and found_id):
        return

    # ---- 6. 计算匹配 ----
    client.token = student_token
    status, body = client.post(f"/api/v1/items/{lost_id}/match", {"force": True, "top_k": 5})
    report.add("重新计算匹配", status in (200, 202), f"HTTP {status}")

    status, body = client.get(f"/api/v1/items/{lost_id}/matches?top_k=5")
    results = (body.get("data") or {}).get("results") or []
    report.add("获取候选匹配", status == 200, f"返回 {len(results)} 个候选")

    match_id = None
    if results:
        top = results[0]
        scores = top.get("score_detail") or {}
        weights = {"category_score": 0.20, "attribute_score": 0.25, "image_score": 0.20,
                   "time_score": 0.15, "location_score": 0.15, "brand_model_score": 0.05}
        computed = sum(weights.get(k, 0) * float(scores.get(k, 0) or 0) for k in weights)
        total = float(top.get("total_score") or 0)
        report.add(
            "总分等于六维加权和（容差 1e-3）",
            abs(computed - total) < 1e-3,
            f"接口 {total:.4f} vs 手算 {computed:.4f}",
        )
        report.add(
            "匹配结果可解释",
            bool(top.get("evidence")) and "uncertainties" in top,
            f"依据 {len(top.get('evidence') or [])} 条 / 不确定 {len(top.get('uncertainties') or [])} 条",
        )
        report.add("响应含免责声明", bool((body.get("data") or {}).get("disclaimer")), "候选匹配不等于确认归属")
        match_id = top.get("match_id")

    if not match_id:
        report.add("取得 match_id", False, "无候选匹配，后续认领流程无法继续")
        return

    # ---- 7. 发起认领 ----
    client.token = finder_token
    status, body = client.post("/api/v1/claims", {"match_id": match_id})
    claim_data = body.get("data") or {}
    claim_id = claim_data.get("claim_id")
    questions = claim_data.get("questions") or []
    report.add(
        "发起认领",
        status == 201 and claim_id is not None,
        f"HTTP {status}, claim_id={claim_id}, 问题 {len(questions)} 个",
    )
    if not claim_id:
        return

    # 安全红线：问题与响应不得泄露标准答案
    claim_text = json.dumps(body, ensure_ascii=False)
    report.add(
        "认领响应不含标准答案（安全红线）",
        not any(token in claim_text for token in ("reference_answer", "standard_answer", "answers_hash")),
        "只返回 question_id 与 question_text",
    )

    # ---- 8. 提交验证（先错后对） ----
    wrong_answers = {"answers": [{"question_id": q.get("question_id"), "answer": "不记得了"} for q in questions]}
    status, body = client.post(f"/api/v1/claims/{claim_id}/verify", wrong_answers)
    wrong_data = body.get("data") or {}
    report.add(
        "提交错误答案不通过",
        status == 200 and wrong_data.get("risk_level") in ("medium", "high"),
        f"risk_level={wrong_data.get('risk_level')}, 可信度={wrong_data.get('credibility_score')}",
    )

    right_answers = {
        "answers": [
            {"question_id": q.get("question_id"), "answer": "盒盖内侧有一道3厘米划痕"}
            for q in questions
        ]
    }
    status, body = client.post(f"/api/v1/claims/{claim_id}/verify", right_answers)
    right_data = body.get("data") or {}
    report.add(
        "提交答案得到验证结果",
        status == 200 and right_data.get("verification_score") is not None,
        f"验证分={right_data.get('verification_score')}, 可信度={right_data.get('credibility_score')}, "
        f"风险={right_data.get('risk_level')}",
    )
    report.add(
        "验证通过后不自动确权",
        right_data.get("status") in ("pending_review", "claiming"),
        f"status={right_data.get('status')}（应停在待审核）",
    )

    # ---- 9. 管理员审核台 ----
    client.token = admin_token
    status, body = client.get("/api/v1/admin/claims?status=all&page=1&page_size=10")
    pending = (body.get("data") or {}).get("list") or []
    report.add("管理员待办列表可读", status == 200, f"返回 {len(pending)} 条")

    status, body = client.get(f"/api/v1/admin/claims/{claim_id}")
    detail = body.get("data") or {}
    report.add(
        "管理员审核详情可读",
        status == 200 and "match" in detail,
        f"含 match/lost_item/found_item/verification 等区块",
    )

    # ---- 10. 审核通过 ----
    status, body = client.post(
        f"/api/v1/admin/claims/{claim_id}/review",
        {"action": "approve", "comment": "E2E 脚本自动审核：匹配依据与隐藏特征验证均通过"},
    )
    review = body.get("data") or {}
    report.add(
        "管理员审核通过",
        status == 200 and review.get("claim_status") == "approved",
        f"claim_status={review.get('claim_status')}, item_status={review.get('item_status')}",
    )

    # ---- 11. 状态与日志 ----
    # 契约 §3.11 与 admin_service.review_claim 明确：审核通过后状态流转的是
    # **被认领物品（lost_item）** —— match.lost_item，而不是拾物。
    # 因此这里必须查 lost_id；查 found_id 会永远停在 published。
    status, body = client.get(f"/api/v1/items/{lost_id}")
    item_status = (body.get("data") or {}).get("status")
    report.add(
        "物品状态变为已认领",
        item_status == "claimed",
        f"item_id={lost_id}（失物）status={item_status}",
    )

    status, body = client.get(f"/api/v1/admin/audit-logs?target_type=claim&target_id={claim_id}")
    logs = (body.get("data") or {}).get("list") or []
    actions = sorted({entry.get("action") for entry in logs})
    report.add("审核操作写入审计日志", len(logs) > 0, f"{len(logs)} 条：{actions}")

    log_text = json.dumps(logs, ensure_ascii=False)
    report.add(
        "审计日志不含答案与密钥（安全红线）",
        "answers_hash" not in log_text and "Bearer " not in log_text,
        "详情已脱敏",
    )

    # ---- 12. 非法状态跳转被拒绝 ----
    client.token = student_token
    status, body = client.post(
        f"/api/v1/admin/claims/{claim_id}/review", {"action": "approve", "comment": "重复审核测试"}
    )
    report.add(
        "普通用户越权审核被拒绝",
        status in (401, 403),
        f"HTTP {status}（应为 401/403）",
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="端到端验收（云端部署后验证）")
    parser.add_argument("--base-url", required=True, help="服务基址，如 https://demo.example.com")
    parser.add_argument("--timeout", type=float, default=30.0, help="单请求超时（秒）")
    parser.add_argument("--skip-write", action="store_true", help="只跑只读检查，不写入任何数据")
    parser.add_argument("--verbose", action="store_true", help="输出详细过程")
    args = parser.parse_args()

    print("=" * 70)
    print("  拾光 · 端到端验收")
    print("=" * 70)
    print(f"  目标：{args.base_url}")
    print(f"  模式：{'只读检查' if args.skip_write else '完整流程（会写入测试数据）'}")
    print("-" * 70)

    client = ApiClient(args.base_url, args.timeout)
    report = Report()
    run_flow(client, report, skip_write=args.skip_write, verbose=args.verbose)

    print("-" * 70)
    total = len(report.steps)
    failed = report.failed
    warnings = report.warnings
    passed = total - len(failed) - len(warnings)
    print(f"  通过 {passed} / {total} ｜ 警告 {len(warnings)} ｜ 失败 {len(failed)}")
    print("=" * 70)

    if warnings:
        print("\n  ! 非关键项未通过（可降级，不阻断）：")
        for step in warnings:
            print(f"    · {step.name}：{step.detail}")

    if failed:
        print("\n  ✗ 关键步骤失败：")
        for step in failed:
            print(f"    · {step.name}：{step.detail}")
        print("\n  结论：端到端验收未通过")
        return 1

    print("\n  结论：端到端流程全部通过 ✓")
    print("  覆盖：发布 → 匹配 → 认领 → 验证 → 审核 → 状态归档 → 审计日志 → 越权拦截")
    return 0


if __name__ == "__main__":
    sys.exit(main())
