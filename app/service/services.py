#!/usr/bin/env python3
"""
Service 层 — v1.5 架构

所有业务逻辑统一在此层，Controller 禁止写业务逻辑。
Service 负责:
  - 参数校验
  - 幂等性控制
  - 事务协调
  - 权限检查
  - 调用 Workflow / DAO
"""

import hashlib
import json
import time
from app.dao.db import Status, Role, new_id
from app.dao.models import (
    UserDAO, ReimbursementDAO, ExpenseItemDAO,
    ApprovalFlowDAO, AuditLogDAO, AsyncTaskDAO,
)
from app.workflow.engine import StateMachine, WorkflowEngine, WorkflowError, StateTransitionError


# ============ 统一返回 ============

class R:
    @staticmethod
    def ok(data=None, message="ok"):
        return {"code": 0, "message": message, "data": data}

    @staticmethod
    def error(message, code=-1):
        return {"code": code, "message": message, "data": None}

    @staticmethod
    def forbidden(message="无权限"):
        return {"code": 403, "message": message, "data": None}

    @staticmethod
    def not_found(message="资源不存在"):
        return {"code": 404, "message": message, "data": None}

    @staticmethod
    def conflict(message="冲突"):
        return {"code": 409, "message": message, "data": None}


# ============ RBAC 权限矩阵 ============

ROLE_PERMISSIONS = {
    Role.APPLICANT: [
        "reimbursement:create", "reimbursement:read_own", "reimbursement:submit",
        "reimbursement:resubmit", "export:own",
    ],
    Role.APPROVER: [
        "reimbursement:read_all", "reimbursement:approve", "reimbursement:audit",
        "summary:read", "export:all",
    ],
    Role.ADMIN: [
        "reimbursement:read_all", "reimbursement:approve", "reimbursement:audit",
        "reimbursement:delete", "reimbursement:reimburse", "summary:read",
        "export:all", "user:manage", "log:read", "rule:manage", "guard:run",
    ],
}


def check_permission(user_role, permission):
    return permission in ROLE_PERMISSIONS.get(user_role, [])


# ============ 幂等性缓存 ============

_idempotent_cache = {}  # {content_hash: (rid, timestamp)}


