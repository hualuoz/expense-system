#!/usr/bin/env python3
"""
DAO 层 - 数据库初始化与访问（v2.0 升级版）

升级要点:
  - 新状态: DEPT_APPROVING, FINANCE_REVIEWING, PAYMENT_PENDING, PAID, ARCHIVED
  - 新角色: dept_manager, finance_reviewer, cashier, auditor
  - reimbursements 增加大量字段: 费用类型/事由/收款账户/发票/单号等
  - 新增附件表 attachments
  - 新增凭证审核表 voucher_review
  - 新增付款记录表 payment_records
  - 新增归档记录表 archives
  - 新增重复检测表 duplicate_checks
  - 审计日志增强: user_agent, user_role, before_status, after_status
"""

import sqlite3
import os
import uuid
import json
from datetime import datetime

# DB 路径
_LOCAL_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data.db")
if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
    DB_PATH = os.path.join("/tmp", "expense_data.db")
else:
    DB_PATH = _LOCAL_DB


# ============ 状态常量 v2.0 ============

class Status:
    DRAFT              = "DRAFT"
    SUBMITTED          = "SUBMITTED"
    DEPT_APPROVING     = "DEPT_APPROVING"
    FINANCE_REVIEWING  = "FINANCE_REVIEWING"
    APPROVED           = "APPROVED"
    REJECTED           = "REJECTED"
    PAYMENT_PENDING    = "PAYMENT_PENDING"
    PAID               = "PAID"
    ARCHIVED           = "ARCHIVED"

    ALL = [DRAFT, SUBMITTED, DEPT_APPROVING, FINANCE_REVIEWING,
           APPROVED, REJECTED, PAYMENT_PENDING, PAID, ARCHIVED]

    DISPLAY = {
        DRAFT: "草稿", SUBMITTED: "已提交", DEPT_APPROVING: "部门审批中",
        FINANCE_REVIEWING: "财务审核中", APPROVED: "已通过", REJECTED: "已驳回",
        PAYMENT_PENDING: "待付款", PAID: "已打款", ARCHIVED: "已归档",
    }

    # 不可变状态
    FINAL_STATES = {PAID, ARCHIVED}

    # 可编辑状态
    EDITABLE_STATES = {DRAFT, SUBMITTED, REJECTED}


class Role:
    APPLICANT       = "applicant"
    DEPT_MANAGER    = "dept_manager"
    FINANCE_REVIEWER = "finance_reviewer"
    CASHIER         = "cashier"
    AUDITOR         = "auditor"
    ADMIN           = "admin"

    ALL = [APPLICANT, DEPT_MANAGER, FINANCE_REVIEWER, CASHIER, AUDITOR, ADMIN]

    DISPLAY = {
        APPLICANT: "申请人", DEPT_MANAGER: "部门负责人",
        FINANCE_REVIEWER: "财务审核", CASHIER: "出纳",
        AUDITOR: "审计员", ADMIN: "系统管理员",
    }


# 费用类型
EXPENSE_TYPES = {
    "TRAVEL": "差旅费", "OFFICE": "办公费", "ENTERTAIN": "招待费",
    "TRANSPORT": "交通费", "LODGING": "住宿费", "PURCHASE": "采购费",
    "TRAINING": "培训费", "OTHER": "其他",
}


# ============ 数据库连接 ============

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def new_id():
    return uuid.uuid4().hex[:10]


def generate_reimb_number():
    """生成报销单号 BX-YYYYMMDD-XXXX"""
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"BX-{today}-"
    conn = get_db()
    row = conn.execute(
        "SELECT reimb_number FROM reimbursements WHERE reimb_number LIKE ? ORDER BY reimb_number DESC LIMIT 1",
        (prefix + "%",)
    ).fetchone()
    conn.close()
    if row:
        seq = int(row["reimb_number"].split("-")[-1]) + 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


# ============ Schema 初始化 v2.0 ============

