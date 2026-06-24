#!/usr/bin/env python3
"""
DAO 层 - 数据访问对象（v1.5 重构版）

所有数据库操作统一走此模块，Service / Workflow / Controller 禁止直接写 SQL。
"""

import json
from .db import get_db, query_one, query_all, execute, execute_many, new_id, DataGuard


# ============ 用户 DAO ============

class UserDAO:
    @staticmethod
    def get_by_id(user_id):
        return query_one("SELECT * FROM users WHERE id=? AND is_active=1", (user_id,))

    @staticmethod
    def get_by_username(username):
        return query_one("SELECT * FROM users WHERE username=? AND is_active=1", (username,))

    @staticmethod
    def list_all():
        return query_all("SELECT id,username,display_name,department,role FROM users WHERE is_active=1 ORDER BY id")


# ============ 报销单 DAO ============

class ReimbursementDAO:
    @staticmethod
    def create(rid, applicant_id, applicant_name, department, total_amount, request_id=None, status="DRAFT"):
        execute(
            "INSERT INTO reimbursements (id,request_id,applicant_id,applicant_name,department,status,total_amount) VALUES (?,?,?,?,?,?,?)",
            (rid, request_id, applicant_id, applicant_name, department, status, total_amount),
        )

    @staticmethod
    def get_by_id(rid):
        return query_one("SELECT * FROM reimbursements WHERE id=?", (rid,))

    @staticmethod
    def get_by_request_id(request_id):
        return query_one("SELECT * FROM reimbursements WHERE request_id=?", (request_id,))

    @staticmethod
    def list_by_status(status="ALL", applicant_id=None):
        sql = "SELECT * FROM reimbursements WHERE 1=1"
        params = []
        if status != "ALL":
            sql += " AND status=?"
            params.append(status)
        if applicant_id:
            sql += " AND applicant_id=?"
            params.append(applicant_id)
        sql += " ORDER BY updated_at DESC"
        return query_all(sql, params)

    @staticmethod
    def update_status(rid, status, inc_version=True):
        if inc_version:
            execute("UPDATE reimbursements SET status=?, updated_at=datetime('now','localtime'), version=version+1 WHERE id=?",
                    (status, rid))
        else:
            execute("UPDATE reimbursements SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (status, rid))

    @staticmethod
    def update_total(rid, total_amount):
        execute("UPDATE reimbursements SET total_amount=?, updated_at=datetime('now','localtime') WHERE id=?",
                (total_amount, rid))

    @staticmethod
    def delete(rid):
        execute("DELETE FROM expense_items WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM approval_flow WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM reimbursements WHERE id=?", (rid,))


# ============ 费用明细 DAO ============

class ExpenseItemDAO:
    @staticmethod
    def delete_by_reimbursement(rid):
        execute("DELETE FROM expense_items WHERE reimbursement_id=?", (rid,))

    @staticmethod
    def insert_items(items):
        execute_many(
            "INSERT INTO expense_items (id,reimbursement_id,date,category,amount,description,receipt_no,project_code,city,employee_level,source,ocr_raw_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(i["id"], i["reimbursement_id"], i["date"], i["category"], i["amount"],
              i.get("description", ""), i.get("receipt_no", ""), i.get("project_code", ""),
              i.get("city", ""), i.get("employee_level", "staff"), i.get("source", "manual"),
              i.get("ocr_raw_text", "")) for i in items],
        )

    @staticmethod
    def list_by_reimbursement(rid):
        return query_all("SELECT * FROM expense_items WHERE reimbursement_id=? ORDER BY date, id", (rid,))


# ============ 审批流 DAO ============

class ApprovalFlowDAO:
    @staticmethod
    def create_steps(steps):
        execute_many(
            "INSERT INTO approval_flow (id,reimbursement_id,step,step_name,rule_id,approver_id,approver_name,status) VALUES (?,?,?,?,?,?,?,?)",
            [(s["id"], s["reimbursement_id"], s["step"], s["step_name"],
              s.get("rule_id", ""), s.get("approver_id", ""), s.get("approver_name", ""), s.get("status", "PENDING"))
             for s in steps],
        )

    @staticmethod
    def list_by_reimbursement(rid):
        return query_all("SELECT * FROM approval_flow WHERE reimbursement_id=? ORDER BY step", (rid,))

    @staticmethod
    def get_pending_step(rid, step):
        return query_one("SELECT * FROM approval_flow WHERE reimbursement_id=? AND step=?", (rid, step))

    @staticmethod
    def act_step(flow_id, status, comment, approver_id, approver_name):
        execute("UPDATE approval_flow SET status=?, comment=?, approver_id=?, approver_name=?, acted_at=datetime('now','localtime') WHERE id=?",
                (status, comment, approver_id, approver_name, flow_id))

    @staticmethod
    def delete_by_reimbursement(rid):
        execute("DELETE FROM approval_flow WHERE reimbursement_id=?", (rid,))


# ============ 审批规则 DAO ============

class ApprovalRuleDAO:
    @staticmethod
    def get_matching_rule(amount):
        """根据金额匹配审批规则（优先级最高优先）"""
        return query_one(
            "SELECT * FROM approval_rules WHERE ? BETWEEN min_amount AND max_amount AND is_active=1 ORDER BY priority DESC LIMIT 1",
            (amount,),
        )

    @staticmethod
    def get_rule_steps(rule_id):
        """获取规则下的审批步骤"""
        return query_all("SELECT * FROM approval_rule_steps WHERE rule_id=? ORDER BY step", (rule_id,))

    @staticmethod
    def list_all_rules():
        return query_all("SELECT r.*, (SELECT COUNT(*) FROM approval_rule_steps s WHERE s.rule_id=r.id) as step_count FROM approval_rules r ORDER BY r.priority DESC")


# ============ 审计日志 DAO ============

class AuditLogDAO:
    @staticmethod
    def log(user_id, user_name, action, target_type, target_id, before_data="", after_data=""):
        lid = new_id()
        before = _to_json(before_data)
        after  = _to_json(after_data)
        execute("INSERT INTO audit_log (id,user_id,user_name,action,target_type,target_id,before_data,after_data) VALUES (?,?,?,?,?,?,?,?)",
                (lid, user_id, user_name, action, target_type, target_id, before, after))

    @staticmethod
    def list_by_target(target_id, limit=50):
        return query_all("SELECT * FROM audit_log WHERE target_id=? ORDER BY timestamp DESC LIMIT ?", (target_id, limit))

    @staticmethod
    def list_all(limit=100):
        return query_all("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))


# ============ 异步任务 DAO ============

class AsyncTaskDAO:
    @staticmethod
    def create(task_id, task_type, input_data=""):
        execute("INSERT INTO async_tasks (id,task_type,status,input_data) VALUES (?,?,?,?)",
                (task_id, task_type, "PENDING", input_data))

    @staticmethod
    def get(task_id):
        return query_one("SELECT * FROM async_tasks WHERE id=?", (task_id,))

    @staticmethod
    def update_result(task_id, status, output_data="", error=""):
        execute("UPDATE async_tasks SET status=?, output_data=?, error=?, completed_at=datetime('now','localtime') WHERE id=?",
                (status, output_data, error, task_id))


# ============ 辅助 ============

def _to_json(data):
    if isinstance(data, (dict, list)):
        return json.dumps(data, ensure_ascii=False, default=str)
    return str(data) if data else ""
