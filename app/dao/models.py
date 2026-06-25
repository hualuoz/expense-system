#!/usr/bin/env python3
"""
DAO 层 - 数据访问对象（v2.0 升级版）

所有数据库操作统一走此模块，Service / Workflow / Controller 禁止直接写 SQL。
"""

import json
from .db import get_db, query_one, query_all, execute, execute_many, new_id, generate_reimb_number


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

    @staticmethod
    def list_by_department(department):
        return query_all("SELECT id,username,display_name,department,role FROM users WHERE department=? AND is_active=1", (department,))

    @staticmethod
    def list_by_role(role):
        return query_all("SELECT id,username,display_name,department,role FROM users WHERE role=? AND is_active=1", (role,))

    @staticmethod
    def update_role(user_id, role):
        execute("UPDATE users SET role=? WHERE id=?", (role, user_id))


# ============ 报销单 DAO ============

class ReimbursementDAO:
    @staticmethod
    def create(rid, applicant_id, applicant_name, department, total_amount,
               request_id=None, reimb_number=None, expense_type="OTHER",
               reason="", account_name="", account_number="", bank_name="",
               invoice_number="", invoice_amount=0, invoice_date=""):
        execute(
            "INSERT INTO reimbursements "
            "(id,request_id,reimb_number,applicant_id,applicant_name,department,"
            "status,total_amount,expense_type,reason,account_name,account_number,"
            "bank_name,invoice_number,invoice_amount,invoice_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, request_id, reimb_number, applicant_id, applicant_name, department,
             "DRAFT", total_amount, expense_type, reason, account_name, account_number,
             bank_name, invoice_number, invoice_amount, invoice_date),
        )

    @staticmethod
    def get_by_id(rid):
        return query_one("SELECT * FROM reimbursements WHERE id=?", (rid,))

    @staticmethod
    def get_by_request_id(request_id):
        return query_one("SELECT * FROM reimbursements WHERE request_id=?", (request_id,))

    @staticmethod
    def get_by_reimb_number(reimb_number):
        return query_one("SELECT * FROM reimbursements WHERE reimb_number=?", (reimb_number,))

    @staticmethod
    def list_by_status(status="ALL", applicant_id=None, department=None,
                       expense_type=None, risk_level=None):
        sql = "SELECT * FROM reimbursements WHERE 1=1"
        params = []
        if status != "ALL":
            sql += " AND status=?"
            params.append(status)
        if applicant_id:
            sql += " AND applicant_id=?"
            params.append(applicant_id)
        if department:
            sql += " AND department=?"
            params.append(department)
        if expense_type:
            sql += " AND expense_type=?"
            params.append(expense_type)
        if risk_level:
            sql += " AND risk_level=?"
            params.append(risk_level)
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
    def update_fields(rid, **kwargs):
        """更新报销单指定字段"""
        if not kwargs:
            return
        sets = []
        params = []
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            params.append(v)
        sets.append("updated_at=datetime('now','localtime')")
        params.append(rid)
        execute(f"UPDATE reimbursements SET {', '.join(sets)} WHERE id=?", params)

    @staticmethod
    def update_total(rid, total_amount):
        execute("UPDATE reimbursements SET total_amount=?, updated_at=datetime('now','localtime') WHERE id=?",
                (total_amount, rid))

    @staticmethod
    def update_risk_level(rid, risk_level):
        execute("UPDATE reimbursements SET risk_level=?, updated_at=datetime('now','localtime') WHERE id=?",
                (risk_level, rid))

    @staticmethod
    def delete(rid):
        execute("DELETE FROM attachments WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM expense_items WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM approval_flow WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM voucher_reviews WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM payment_records WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM duplicate_checks WHERE reimbursement_id=?", (rid,))
        execute("DELETE FROM reimbursements WHERE id=?", (rid,))

    @staticmethod
    def check_invoice_duplicate(invoice_number, exclude_rid=None):
        """检查发票号码是否重复"""
        if not invoice_number:
            return []
        sql = "SELECT id, reimb_number, applicant_name, total_amount, status FROM reimbursements WHERE invoice_number=?"
        params = [invoice_number]
        if exclude_rid:
            sql += " AND id != ?"
            params.append(exclude_rid)
        return query_all(sql, params)

    @staticmethod
    def check_amount_date_duplicate(applicant_id, amount, expense_type, date, exclude_rid=None):
        """检查同一人同一天同金额同类型重复"""
        sql = ("SELECT id, reimb_number, applicant_name, total_amount, status FROM reimbursements "
               "WHERE applicant_id=? AND total_amount=? AND expense_type=? AND id != ?")
        params = [applicant_id, amount, expense_type, exclude_rid or ""]
        return query_all(sql, params)


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


