#!/usr/bin/env python3
"""
Workflow 层 — v2.0 架构

状态机系统:
  - 完整审批流: DRAFT → SUBMITTED → DEPT_APPROVING → FINANCE_REVIEWING → APPROVED → PAYMENT_PENDING → PAID → ARCHIVED
  - 禁止越级流转
  - 不相容岗位分离: 同一人不能同时完成提交、审批、财务审核、付款
  - 每次状态变更自动写入审计日志

审批流引擎:
  - 配置化：从 approval_rules / approval_rule_steps 表读取规则
  - 按金额匹配规则，动态生成审批步骤
"""

from app.dao.db import Status, Role, new_id
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


class PermissionError(WorkflowError):
    """权限异常"""
    pass


# ============ 状态机 — 合法转换表 v2.0 ============

TRANSITIONS = {
    # (当前状态, 操作) → 新状态
    (Status.DRAFT,              "submit")           : Status.SUBMITTED,
    (Status.SUBMITTED,          "start_review")     : Status.DEPT_APPROVING,
    (Status.DEPT_APPROVING,     "dept_approve")     : Status.FINANCE_REVIEWING,   # 部门审批通过 → 财务审核
    (Status.DEPT_APPROVING,     "reject")           : Status.REJECTED,
    (Status.FINANCE_REVIEWING,  "finance_approve")  : Status.APPROVED,           # 财务审核通过
    (Status.FINANCE_REVIEWING,  "reject")           : Status.REJECTED,
    (Status.APPROVED,           "mark_payment")     : Status.PAYMENT_PENDING,    # 标记待付款
    (Status.PAYMENT_PENDING,    "pay")               : Status.PAID,               # 出纳确认打款
    (Status.PAID,               "archive")           : Status.ARCHIVED,           # 归档
    (Status.REJECTED,           "resubmit")          : Status.SUBMITTED,          # 重新提交
}

# 操作允许角色（严格RBAC）
ACTION_ROLES = {
    "submit":          [Role.APPLICANT],
    "start_review":    [Role.DEPT_MANAGER, Role.ADMIN],
    "dept_approve":    [Role.DEPT_MANAGER, Role.ADMIN],
    "finance_approve": [Role.FINANCE_REVIEWER, Role.ADMIN],
    "reject":          [Role.DEPT_MANAGER, Role.FINANCE_REVIEWER, Role.ADMIN],
    "mark_payment":    [Role.FINANCE_REVIEWER, Role.ADMIN],
    "pay":             [Role.CASHIER, Role.ADMIN],
    "archive":         [Role.CASHIER, Role.ADMIN],
    "resubmit":        [Role.APPLICANT],
}

# 不相容岗位: 同一人不能完成以下配对中的操作
INCOMPATIBLE_ACTIONS = {
    "submit":          ["dept_approve", "finance_approve", "pay"],
    "dept_approve":    ["finance_approve", "pay"],
    "finance_approve": ["pay"],
}


# ============ 状态机核心 v2.0 ============