def init_db():
    """v2.0 升级版数据库 Schema"""
    conn = get_db()
    c = conn.cursor()

    # ----- 用户表 -----
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        username      TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name  TEXT NOT NULL,
        department    TEXT DEFAULT '',
        role          TEXT NOT NULL DEFAULT 'applicant',
        is_active     INTEGER DEFAULT 1,
        created_at    TEXT DEFAULT (datetime('now','localtime'))
    )""")

    # ----- 报销单表（v2.0 大幅扩展字段） -----
    c.execute("""CREATE TABLE IF NOT EXISTS reimbursements (
        id              TEXT PRIMARY KEY,
        request_id      TEXT UNIQUE,
        reimb_number    TEXT UNIQUE,
        applicant_id    TEXT NOT NULL,
        applicant_name  TEXT NOT NULL,
        department      TEXT DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'DRAFT',
        total_amount    REAL DEFAULT 0,
        expense_type    TEXT DEFAULT 'OTHER',
        reason          TEXT DEFAULT '',
        account_name    TEXT DEFAULT '',
        account_number  TEXT DEFAULT '',
        bank_name       TEXT DEFAULT '',
        invoice_number  TEXT DEFAULT '',
        invoice_amount  REAL DEFAULT 0,
        invoice_date    TEXT DEFAULT '',
        risk_level      TEXT DEFAULT 'none',
        version         INTEGER DEFAULT 1,
        created_at      TEXT DEFAULT (datetime('now','localtime')),
        updated_at      TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (applicant_id) REFERENCES users(id)
    )""")

    # ----- 费用明细表 -----
    c.execute("""CREATE TABLE IF NOT EXISTS expense_items (
        id                TEXT PRIMARY KEY,
        reimbursement_id  TEXT NOT NULL,
        date              TEXT NOT NULL,
        category          TEXT NOT NULL,
        amount            REAL NOT NULL CHECK(amount > 0),
        description       TEXT NOT NULL DEFAULT '',
        receipt_no        TEXT DEFAULT '',
        project_code      TEXT DEFAULT '',
        city              TEXT DEFAULT '',
        employee_level    TEXT DEFAULT 'staff',
        source            TEXT DEFAULT 'manual',
        ocr_raw_text      TEXT DEFAULT '',
        created_at        TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (reimbursement_id) REFERENCES reimbursements(id) ON DELETE CASCADE
    )""")

    # ----- 附件表（v2.0 新增） -----
    c.execute("""CREATE TABLE IF NOT EXISTS attachments (
        id                TEXT PRIMARY KEY,
        reimbursement_id  TEXT NOT NULL,
        file_name         TEXT NOT NULL,
        file_type         TEXT DEFAULT '',
        file_size         INTEGER DEFAULT 0,
        file_hash         TEXT DEFAULT '',
        attachment_type   TEXT DEFAULT 'other',
        uploaded_by       TEXT DEFAULT '',
        uploaded_at       TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (reimbursement_id) REFERENCES reimbursements(id) ON DELETE CASCADE
    )""")

    # ----- 审批流表 -----
    c.execute("""CREATE TABLE IF NOT EXISTS approval_flow (
        id                TEXT PRIMARY KEY,
        reimbursement_id  TEXT NOT NULL,
        step              INTEGER NOT NULL,
        step_name         TEXT NOT NULL,
        rule_id           TEXT DEFAULT '',
        approver_id       TEXT DEFAULT '',
        approver_name     TEXT DEFAULT '',
        approver_role     TEXT DEFAULT '',
        status            TEXT DEFAULT 'PENDING',
        comment           TEXT DEFAULT '',
        user_agent        TEXT DEFAULT '',
        acted_at          TEXT DEFAULT '',
        created_at        TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (reimbursement_id) REFERENCES reimbursements(id) ON DELETE CASCADE
    )""")

    # ----- 审批规则表 -----
    c.execute("""CREATE TABLE IF NOT EXISTS approval_rules (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        min_amount  REAL DEFAULT 0,
        max_amount  REAL DEFAULT 999999999,
        priority    INTEGER DEFAULT 0,
        is_active   INTEGER DEFAULT 1,
        created_at  TEXT DEFAULT (datetime('now','localtime'))
    )""")

    # ----- 审批规则步骤表 -----
    c.execute("""CREATE TABLE IF NOT EXISTS approval_rule_steps (
        id          TEXT PRIMARY KEY,
        rule_id     TEXT NOT NULL,
        step        INTEGER NOT NULL,
        step_name   TEXT NOT NULL,
        approver_id TEXT NOT NULL,
        approver_role TEXT DEFAULT '',
        FOREIGN KEY (rule_id) REFERENCES approval_rules(id) ON DELETE CASCADE
    )""")

    # ----- 凭证审核表（v2.0 新增） -----
    c.execute("""CREATE TABLE IF NOT EXISTS voucher_reviews (
        id                     TEXT PRIMARY KEY,
        reimbursement_id       TEXT NOT NULL,
        reviewer_id            TEXT NOT NULL,
        reviewer_name          TEXT DEFAULT '',
        invoice_complete       INTEGER DEFAULT 0,
        amount_match           INTEGER DEFAULT 0,
        attachment_complete    INTEGER DEFAULT 0,
        no_duplicate           INTEGER DEFAULT 0,
        category_reasonable    INTEGER DEFAULT 0,
        policy_compliant       INTEGER DEFAULT 0,
        review_result          TEXT DEFAULT 'pending',
        review_comment         TEXT DEFAULT '',
        reviewed_at            TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (reimbursement_id) REFERENCES reimbursements(id) ON DELETE CASCADE
    )""")

    # ----- 付款记录表（v2.0 新增） -----
    c.execute("""CREATE TABLE IF NOT EXISTS payment_records (
        id                TEXT PRIMARY KEY,
        reimbursement_id  TEXT NOT NULL,
        paid_amount       REAL DEFAULT 0,
        payment_date      TEXT DEFAULT '',
        payment_account   TEXT DEFAULT '',
        receive_account   TEXT DEFAULT '',
        bank_serial       TEXT DEFAULT '',
        payment_voucher   TEXT DEFAULT '',
        payment_note      TEXT DEFAULT '',
        difference_note   TEXT DEFAULT '',
        paid_by           TEXT DEFAULT '',
        paid_by_name      TEXT DEFAULT '',
        paid_at           TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (reimbursement_id) REFERENCES reimbursements(id) ON DELETE CASCADE
    )""")

    # ----- 归档记录表（v2.0 新增） -----
    c.execute("""CREATE TABLE IF NOT EXISTS archives (
        id                TEXT PRIMARY KEY,
        reimbursement_id  TEXT NOT NULL UNIQUE,
        archive_data      TEXT DEFAULT '',
        archived_by       TEXT DEFAULT '',
        archived_by_name  TEXT DEFAULT '',
        archived_at       TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (reimbursement_id) REFERENCES reimbursements(id) ON DELETE CASCADE
    )""")

    # ----- 重复检测表（v2.0 新增） -----
    c.execute("""CREATE TABLE IF NOT EXISTS duplicate_checks (
        id                     TEXT PRIMARY KEY,
        reimbursement_id       TEXT NOT NULL,
        check_type             TEXT NOT NULL,
        matched_reimbursement_id TEXT DEFAULT '',
        risk_level             TEXT DEFAULT 'medium',
        detail                 TEXT DEFAULT '',
        checked_at             TEXT DEFAULT (datetime('now','localtime'))
    )""")

    # ----- 审计日志表（v2.0 增强） -----
    c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id            TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL,
        user_name     TEXT DEFAULT '',
        user_role     TEXT DEFAULT '',
        action        TEXT NOT NULL,
        target_type   TEXT NOT NULL,
        target_id     TEXT NOT NULL,
        before_status TEXT DEFAULT '',
        after_status  TEXT DEFAULT '',
        before_data   TEXT DEFAULT '',
        after_data    TEXT DEFAULT '',
        user_agent    TEXT DEFAULT '',
        timestamp     TEXT DEFAULT (datetime('now','localtime'))
    )""")

    # ----- 异步任务表 -----
    c.execute("""CREATE TABLE IF NOT EXISTS async_tasks (
        id           TEXT PRIMARY KEY,
        task_type    TEXT NOT NULL,
        status       TEXT DEFAULT 'PENDING',
        input_data   TEXT DEFAULT '',
        output_data  TEXT DEFAULT '',
        error        TEXT DEFAULT '',
        created_at   TEXT DEFAULT (datetime('now','localtime')),
        completed_at TEXT DEFAULT ''
    )""")

    # ========== 兼容迁移：为旧表添加新字段 ==========
    _migrate_reimbursements(c)

    # ========== 初始化种子数据 v2.0 ==========
    c.execute("SELECT COUNT(*) as cnt FROM users")
    if c.fetchone()["cnt"] == 0:
        users = [
            ("u001", "zhangsan",   "hash_zhangsan",  "张三",     "技术研发部", Role.APPLICANT),
            ("u002", "lisi",       "hash_lisi",      "李主管",   "技术研发部", Role.DEPT_MANAGER),
            ("u003", "wangwu",     "hash_wangwu",    "王财务",   "财务部",     Role.FINANCE_REVIEWER),
            ("u004", "zhaoliu",    "hash_zhaoliu",   "赵总经理", "管理层",     Role.DEPT_MANAGER),
            ("u005", "cashier1",   "hash_cashier1",  "陈出纳",   "财务部",     Role.CASHIER),
            ("u006", "auditor1",   "hash_auditor1",  "孙审计",   "审计部",     Role.AUDITOR),
            ("u007", "admin",      "hash_admin",     "系统管理员","信息部",     Role.ADMIN),
        ]
        for u in users:
            c.execute("INSERT OR IGNORE INTO users (id,username,password_hash,display_name,department,role) VALUES (?,?,?,?,?,?)", u)

    # 初始化审批规则（v2.0: 3级审批 → 部门负责人 + 财务审核 + 总经理）
    c.execute("SELECT COUNT(*) as cnt FROM approval_rules")
    if c.fetchone()["cnt"] == 0:
        rules = [
            ("rule_1", "小额报销(≤500)",      0,      500,   1),
            ("rule_2", "中额报销(500~5000)",  500.01, 5000,  2),
            ("rule_3", "大额报销(>5000)",      5000.01, 999999999, 3),
        ]
        for r in rules:
            c.execute("INSERT INTO approval_rules (id,name,min_amount,max_amount,priority) VALUES (?,?,?,?,?)", r)

        rule_steps = [
            # rule_1: 部门负责人
            ("rs_1_1", "rule_1", 1, "部门负责人审批", "u002", Role.DEPT_MANAGER),
            # rule_2: 部门负责人 + 财务审核
            ("rs_2_1", "rule_2", 1, "部门负责人审批", "u002", Role.DEPT_MANAGER),
            ("rs_2_2", "rule_2", 2, "财务审核",       "u003", Role.FINANCE_REVIEWER),
            # rule_3: 部门负责人 + 财务审核 + 总经理
            ("rs_3_1", "rule_3", 1, "部门负责人审批", "u002", Role.DEPT_MANAGER),
            ("rs_3_2", "rule_3", 2, "财务审核",       "u003", Role.FINANCE_REVIEWER),
            ("rs_3_3", "rule_3", 3, "总经理审批",     "u004", Role.DEPT_MANAGER),
        ]
        for s in rule_steps:
            c.execute("INSERT INTO approval_rule_steps (id,rule_id,step,step_name,approver_id,approver_role) VALUES (?,?,?,?,?,?)", s)

    conn.commit()
    conn.close()