def _items_hash(user_id, items_data):
    """计算请求内容哈希（幂等性）"""
    key_data = json.dumps(
        {"uid": user_id, "items": sorted(items_data, key=lambda x: str(x))},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.md5(key_data.encode()).hexdigest()


# ============ 参数校验 ============

def _validate_items(items):
    """校验费用明细"""
    errors = []
    for i, it in enumerate(items):
        idx = i + 1
        # 金额
        try:
            amt = float(it.get("amount", 0))
            if amt <= 0:
                errors.append(f"第{idx}项金额必须大于0")
        except (ValueError, TypeError):
            errors.append(f"第{idx}项金额格式错误")
        # 日期
        if not it.get("date") or not str(it["date"]).strip():
            errors.append(f"第{idx}项缺少日期")
        # 类别
        if not it.get("category") or not str(it["category"]).strip():
            errors.append(f"第{idx}项缺少费用类别")
        # 说明
        if not it.get("description") or not str(it["description"]).strip():
            errors.append(f"第{idx}项缺少说明")
    return errors


# ============ Service 类 ============

class AuthService:
    @staticmethod
    def login(username):
        user = UserDAO.get_by_username(username)
        if not user:
            return R.error("用户不存在")
        return R.ok({
            "user_id": user["id"], "username": user["username"],
            "display_name": user["display_name"], "department": user["department"],
            "role": user["role"],
        })

    @staticmethod
    def get_current_user(user_id):
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.not_found("用户不存在")
        return R.ok({
            "user_id": user["id"], "username": user["username"],
            "display_name": user["display_name"], "department": user["department"],
            "role": user["role"],
        })


class ReimbursementService:
    """报销单业务逻辑（所有业务入口）"""

    @staticmethod
    def create_expense(user_id, items_data, request_id=None):
        """
        创建报销单（含幂等性 + 参数校验 + 事务控制）

        幂等性:
          - 如果提供 request_id，基于 request_id 去重
          - 如果未提供，基于 content hash 在 60s 内去重
        """
        # 1. 用户校验
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        if not check_permission(user["role"], "reimbursement:create"):
            return R.forbidden()

        # 2. 参数校验
        if not items_data:
            return R.error("至少需要一笔费用明细")
        errors = _validate_items(items_data)
        if errors:
            return R.error("; ".join(errors))

        # 3. 幂等性 — request_id
        if request_id:
            existing = ReimbursementDAO.get_by_request_id(request_id)
            if existing:
                return R.ok({
                    "id": existing["id"], "status": existing["status"],
                    "total_amount": existing["total_amount"],
                    "idempotent": True, "message": "重复请求，返回已有记录",
                })

        # 4. 幂等性 — content hash (60s)
        content_hash = _items_hash(user_id, items_data)
        if content_hash in _idempotent_cache:
            cached_rid, cached_time = _idempotent_cache[content_hash]
            if time.time() - cached_time < 60:
                existing = ReimbursementDAO.get_by_id(cached_rid)
                if existing:
                    return R.ok({
                        "id": existing["id"], "status": existing["status"],
                        "total_amount": existing["total_amount"],
                        "idempotent": True, "message": "60秒内重复请求",
                    })

        # 5. 创建
        rid = new_id()
        total = sum(float(it.get("amount", 0)) for it in items_data)

        # 写入报销单
        ReimbursementDAO.create(rid, user_id, user["display_name"], user["department"], total, request_id)

        # 写入费用明细
        db_items = []
        for it in items_data:
            it["id"] = new_id()
            it["reimbursement_id"] = rid
            it["amount"] = float(it.get("amount", 0))
            db_items.append(it)
        ExpenseItemDAO.delete_by_reimbursement(rid)
        ExpenseItemDAO.insert_items(db_items)

        # 生成审批步骤
        steps = WorkflowEngine.generate_approval_steps(rid, total)
        ApprovalFlowDAO.create_steps(steps)

        # 更新状态: DRAFT → SUBMITTED (通过状态机)
        ReimbursementDAO.update_status(rid, Status.SUBMITTED, inc_version=True)

        # 记录幂等缓存
        _idempotent_cache[content_hash] = (rid, time.time())

        # 审计日志
        AuditLogDAO.log(user_id, user["display_name"], "create", "reimbursement", rid, "",
                        {"id": rid, "total": total, "request_id": request_id})

        return R.ok({
            "id": rid, "status": Status.SUBMITTED,
            "total_amount": total, "approval_steps": len(steps),
        })

    @staticmethod
    def submit_expense(rid, user_id):
        """提交报销单（DRAFT → SUBMITTED）"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        r = ReimbursementDAO.get_by_id(rid)
        if not r:
            return R.not_found()
        if r["applicant_id"] != user_id and user["role"] != Role.ADMIN:
            return R.forbidden()
        try:
            result = StateMachine.transition(rid, "submit", user_id, user["display_name"])
            return R.ok(result)
        except WorkflowError as e:
            return R.error(str(e))

    @staticmethod
    def approve_expense(rid, user_id, comment=""):
        """审批通过"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        if not check_permission(user["role"], "reimbursement:approve"):
            return R.forbidden()
        try:
            result = StateMachine.transition(rid, "approve", user_id, user["display_name"], comment)
            return R.ok(result)
        except WorkflowError as e:
            return R.error(str(e))

    @staticmethod
    def reject_expense(rid, user_id, comment=""):
        """审批驳回"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        if not check_permission(user["role"], "reimbursement:approve"):
            return R.forbidden()
        if not comment.strip():
            return R.error("驳回必须填写理由")
        try:
            result = StateMachine.transition(rid, "reject", user_id, user["display_name"], comment)
            return R.ok(result)
        except WorkflowError as e:
            return R.error(str(e))

    @staticmethod
    def start_review(rid, user_id):
        """开始审核（SUBMITTED → UNDER_REVIEW）"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        if not check_permission(user["role"], "reimbursement:approve"):
            return R.forbidden()
        try:
            result = StateMachine.transition(rid, "start_review", user_id, user["display_name"])
            return R.ok(result)
        except WorkflowError as e:
            return R.error(str(e))

    @staticmethod
    def reimburse_expense(rid, user_id):
        """确认打款（APPROVED → REIMBURSED）"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        if not check_permission(user["role"], "reimbursement:reimburse"):
            return R.forbidden()
        try:
            result = StateMachine.transition(rid, "reimburse", user_id, user["display_name"])
            return R.ok(result)
        except WorkflowError as e:
            return R.error(str(e))

    @staticmethod
    def resubmit_expense(rid, user_id):
        """重新提交（REJECTED → SUBMITTED）"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        r = ReimbursementDAO.get_by_id(rid)
        if not r:
            return R.not_found()
        if r["applicant_id"] != user_id and user["role"] != Role.ADMIN:
            return R.forbidden()
        try:
            result = StateMachine.transition(rid, "resubmit", user_id, user["display_name"])
            return R.ok(result)
        except WorkflowError as e:
            return R.error(str(e))

    @staticmethod
    def list_expenses(user_id, status="ALL"):
        """列表（按角色过滤）"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        if user["role"] in (Role.ADMIN, Role.APPROVER):
            data = ReimbursementDAO.list_by_status(status)
        else:
            data = ReimbursementDAO.list_by_status(status, applicant_id=user_id)
        for d in data:
            d["status_display"] = Status.DISPLAY.get(d["status"], d["status"])
        return R.ok(data)

    @staticmethod
    def get_detail(rid, user_id):
        """详情"""
        user = UserDAO.get_by_id(user_id)
        r = ReimbursementDAO.get_by_id(rid)
        if not r:
            return R.not_found()
        if user["role"] == Role.APPLICANT and r["applicant_id"] != user_id:
            return R.forbidden()
        items = ExpenseItemDAO.list_by_reimbursement(rid)
        steps = ApprovalFlowDAO.list_by_reimbursement(rid)
        r["status_display"] = Status.DISPLAY.get(r["status"], r["status"])
        r["items"] = items
        r["approval_steps"] = steps
        r["is_immutable"] = r["status"] in Status.FINAL_STATES or (r["status"] == Status.APPROVED)
        return R.ok(r)

    @staticmethod
    def delete_expense(rid, user_id):
        """删除"""
        user = UserDAO.get_by_id(user_id)
        if not user or not check_permission(user["role"], "reimbursement:delete"):
            return R.forbidden()
        r = ReimbursementDAO.get_by_id(rid)
        if not r:
            return R.not_found()
        if r["status"] in (Status.APPROVED, Status.REIMBURSED):
            return R.error("已审批的报销单不可删除")
        ReimbursementDAO.delete(rid)
        AuditLogDAO.log(user_id, user["display_name"], "delete", "reimbursement", rid, r["status"], "")
        return R.ok()

    @staticmethod
    def export_expense(rid, user_id):
        """导出（异步任务）"""
        user = UserDAO.get_by_id(user_id)
        if not user:
            return R.error("用户不存在")
        r = ReimbursementDAO.get_by_id(rid)
        if not r:
            return R.not_found()
        if user["role"] == Role.APPLICANT and r["applicant_id"] != user_id:
            return R.forbidden()
        # 提交异步导出
        from tasks.task_runner import submit_export_task
        task_id = submit_export_task(rid, user_id)
        AuditLogDAO.log(user_id, user["display_name"], "export", "reimbursement", rid, "", "")
        return R.ok({"task_id": task_id, "status": "PENDING"})


class AuditService:
    @staticmethod
    def get_logs(user_id, target_id=None, limit=100):
        user = UserDAO.get_by_id(user_id)
        if not user or not check_permission(user["role"], "log:read"):
            return R.forbidden()
        if target_id:
            return R.ok(AuditLogDAO.list_by_target(target_id, limit))
        return R.ok(AuditLogDAO.list_all(limit))


class SummaryService:
    @staticmethod
    def get_summary(user_id):
        user = UserDAO.get_by_id(user_id)
        if not user or not check_permission(user["role"], "summary:read"):
            return R.forbidden()
        all_r = ReimbursementDAO.list_by_status("ALL")
        stats = {"total_claims": len(all_r), "total_amount": sum(r["total_amount"] for r in all_r), "by_status": {}}
        for r in all_r:
            s = r["status"]
            if s not in stats["by_status"]:
                stats["by_status"][s] = {"count": 0, "amount": 0, "display": Status.DISPLAY.get(s, s)}
            stats["by_status"][s]["count"] += 1
            stats["by_status"][s]["amount"] += r["total_amount"]
        all_items = []
        for r in all_r:
            all_items.extend(ExpenseItemDAO.list_by_reimbursement(r["id"]))
        cat_stats = {}
        for it in all_items:
            cat = it["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"category": cat, "count": 0, "amount": 0}
            cat_stats[cat]["count"] += 1
            cat_stats[cat]["amount"] += it["amount"]
        stats["by_category"] = sorted(cat_stats.values(), key=lambda x: -x["amount"])
        return R.ok(stats)


class DataGuardService:
    @staticmethod
    def run_check(user_id):
        """执行数据一致性检查"""
        user = UserDAO.get_by_id(user_id)
        if not user or not check_permission(user["role"], "guard:run"):
            return R.forbidden()
        from app.dao.db import DataGuard
        issues = DataGuard.check_consistency()
        AuditLogDAO.log(user_id, user["display_name"], "guard_check", "system", "all", "", f"{len(issues)} issues found")
        return R.ok({"total_issues": len(issues), "issues": issues})


class RuleService:
    @staticmethod
    def list_rules(user_id):
        user = UserDAO.get_by_id(user_id)
        if not user or not check_permission(user["role"], "rule:manage"):
            return R.forbidden()
        return R.ok(WorkflowEngine.list_rules())