class StateMachine:
    """
    统一状态机 — 所有状态变更的唯一入口。
    
    使用方式:
        StateMachine.transition(rid, "dept_approve", user_id, user_name, user_role, comment)
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
    def check_role(action, user_role):
        """检查角色是否有权执行操作"""
        allowed = ACTION_ROLES.get(action, [])
        if user_role not in allowed:
            raise PermissionError(f"角色 '{user_role}' 无权执行 '{action}'")

    @staticmethod
    def check_incompatible(rid, action, user_id):
        """检查不相容岗位：同一人不能完成提交+审批+付款"""
        from app.dao.models import AuditLogDAO
        # 获取此报销单的操作历史
        logs = AuditLogDAO.list_by_target(rid, 200)
        incompatible = INCOMPATIBLE_ACTIONS.get(action, [])
        for log in logs:
            if log["user_id"] == user_id and log["action"] in incompatible:
                raise PermissionError(
                    f"不相容岗位: 您已执行了 '{log['action']}' 操作，不能再执行 '{action}'"
                )

    @staticmethod
    def transition(rid, action, user_id, user_name, user_role="", comment="", user_agent=""):
        """
        统一状态转换方法。
        
        流程:
          1. 读取当前状态
          2. 检查不可变
          3. 检查角色权限
          4. 检查不相容岗位
          5. 检查状态转换合法性
          6. 执行转换
          7. 写入审计日志
        """
        r = ReimbursementDAO.get_by_id(rid)
        if not r:
            raise WorkflowError("报销单不存在")

        current_status = r["status"]

        # (1) 不可变检查 — ARCHIVED 是最终态，PAID 仅允许 archive
        if current_status == Status.ARCHIVED:
            raise ImmutableStateError(f"状态 {Status.DISPLAY.get(current_status, current_status)} 不可变更")

        # PAID 只允许 archive
        if current_status == Status.PAID and action != "archive":
            raise ImmutableStateError(f"已打款状态只允许归档操作")

        # (2) 角色检查
        StateMachine.check_role(action, user_role)

        # (3) 不相容岗位检查
        StateMachine.check_incompatible(rid, action, user_id)

        # (4) 转换合法性
        if not StateMachine.can_transition(current_status, action):
            raise StateTransitionError(
                f"非法状态转换: {Status.DISPLAY.get(current_status, current_status)} 不能执行 {action}"
            )

        # (5) 执行转换
        before_status = current_status

        # ---- 特殊处理: 部门审批 / 财务审核 / 付款 / 归档 ----
        if action == "start_review":
            return StateMachine._handle_start_review(rid, user_id, user_name, user_role, comment, user_agent, before_status, r)

        if action == "dept_approve":
            return StateMachine._handle_dept_approve(rid, user_id, user_name, user_role, comment, user_agent, before_status, r)

        if action == "finance_approve":
            return StateMachine._handle_finance_approve(rid, user_id, user_name, user_role, comment, user_agent, before_status, r)

        if action == "reject":
            return StateMachine._handle_reject(rid, user_id, user_name, user_role, comment, user_agent, before_status, r)

        if action == "mark_payment":
            ReimbursementDAO.update_status(rid, Status.PAYMENT_PENDING, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "mark_payment", "reimbursement", rid,
                            before_data=before_status, after_data=Status.PAYMENT_PENDING,
                            user_role=user_role, user_agent=user_agent,
                            before_status=before_status, after_status=Status.PAYMENT_PENDING)
            return {"id": rid, "status": Status.PAYMENT_PENDING}

        if action == "pay":
            ReimbursementDAO.update_status(rid, Status.PAID, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "pay", "reimbursement", rid,
                            before_data=before_status, after_data=Status.PAID,
                            user_role=user_role, user_agent=user_agent,
                            before_status=before_status, after_status=Status.PAID)
            return {"id": rid, "status": Status.PAID}

        if action == "archive":
            ReimbursementDAO.update_status(rid, Status.ARCHIVED, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "archive", "reimbursement", rid,
                            before_data=before_status, after_data=Status.ARCHIVED,
                            user_role=user_role, user_agent=user_agent,
                            before_status=before_status, after_status=Status.ARCHIVED)
            return {"id": rid, "status": Status.ARCHIVED}

        if action == "resubmit":
            return StateMachine._handle_resubmit(rid, user_id, user_name, user_role, user_agent, r)

        if action == "submit":
            ReimbursementDAO.update_status(rid, Status.SUBMITTED, inc_version=True)
            AuditLogDAO.log(user_id, user_name, "submit", "reimbursement", rid,
                            before_data=before_status, after_data=Status.SUBMITTED,
                            user_role=user_role, user_agent=user_agent,
                            before_status=before_status, after_status=Status.SUBMITTED)
            return {"id": rid, "status": Status.SUBMITTED}

        raise WorkflowError(f"未知操作: {action}")

    @staticmethod
    def _handle_start_review(rid, user_id, user_name, user_role, comment, user_agent, before_status, r):
        """开始审核: SUBMITTED → DEPT_APPROVING"""
        # 检查: 部门负责人不能审批自己提交的
        if r["applicant_id"] == user_id:
            raise PermissionError("不能审批自己提交的报销")

        ReimbursementDAO.update_status(rid, Status.DEPT_APPROVING, inc_version=True)
        AuditLogDAO.log(user_id, user_name, "start_review", "reimbursement", rid,
                        before_data=before_status, after_data=Status.DEPT_APPROVING,
                        user_role=user_role, user_agent=user_agent,
                        before_status=before_status, after_status=Status.DEPT_APPROVING)
        return {"id": rid, "status": Status.DEPT_APPROVING}

    @staticmethod
    def _handle_dept_approve(rid, user_id, user_name, user_role, comment, user_agent, before_status, r):
        """部门审批: DEPT_APPROVING → FINANCE_REVIEWING 或 驳回"""
        if r["applicant_id"] == user_id:
            raise PermissionError("不能审批自己提交的报销")

        steps = ApprovalFlowDAO.list_by_reimbursement(rid)
        current_step = None
        for s in steps:
            if s["status"] == "PENDING":
                current_step = s
                break

        if not current_step:
            raise WorkflowError("没有待审批步骤")

        ApprovalFlowDAO.act_step(current_step["id"], "APPROVED", comment or "部门审批通过", user_id, user_name, user_agent)

        # 检查是否还有下一步部门审批
        next_pending = ApprovalFlowDAO.get_first_pending(rid)
        if next_pending and "部门" in next_pending["step_name"]:
            AuditLogDAO.log(user_id, user_name, "dept_approve_step", "reimbursement", rid,
                            before_data=before_status, after_data=Status.DEPT_APPROVING,
                            user_role=user_role, user_agent=user_agent,
                            before_status=before_status, after_status=Status.DEPT_APPROVING)
            return {"id": rid, "status": Status.DEPT_APPROVING,
                    "message": f"{current_step['step_name']}已通过，等待{next_pending['step_name']}"}

        # 部门审批全部完成 → 转财务审核
        ReimbursementDAO.update_status(rid, Status.FINANCE_REVIEWING, inc_version=True)
        AuditLogDAO.log(user_id, user_name, "dept_approve", "reimbursement", rid,
                        before_data=before_status, after_data=Status.FINANCE_REVIEWING,
                        user_role=user_role, user_agent=user_agent,
                        before_status=before_status, after_status=Status.FINANCE_REVIEWING)
        return {"id": rid, "status": Status.FINANCE_REVIEWING}

    @staticmethod
    def _handle_finance_approve(rid, user_id, user_name, user_role, comment, user_agent, before_status, r):
        """财务审核: FINANCE_REVIEWING → APPROVED"""
        steps = ApprovalFlowDAO.list_by_reimbursement(rid)
        current_step = None
        for s in steps:
            if s["status"] == "PENDING":
                current_step = s
                break

        if not current_step:
            raise WorkflowError("没有待审批步骤")

        ApprovalFlowDAO.act_step(current_step["id"], "APPROVED", comment or "财务审核通过", user_id, user_name, user_agent)

        # 检查是否还有下一步
        next_pending = ApprovalFlowDAO.get_first_pending(rid)
        if next_pending:
            AuditLogDAO.log(user_id, user_name, "finance_approve_step", "reimbursement", rid,
                            before_data=before_status, after_data=Status.FINANCE_REVIEWING,
                            user_role=user_role, user_agent=user_agent,
                            before_status=before_status, after_status=Status.FINANCE_REVIEWING)
            return {"id": rid, "status": Status.FINANCE_REVIEWING,
                    "message": f"{current_step['step_name']}已通过，等待{next_pending['step_name']}"}

        # 全部通过 → APPROVED
        ReimbursementDAO.update_status(rid, Status.APPROVED, inc_version=True)
        AuditLogDAO.log(user_id, user_name, "finance_approve", "reimbursement", rid,
                        before_data=before_status, after_data=Status.APPROVED,
                        user_role=user_role, user_agent=user_agent,
                            before_status=before_status, after_status=Status.APPROVED)
        return {"id": rid, "status": Status.APPROVED}

    @staticmethod
    def _handle_reject(rid, user_id, user_name, user_role, comment, user_agent, before_status, r):
        """驳回"""
        if not comment or not comment.strip():
            raise WorkflowError("驳回必须填写理由")

        if r["applicant_id"] == user_id:
            raise PermissionError("不能驳回自己提交的报销")

        # 记录审批步骤
        current_step = ApprovalFlowDAO.get_first_pending(rid)
        if current_step:
            ApprovalFlowDAO.act_step(current_step["id"], "REJECTED", comment, user_id, user_name, user_agent)

        ReimbursementDAO.update_status(rid, Status.REJECTED, inc_version=True)
        AuditLogDAO.log(user_id, user_name, "reject", "reimbursement", rid,
                        before_data=before_status, after_data=Status.REJECTED,
                        user_role=user_role, user_agent=user_agent,
                        before_status=before_status, after_status=Status.REJECTED)
        return {"id": rid, "status": Status.REJECTED}

    @staticmethod
    def _handle_resubmit(rid, user_id, user_name, user_role, user_agent, r):
        """重新提交"""
        if r["applicant_id"] != user_id:
            raise PermissionError("只有申请人可以重新提交")
        ReimbursementDAO.update_status(rid, Status.SUBMITTED, inc_version=True)
        # 重置审批流
        new_steps = WorkflowEngine.generate_approval_steps(rid, r["total_amount"])
        ApprovalFlowDAO.delete_by_reimbursement(rid)
        ApprovalFlowDAO.create_steps(new_steps)
        AuditLogDAO.log(user_id, user_name, "resubmit", "reimbursement", rid,
                        before_data=r["status"], after_data=Status.SUBMITTED,
                        user_role=user_role, user_agent=user_agent,
                        before_status=r["status"], after_status=Status.SUBMITTED)
        return {"id": rid, "status": Status.SUBMITTED}


# ============ 审批流引擎 v2.0 ============

class WorkflowEngine:
    """
    审批流引擎 — 配置化、可扩展。
    
    v2.0: 第一级为部门负责人审批，第二级为财务审核，第三级为总经理审批
    """

    @staticmethod
    def generate_approval_steps(reimbursement_id, total_amount):
        """根据金额从数据库匹配审批规则，生成审批步骤"""
        rule = ApprovalRuleDAO.get_matching_rule(total_amount)

        if rule:
            rule_steps = ApprovalRuleDAO.get_rule_steps(rule["id"])
            if rule_steps:
                from app.dao.models import UserDAO
                steps = []
                for rs in rule_steps:
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
                        "approver_role": rs.get("approver_role", ""),
                    })
                return steps

        return WorkflowEngine._default_steps(reimbursement_id, total_amount)

    @staticmethod
    def _default_steps(reimbursement_id, total_amount):
        """兜底默认规则"""
        from app.dao.models import UserDAO
        if total_amount <= 500:
            steps_data = [(1, "部门负责人审批", "u002", Role.DEPT_MANAGER)]
        elif total_amount <= 5000:
            steps_data = [
                (1, "部门负责人审批", "u002", Role.DEPT_MANAGER),
                (2, "财务审核", "u003", Role.FINANCE_REVIEWER),
            ]
        else:
            steps_data = [
                (1, "部门负责人审批", "u002", Role.DEPT_MANAGER),
                (2, "财务审核", "u003", Role.FINANCE_REVIEWER),
                (3, "总经理审批", "u004", Role.DEPT_MANAGER),
            ]

        steps = []
        for step_num, step_name, approver_id, approver_role in steps_data:
            approver = UserDAO.get_by_id(approver_id)
            steps.append({
                "id": new_id(),
                "reimbursement_id": reimbursement_id,
                "step": step_num,
                "step_name": step_name,
                "rule_id": "default",
                "approver_id": approver_id,
                "approver_name": approver["display_name"] if approver else approver_id,
                "approver_role": approver_role,
            })
        return steps

    @staticmethod
    def list_rules():
        """列出所有审批规则（含步骤）"""
        rules = ApprovalRuleDAO.list_all_rules()
        for r in rules:
            r["steps"] = ApprovalRuleDAO.get_rule_steps(r["id"])
        return rules