# ============ 附件 DAO ============

class AttachmentDAO:
    @staticmethod
    def create(att_id, reimbursement_id, file_name, file_type="", file_size=0,
               file_hash="", attachment_type="other", uploaded_by=""):
        execute(
            "INSERT INTO attachments (id,reimbursement_id,file_name,file_type,file_size,file_hash,attachment_type,uploaded_by) VALUES (?,?,?,?,?,?,?,?)",
            (att_id, reimbursement_id, file_name, file_type, file_size, file_hash, attachment_type, uploaded_by),
        )

    @staticmethod
    def list_by_reimbursement(rid):
        return query_all("SELECT * FROM attachments WHERE reimbursement_id=? ORDER BY uploaded_at", (rid,))

    @staticmethod
    def get_by_id(att_id):
        return query_one("SELECT * FROM attachments WHERE id=?", (att_id,))

    @staticmethod
    def delete(att_id):
        execute("DELETE FROM attachments WHERE id=?", (att_id,))

    @staticmethod
    def check_hash_duplicate(file_hash, exclude_rid=None):
        """检查附件hash是否重复"""
        if not file_hash:
            return []
        sql = "SELECT a.*, r.reimb_number FROM attachments a JOIN reimbursements r ON a.reimbursement_id = r.id WHERE a.file_hash=?"
        params = [file_hash]
        if exclude_rid:
            sql += " AND a.reimbursement_id != ?"
            params.append(exclude_rid)
        return query_all(sql, params)

    @staticmethod
    def count_by_reimbursement(rid):
        row = query_one("SELECT COUNT(*) as cnt FROM attachments WHERE reimbursement_id=?", (rid,))
        return row["cnt"] if row else 0


# ============ 审批流 DAO ============

class ApprovalFlowDAO:
    @staticmethod
    def create_steps(steps):
        execute_many(
            "INSERT INTO approval_flow (id,reimbursement_id,step,step_name,rule_id,approver_id,approver_name,approver_role,status) VALUES (?,?,?,?,?,?,?,?,?)",
            [(s["id"], s["reimbursement_id"], s["step"], s["step_name"],
              s.get("rule_id", ""), s.get("approver_id", ""), s.get("approver_name", ""),
              s.get("approver_role", ""), s.get("status", "PENDING"))
             for s in steps],
        )

    @staticmethod
    def list_by_reimbursement(rid):
        return query_all("SELECT * FROM approval_flow WHERE reimbursement_id=? ORDER BY step", (rid,))

    @staticmethod
    def get_pending_step(rid, step):
        return query_one("SELECT * FROM approval_flow WHERE reimbursement_id=? AND step=?", (rid, step))

    @staticmethod
    def get_first_pending(rid):
        return query_one("SELECT * FROM approval_flow WHERE reimbursement_id=? AND status='PENDING' ORDER BY step LIMIT 1", (rid,))

    @staticmethod
    def act_step(flow_id, status, comment, approver_id, approver_name, user_agent=""):
        execute("UPDATE approval_flow SET status=?, comment=?, approver_id=?, approver_name=?, user_agent=?, acted_at=datetime('now','localtime') WHERE id=?",
                (status, comment, approver_id, approver_name, user_agent, flow_id))

    @staticmethod
    def delete_by_reimbursement(rid):
        execute("DELETE FROM approval_flow WHERE reimbursement_id=?", (rid,))


# ============ 审批规则 DAO ============

class ApprovalRuleDAO:
    @staticmethod
    def get_matching_rule(amount):
        return query_one(
            "SELECT * FROM approval_rules WHERE ? BETWEEN min_amount AND max_amount AND is_active=1 ORDER BY priority DESC LIMIT 1",
            (amount,),
        )

    @staticmethod
    def get_rule_steps(rule_id):
        return query_all("SELECT * FROM approval_rule_steps WHERE rule_id=? ORDER BY step", (rule_id,))

    @staticmethod
    def list_all_rules():
        return query_all("SELECT r.*, (SELECT COUNT(*) FROM approval_rule_steps s WHERE s.rule_id=r.id) as step_count FROM approval_rules r ORDER BY r.priority DESC")


# ============ 凭证审核 DAO ============

