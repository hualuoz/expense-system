#!/usr/bin/env python3
"""
Workflow 层 — v1.5 架构

2️⃣ 状态机系统:
  - transition() 强约束：所有状态变更必须走此方法
  - 非法状态流转自动拦截
  - 每次状态变更自动写入审计日志

3️⃣ 审批流引擎:
  - 配置化：从 approval_rules / approval_rule_steps 表读取规则
  - 不改代码可改规则
  - 条件分流：按金额匹配规则
  - 支持多节点审批
"""

from app.dao.db import Status, new_id
from app.dao.models import (
    ReimbursementDAO, ExpenseItemDAO, ApprovalFlowDAO,
    ApprovalRuleDAO, AuditLogDAO,
)


# ============ 异常 ============

class WorkflowError(Exception):
    """工作流异常"""
    pass


class StateTransitionError(WorkflowError):
    """状态转换异常"""
    pass


class ImmutableStateError(WorkflowError):
    """不可变状态异常"""
    pass


# ============ 状态机 — 合法转换表 ============

TRANSITIONS = {
    # (当前状态, 操作) → 新状态
    (Status.DRAFT,        "submit")      : Status.SUBMITTED,
    (Status.SUBMITTED,    "start_review"): Status.UNDER_REVIEW,
    (Status.UNDER_REVIEW, "approve")     : Status.APPROVED,
    (Status.UNDER_REVIEW, "reject")      : Status.REJECTED,
    (Status.APPROVED,     "reimburse")   : Status.REIMBURSED,
    (Status.REJECTED,     "resubmit")    : Status.SUBMITTED,
}

# 操作允许角色
ACTION_ROLES = {
    "submit":       ["applicant", "admin"],
    "start_review": ["approver",  "admin"],
    "approve":      ["approver",  "admin"],
    "reject":       ["approver",  "admin"],
    "reimburse":    ["admin"],
    "resubmit":     ["applicant", "admin"],
}


# ============ 状态机核心 ============

class StateMachine:
    """
    统一状态机 — 所有状态变更的唯一入口。

    使用方式:
        StateMachine.transition(rid, "approve", user_id, user_name)
    禁止:
        ReimbursementDAO.update_status(rid, "APPROVED")  # ❌ 绕过状态机
    """

    @staticmethod
    def can_transition(current_status, action):
        return (current_status, action) in TRANSITIONS

    @staticmethod
    def get_next_status(current_status, action):
        key = (current_status, action)
        if key not in TRANSITIONS:
            raise StateTransitionError(f"非法状态转换: {current_status} → {action}")
        return TRANSITIONS[key]

    @staticmethod
    def check_permission(action, user_role):
        allowed = ACTION_ROLES.get(action, [])
        if user_role not in allowed:
            raise WorkflowError(f"角色 '{user_role}' 无权执行 '{action}'")

    @staticmethod
    def transition(rid, action, user_id, user_name, comment=""):
        """
        统一状态转换方法。

        流程:
          1. 读取当前状态
          2. 检查不可变
          3. 检查角色权限
          4. 检查状态转换合法性
          5. 执行转换
          6. 写入审计日志

        返回: {"id", "status", "message"}
        """
        r = ReimbursementDAO.get_by_id(rid)
        if not r:
            raise WorkflowError("报销单不存在")

        current_status = r["status"]

        # (1) 不可变检查
        if current_status in Status.FINAL_STATES:
            raise ImmutableStateError(f"状态 {current_status} 不可变更")

        if current_status == Status.APPROVED and action != "reimburse":
            raise ImmutableStateError(f"状态 {current_status} 仅允许 reimburse 操作")

        # (2) 角色检查已在 Service 层完成（RBAC 权限矩阵）
        # StateMachine 只负责状态转换合法性

        # (3) 转换合法性
        if not StateMachine.can_transition(current_status, action):
            raise StateTransitionError(
                f"非法状态转换: {current_status} 不能执行 {action}"
            )

        # (4) 执行转换
        before_status = current_status

        # ---- action 特殊处理 ----
        if action == "start_review":
            ReimbursementDAO.update_status(rid, Status.UNDER_REVIEW, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "start_review", "reimbursement", rid, before_status, Status.UNDER_REVIEW)
            return {"id": rid, "status": Status.UNDER_REVIEW}

        if action in ("approve", "reject"):
            return StateMachine._handle_approve_reject(rid, action, user_id, user_name, comment, before_status, r)

        if action == "reimburse":
            ReimbursementDAO.update_status(rid, Status.REIMBURSED, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "reimburse", "reimbursement", rid, before_status, Status.REIMBURSED)
            return {"id": rid, "status": Status.REIMBURSED}

        if action == "resubmit":
            return StateMachine._handle_resubmit(rid, user_id, user_name, r)

        if action == "submit":
            ReimbursementDAO.update_status(rid, Status.SUBMITTED, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "submit", "reimbursement", rid, before_status, Status.SUBMITTED)
            return {"id": rid, "status": Status.SUBMITTED}

        raise WorkflowError(f"未知操作: {action}")

    @staticmethod
    def _handle_approve_reject(rid, action, user_id, user_name, comment, before_status, r):
        """处理 approve / reject 操作（含多级审批）"""
        steps = ApprovalFlowDAO.list_by_reimbursement(rid)
        current_step = None
        for s in steps:
            if s["status"] == "PENDING":
                current_step = s
                break

        if not current_step:
            raise WorkflowError("没有待审批步骤")

        step_status = "APPROVED" if action == "approve" else "REJECTED"
        ApprovalFlowDAO.act_step(current_step["id"], step_status, comment, user_id, user_name)

        if action == "approve":
            next_pending = ApprovalFlowDAO.get_pending_step(rid, current_step["step"] + 1)
            if next_pending:
                # 还有下一步
                AuditLogDAO.log(user_id, user_name, "approve_step", "reimbursement", rid,
                                before_status, f"{current_step['step_name']}已通过,等待{next_pending['step_name']}")
                return {"id": rid, "status": Status.UNDER_REVIEW,
                        "message": f"{current_step['step_name']}已通过，等待{next_pending['step_name']}"}
            else:
                # 全部通过
                ReimbursementDAO.update_status(rid, Status.APPROVED, inc_version=True)
                AuditLogDAO.log(user_id, user_name, "approve", "reimbursement", rid, before_status, Status.APPROVED)
                return {"id": rid, "status": Status.APPROVED}
        else:
            ReimbursementDAO.update_status(rid, Status.REJECTED, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "reject", "reimbursement", rid, before_status, Status.REJECTED)
            return {"id": rid, "status": Status.REJECTED}

    @staticmethod
    def _handle_resubmit(rid, user_id, user_name, r):
        """处理重新提交"""
        ReimbursementDAO.update_status(rid, Status.SUBMITTED, inc_version=True)
        # 重置审批流
        new_steps = WorkflowEngine.generate_approval_steps(rid, r["total_amount"])
        ApprovalFlowDAO.delete_by_reimbursement(rid)
        ApprovalFlowDAO.create_steps(new_steps)
        AuditLogDAO.log(user_id, user_name, "resubmit", "reimbursement", rid, r["status"], Status.SUBMITTED)
        return {"id": rid, "status": Status.SUBMITTED}