def _migrate_reimbursements(c):
    """为旧版 reimbursements 表添加新字段（兼容升级）"""
    # 获取现有列
    c.execute("PRAGMA table_info(reimbursements)")
    existing_cols = {row[1] for row in c.fetchall()}

    migrations = [
        ("reimb_number",   "TEXT DEFAULT ''"),
        ("expense_type",   "TEXT DEFAULT 'OTHER'"),
        ("reason",         "TEXT DEFAULT ''"),
        ("account_name",   "TEXT DEFAULT ''"),
        ("account_number", "TEXT DEFAULT ''"),
        ("bank_name",      "TEXT DEFAULT ''"),
        ("invoice_number", "TEXT DEFAULT ''"),
        ("invoice_amount", "REAL DEFAULT 0"),
        ("invoice_date",   "TEXT DEFAULT ''"),
        ("risk_level",     "TEXT DEFAULT 'none'"),
    ]
    for col_name, col_def in migrations:
        if col_name not in existing_cols:
            try:
                c.execute(f"ALTER TABLE reimbursements ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass  # 列已存在


# ============ 通用查询助手 ============

def query_one(sql, params=()):
    conn = get_db()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None

def query_all(sql, params=()):
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def execute(sql, params=()):
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected

def execute_many(sql, params_list):
    conn = get_db()
    conn.executemany(sql, params_list)
    conn.commit()
    conn.close()


# ============ Data Guard v2.0 ============

class DataGuard:
    """v2.0 数据一致性守卫"""

    @staticmethod
    def check_consistency():
        """执行全量一致性检查，返回问题列表"""
        issues = []
        conn = get_db()

        # 1. 孤儿费用明细
        rows = conn.execute(
            "SELECT ei.id, ei.reimbursement_id FROM expense_items ei "
            "LEFT JOIN reimbursements r ON ei.reimbursement_id = r.id "
            "WHERE r.id IS NULL"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "orphan_expense_item",
                           "id": r["id"], "detail": f"费用明细 {r['id']} 引用了不存在的报销单 {r['reimbursement_id']}"})

        # 2. 孤儿审批流
        rows = conn.execute(
            "SELECT af.id, af.reimbursement_id FROM approval_flow af "
            "LEFT JOIN reimbursements r ON af.reimbursement_id = r.id "
            "WHERE r.id IS NULL"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "orphan_approval_flow",
                           "id": r["id"], "detail": f"审批流 {r['id']} 引用了不存在的报销单 {r['reimbursement_id']}"})

        # 3. 已通过但无审批记录
        rows = conn.execute(
            "SELECT r.id FROM reimbursements r "
            "WHERE r.status IN ('APPROVED','PAID','ARCHIVED') "
            "AND r.id NOT IN (SELECT DISTINCT reimbursement_id FROM approval_flow WHERE status='APPROVED')"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "approved_no_flow",
                           "id": r["id"], "detail": f"报销单 {r['id']} 已通过但无审批通过记录"})

        # 4. 金额非法
        rows = conn.execute(
            "SELECT id, reimbursement_id, amount FROM expense_items WHERE amount <= 0 OR amount IS NULL"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "invalid_amount",
                           "id": r["id"], "detail": f"费用明细 {r['id']} 金额异常: {r['amount']}"})

        # 5. 总金额与明细不匹配
        rows = conn.execute(
            "SELECT r.id, r.total_amount, COALESCE(SUM(ei.amount),0) as item_sum "
            "FROM reimbursements r LEFT JOIN expense_items ei ON r.id = ei.reimbursement_id "
            "GROUP BY r.id HAVING ABS(r.total_amount - item_sum) > 0.01"
        ).fetchall()
        for r in rows:
            issues.append({"level": "WARNING", "type": "amount_mismatch",
                           "id": r["id"], "detail": f"报销单 {r['id']}: 表头={r['total_amount']:.2f}, 明细合计={r['item_sum']:.2f}"})

        # 6. 已通过但审批步骤未全部通过
        rows = conn.execute(
            "SELECT r.id, COUNT(af.id) as pending FROM reimbursements r "
            "JOIN approval_flow af ON r.id = af.reimbursement_id "
            "WHERE r.status = 'APPROVED' AND af.status != 'APPROVED' "
            "GROUP BY r.id"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "inconsistent_approval",
                           "id": r["id"], "detail": f"报销单 {r['id']} 已通过但有 {r['pending']} 个未通过步骤"})

        # 7. 非法状态值
        valid_statuses = Status.ALL
        rows = conn.execute("SELECT id, status FROM reimbursements").fetchall()
        for r in rows:
            if r["status"] not in valid_statuses:
                issues.append({"level": "CRITICAL", "type": "invalid_status",
                               "id": r["id"], "detail": f"报销单 {r['id']} 非法状态: {r['status']}"})

        # 8. 已审批但审批人为空
        rows = conn.execute(
            "SELECT id, step, step_name FROM approval_flow "
            "WHERE status IN ('APPROVED','REJECTED') AND (approver_id IS NULL OR approver_id = '')"
        ).fetchall()
        for r in rows:
            issues.append({"level": "WARNING", "type": "empty_approver",
                           "id": r["id"], "detail": f"审批步骤 {r['id']} {r['step_name']} 已审批但审批人为空"})

        # 9. 缺失附件的报销单（已提交之后的状态）
        rows = conn.execute(
            "SELECT r.id FROM reimbursements r "
            "WHERE r.status NOT IN ('DRAFT','REJECTED') "
            "AND r.id NOT IN (SELECT DISTINCT reimbursement_id FROM attachments)"
        ).fetchall()
        for r in rows:
            issues.append({"level": "WARNING", "type": "missing_attachment",
                           "id": r["id"], "detail": f"报销单 {r['id']} 已提交但无附件"})

        # 10. 重复发票号码
        rows = conn.execute(
            "SELECT invoice_number, COUNT(*) as cnt FROM reimbursements "
            "WHERE invoice_number != '' AND status != 'DRAFT' "
            "GROUP BY invoice_number HAVING cnt > 1"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "duplicate_invoice",
                           "id": r["invoice_number"], "detail": f"发票号码 {r['invoice_number']} 出现 {r['cnt']} 次"})

        # 11. 越级状态流转检测
        rows = conn.execute(
            "SELECT al.id, al.target_id, al.before_status, al.after_status FROM audit_log al "
            "WHERE al.action IN ('approve','reject','reimburse','pay','archive','start_review') "
            "AND al.before_status != '' AND al.after_status != ''"
        ).fetchall()
        valid_transitions = {
            ("DRAFT","SUBMITTED"), ("SUBMITTED","DEPT_APPROVING"),
            ("DEPT_APPROVING","FINANCE_REVIEWING"), ("DEPT_APPROVING","REJECTED"),
            ("FINANCE_REVIEWING","APPROVED"), ("FINANCE_REVIEWING","REJECTED"),
            ("APPROVED","PAYMENT_PENDING"), ("PAYMENT_PENDING","PAID"),
            ("PAID","ARCHIVED"), ("REJECTED","SUBMITTED"),
        }
        for r in rows:
            if (r["before_status"], r["after_status"]) not in valid_transitions:
                issues.append({"level": "CRITICAL", "type": "invalid_transition",
                               "id": r["id"], "detail": f"越级流转: 报销单 {r['target_id']} 从 {r['before_status']} 到 {r['after_status']}"})

        # 12. 同一人完成不相容岗位操作
        rows = conn.execute(
            "SELECT r.id, r.applicant_id, af.approver_id, af.step_name "
            "FROM reimbursements r JOIN approval_flow af ON r.id = af.reimbursement_id "
            "WHERE r.applicant_id = af.approver_id AND af.status = 'APPROVED'"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "self_approve",
                           "id": r["id"], "detail": f"报销单 {r['id']}: 申请人自己审批了自己的报销"})

        # 13. 已打款但无付款凭证
        rows = conn.execute(
            "SELECT r.id FROM reimbursements r "
            "WHERE r.status IN ('PAID','ARCHIVED') "
            "AND r.id NOT IN (SELECT DISTINCT reimbursement_id FROM payment_records WHERE payment_voucher != '')"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "no_payment_voucher",
                           "id": r["id"], "detail": f"报销单 {r['id']} 已打款但无付款凭证"})

        # 14. 已通过但无财务审核记录
        rows = conn.execute(
            "SELECT r.id FROM reimbursements r "
            "WHERE r.status IN ('APPROVED','PAYMENT_PENDING','PAID','ARCHIVED') "
            "AND r.id NOT IN (SELECT DISTINCT reimbursement_id FROM voucher_reviews WHERE review_result='passed')"
        ).fetchall()
        for r in rows:
            issues.append({"level": "WARNING", "type": "no_voucher_review",
                           "id": r["id"], "detail": f"报销单 {r['id']} 已通过但无凭证审核记录"})

        # 15. 归档后被修改
        rows = conn.execute(
            "SELECT r.id FROM reimbursements r "
            "WHERE r.status = 'ARCHIVED' "
            "AND r.updated_at > (SELECT a.archived_at FROM archives a WHERE a.reimbursement_id = r.id)"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "archived_modified",
                           "id": r["id"], "detail": f"报销单 {r['id']} 归档后被修改"})

        # 16. 审计日志缺失（已打款单据应有完整日志链）
        rows = conn.execute(
            "SELECT r.id FROM reimbursements r "
            "WHERE r.status IN ('PAID','ARCHIVED') "
            "AND (SELECT COUNT(*) FROM audit_log al WHERE al.target_id = r.id) < 3"
        ).fetchall()
        for r in rows:
            issues.append({"level": "WARNING", "type": "incomplete_audit_trail",
                           "id": r["id"], "detail": f"报销单 {r['id']} 已打款/归档但审计日志不完整"})

        conn.close()
        return issues