class VoucherReviewDAO:
    @staticmethod
    def create(review_id, reimbursement_id, reviewer_id, reviewer_name,
               invoice_complete=0, amount_match=0, attachment_complete=0,
               no_duplicate=0, category_reasonable=0, policy_compliant=0,
               review_result="pending", review_comment=""):
        execute(
            "INSERT INTO voucher_reviews "
            "(id,reimbursement_id,reviewer_id,reviewer_name,invoice_complete,amount_match,"
            "attachment_complete,no_duplicate,category_reasonable,policy_compliant,"
            "review_result,review_comment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (review_id, reimbursement_id, reviewer_id, reviewer_name,
             invoice_complete, amount_match, attachment_complete, no_duplicate,
             category_reasonable, policy_compliant, review_result, review_comment),
        )

    @staticmethod
    def get_by_reimbursement(rid):
        return query_one("SELECT * FROM voucher_reviews WHERE reimbursement_id=? ORDER BY reviewed_at DESC LIMIT 1", (rid,))

    @staticmethod
    def list_by_reimbursement(rid):
        return query_all("SELECT * FROM voucher_reviews WHERE reimbursement_id=? ORDER BY reviewed_at DESC", (rid,))


# ============ 付款记录 DAO ============

class PaymentRecordDAO:
    @staticmethod
    def create(payment_id, reimbursement_id, paid_amount, payment_date,
               payment_account, receive_account, bank_serial,
               payment_voucher="", payment_note="", difference_note="",
               paid_by="", paid_by_name=""):
        execute(
            "INSERT INTO payment_records "
            "(id,reimbursement_id,paid_amount,payment_date,payment_account,"
            "receive_account,bank_serial,payment_voucher,payment_note,difference_note,"
            "paid_by,paid_by_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (payment_id, reimbursement_id, paid_amount, payment_date,
             payment_account, receive_account, bank_serial, payment_voucher,
             payment_note, difference_note, paid_by, paid_by_name),
        )

    @staticmethod
    def get_by_reimbursement(rid):
        return query_one("SELECT * FROM payment_records WHERE reimbursement_id=?", (rid,))

    @staticmethod
    def update_voucher(rid, payment_voucher):
        execute("UPDATE payment_records SET payment_voucher=? WHERE reimbursement_id=?", (payment_voucher, rid))


# ============ 归档记录 DAO ============

class ArchiveDAO:
    @staticmethod
    def create(archive_id, reimbursement_id, archive_data, archived_by, archived_by_name):
        execute(
            "INSERT INTO archives (id,reimbursement_id,archive_data,archived_by,archived_by_name) VALUES (?,?,?,?,?)",
            (archive_id, reimbursement_id, archive_data, archived_by, archived_by_name),
        )

    @staticmethod
    def get_by_reimbursement(rid):
        return query_one("SELECT * FROM archives WHERE reimbursement_id=?", (rid,))


# ============ 重复检测 DAO ============

class DuplicateCheckDAO:
    @staticmethod
    def create(check_id, reimbursement_id, check_type, matched_reimbursement_id="",
               risk_level="medium", detail=""):
        execute(
            "INSERT INTO duplicate_checks (id,reimbursement_id,check_type,matched_reimbursement_id,risk_level,detail) VALUES (?,?,?,?,?,?)",
            (check_id, reimbursement_id, check_type, matched_reimbursement_id, risk_level, detail),
        )

    @staticmethod
    def list_by_reimbursement(rid):
        return query_all("SELECT * FROM duplicate_checks WHERE reimbursement_id=? ORDER BY checked_at DESC", (rid,))

    @staticmethod
    def delete_by_reimbursement(rid):
        execute("DELETE FROM duplicate_checks WHERE reimbursement_id=?", (rid,))


# ============ 审计日志 DAO ============

class AuditLogDAO:
    @staticmethod
    def log(user_id, user_name, action, target_type, target_id,
            before_data="", after_data="", user_role="", user_agent="",
            before_status="", after_status=""):
        lid = new_id()
        before = _to_json(before_data)
        after  = _to_json(after_data)
        execute(
            "INSERT INTO audit_log (id,user_id,user_name,user_role,action,target_type,target_id,before_status,after_status,before_data,after_data,user_agent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (lid, user_id, user_name, user_role, action, target_type, target_id,
             before_status, after_status, before, after, user_agent),
        )

    @staticmethod
    def list_by_target(target_id, limit=50):
        return query_all("SELECT * FROM audit_log WHERE target_id=? ORDER BY timestamp DESC LIMIT ?", (target_id, limit))

    @staticmethod
    def list_all(limit=100):
        return query_all("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))

    @staticmethod
    def search(user_id=None, target_id=None, action=None, start_date=None, end_date=None, limit=200):
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if user_id:
            sql += " AND user_id=?"
            params.append(user_id)
        if target_id:
            sql += " AND target_id=?"
            params.append(target_id)
        if action:
            sql += " AND action=?"
            params.append(action)
        if start_date:
            sql += " AND timestamp >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND timestamp <= ?"
            params.append(end_date + " 23:59:59")
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return query_all(sql, params)


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