# ============ 审批流引擎 ============

class WorkflowEngine:
    """
    审批流引擎 — 配置化、可扩展。

    从 approval_rules / approval_rule_steps 表读取规则，
    按金额匹配规则，动态生成审批步骤。
    不改代码，只改数据库即可调整审批流。
    """

    @staticmethod
    def generate_approval_steps(reimbursement_id, total_amount):
        """
        根据金额从数据库匹配审批规则，生成审批步骤。

        如果无匹配规则，使用默认规则兜底。
        """
        rule = ApprovalRuleDAO.get_matching_rule(total_amount)

        if rule:
            rule_steps = ApprovalRuleDAO.get_rule_steps(rule["id"])
            if rule_steps:
                # 从配置读取审批人
                steps = []
                for rs in rule_steps:
                    # 查找审批人信息
                    from app.dao.models import UserDAO
                    approver = UserDAO.get_by_id(rs["approver_id"])
                    approver_name = approver["display_name"] if approver else rs["approver_id"]
                    steps.append({
                        "id": new_id(),
                        "reimbursement_id": reimbursement_id,
                        "step": rs["step"],
                        "step_name": rs["step_name"],
                        "rule_id": rule["id"],
                        "approver_id": rs["approver_id"],
                        "approver_name": approver_name,
                    })
                return steps

        # 兜底: 默认规则
        return WorkflowEngine._default_steps(reimbursement_id, total_amount)

    @staticmethod
    def _default_steps(reimbursement_id, total_amount):
        """兜底默认规则（数据库无规则时使用）"""
        from app.dao.models import UserDAO
        if total_amount <= 500:
            steps_data = [(1, "主管审批", "u002")]
        elif total_amount <= 5000:
            steps_data = [(1, "主管审批", "u002"), (2, "财务审批", "u003")]
        else:
            steps_data = [(1, "主管审批", "u002"), (2, "财务审批", "u003"), (3, "总经理审批", "u004")]

        steps = []
        for step_num, step_name, approver_id in steps_data:
            approver = UserDAO.get_by_id(approver_id)
            steps.append({
                "id": new_id(),
                "reimbursement_id": reimbursement_id,
                "step": step_num,
                "step_name": step_name,
                "rule_id": "default",
                "approver_id": approver_id,
                "approver_name": approver["display_name"] if approver else approver_id,
            })
        return steps

    @staticmethod
    def list_rules():
        """列出所有审批规则（含步骤）"""
        rules = ApprovalRuleDAO.list_all_rules()
        for r in rules:
            r["steps"] = ApprovalRuleDAO.get_rule_steps(r["id"])
        return rules
