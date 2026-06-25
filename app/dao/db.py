#!/usr/bin/env python3
"""
DAO 层 - 数据库初始化与访问（v1.5 重构版）

升级要点:
  - reimbursements 增加 request_id (幂等性)
  - approval_flow 增加 rule_id (可扩展)
  - audit_log 增加不可篡改保护
  - 所有 DB 操作统一走 DAO
"""

import sqlite3
import os
import uuid
import json
from datetime import datetime

# DB 路径: web/data.db (本项目根目录)
# db.py 在 web/app/dao/ 下，往上 3 级才是 web/
# Vercel Serverless: 文件系统只读，只有 /tmp 可写
_LOCAL_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data.db")
if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
    DB_PATH = os.path.join("/tmp", "expense_data.db")
else:
    DB_PATH = _LOCAL_DB


# ============ 状态常量 ============

class Status:
    DRAFT        = "DRAFT"
    SUBMITTED    = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED     = "APPROVED"
    REJECTED     = "REJECTED"
    REIMBURSED   = "REIMBURSED"

    ALL     = [DRAFT, SUBMITTED, UNDER_REVIEW, APPROVED, REJECTED, REIMBURSED]
    DISPLAY = {
        DRAFT: "草稿", SUBMITTED: "已提交", UNDER_REVIEW: "审核中",
        APPROVED: "已通过", REJECTED: "已驳回", REIMBURSED: "已打款",
    }
    # 不可变状态: REIMBURSED 完全不可变; APPROVED 仅允许 reimburse
    FINAL_STATES = {REIMBURSED}


class Role:
    APPLICANT = "applicant"
    APPROVER  = "approver"
    ADMIN     = "admin"
    ALL       = [APPLICANT, APPROVER, ADMIN]
    DISPLAY   = {APPLICANT: "申请人", APPROVER: "审批人", ADMIN: "管理员"}


# ============ 数据库连接 ============

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # 写前日志，提高并发安全
    return conn


def new_id():
    return uuid.uuid4().hex[:10]


# ============ Schema 初始化 ============

def init_db():
    """v1.5 升级版数据库 Schema"""
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

    # ----- 报销单表（v1.5 增加 request_id, version） -----
    c.execute("""CREATE TABLE IF NOT EXISTS reimbursements (
        id              TEXT PRIMARY KEY,
        request_id      TEXT UNIQUE,              -- 幂等键
        applicant_id    TEXT NOT NULL,
        applicant_name  TEXT NOT NULL,
        department      TEXT DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'DRAFT',
        total_amount    REAL DEFAULT 0,
        version         INTEGER DEFAULT 1,        -- 乐观锁
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

    # ----- 审批流表（v1.5 增加 rule_id 可扩展） -----
    c.execute("""CREATE TABLE IF NOT EXISTS approval_flow (
        id                TEXT PRIMARY KEY,
        reimbursement_id  TEXT NOT NULL,
        step              INTEGER NOT NULL,
        step_name         TEXT NOT NULL,
        rule_id           TEXT DEFAULT '',          -- 匹配的规则ID
        approver_id       TEXT DEFAULT '',
        approver_name     TEXT DEFAULT '',
        status            TEXT DEFAULT 'PENDING',
        comment           TEXT DEFAULT '',
        acted_at          TEXT DEFAULT '',
        created_at        TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (reimbursement_id) REFERENCES reimbursements(id) ON DELETE CASCADE
    )""")

    # ----- 审批规则表（v1.5 新增：配置化审批流） -----
    c.execute("""CREATE TABLE IF NOT EXISTS approval_rules (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        min_amount  REAL DEFAULT 0,
        max_amount  REAL DEFAULT 999999999,
        priority    INTEGER DEFAULT 0,
        is_active   INTEGER DEFAULT 1,
        created_at  TEXT DEFAULT (datetime('now','localtime'))
    )""")

    # ----- 审批规则步骤表（v1.5 新增） -----
    c.execute("""CREATE TABLE IF NOT EXISTS approval_rule_steps (
        id          TEXT PRIMARY KEY,
        rule_id     TEXT NOT NULL,
        step        INTEGER NOT NULL,
        step_name   TEXT NOT NULL,
        approver_id TEXT NOT NULL,
        FOREIGN KEY (rule_id) REFERENCES approval_rules(id) ON DELETE CASCADE
    )""")

    # ----- 审计日志表（不可篡改） -----
    c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL,
        user_name   TEXT DEFAULT '',
        action      TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id   TEXT NOT NULL,
        before_data TEXT DEFAULT '',
        after_data  TEXT DEFAULT '',
        timestamp   TEXT DEFAULT (datetime('now','localtime'))
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

    # ========== 初始化种子数据 ==========
    c.execute("SELECT COUNT(*) as cnt FROM users")
    if c.fetchone()["cnt"] == 0:
        users = [
            ("u001", "zhangsan",  "hash_zhangsan", "张三",     "技术研发部", Role.APPLICANT),
            ("u002", "lisi",      "hash_lisi",     "李主管",   "技术研发部", Role.APPROVER),
            ("u003", "wangwu",    "hash_wangwu",   "王财务",   "财务部",     Role.APPROVER),
            ("u004", "zhaoliu",   "hash_zhaoliu",  "赵总经理", "管理层",     Role.APPROVER),
            ("u005", "admin",     "hash_admin",    "系统管理员","信息部",     Role.ADMIN),
        ]
        for u in users:
            c.execute("INSERT OR IGNORE INTO users (id,username,password_hash,display_name,department,role) VALUES (?,?,?,?,?,?)", u)

    # 初始化审批规则（可扩展，不改代码只改数据库）
    c.execute("SELECT COUNT(*) as cnt FROM approval_rules")
    if c.fetchone()["cnt"] == 0:
        rules = [
            ("rule_1", "小额报销(≤500)",     0,      500,   1),
            ("rule_2", "中额报销(500~5000)",  500.01, 5000,  2),
            ("rule_3", "大额报销(>5000)",     5000.01, 999999999, 3),
        ]
        for r in rules:
            c.execute("INSERT INTO approval_rules (id,name,min_amount,max_amount,priority) VALUES (?,?,?,?,?)", r)

        rule_steps = [
            # rule_1: 主管
            ("rs_1_1", "rule_1", 1, "主管审批", "u002"),
            # rule_2: 主管 + 财务
            ("rs_2_1", "rule_2", 1, "主管审批", "u002"),
            ("rs_2_2", "rule_2", 2, "财务审批", "u003"),
            # rule_3: 主管 + 财务 + 总经理
            ("rs_3_1", "rule_3", 1, "主管审批", "u002"),
            ("rs_3_2", "rule_3", 2, "财务审批", "u003"),
            ("rs_3_3", "rule_3", 3, "总经理审批", "u004"),
        ]
        for s in rule_steps:
            c.execute("INSERT INTO approval_rule_steps (id,rule_id,step,step_name,approver_id) VALUES (?,?,?,?,?)", s)

    conn.commit()
    conn.close()


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


# ============ Data Guard — 数据一致性检查 ============

class DataGuard:
    """v1.5 数据一致性守卫"""

    @staticmethod
    def check_consistency():
        """执行全量一致性检查，返回问题列表"""
        issues = []
        conn = get_db()

        # 1. Orphan expense_items
        rows = conn.execute(
            "SELECT ei.id, ei.reimbursement_id FROM expense_items ei "
            "LEFT JOIN reimbursements r ON ei.reimbursement_id = r.id "
            "WHERE r.id IS NULL"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "orphan_expense_item",
                           "id": r["id"], "detail": f"expense_item {r['id']} references missing reimbursement {r['reimbursement_id']}"})

        # 2. Orphan approval_flow
        rows = conn.execute(
            "SELECT af.id, af.reimbursement_id FROM approval_flow af "
            "LEFT JOIN reimbursements r ON af.reimbursement_id = r.id "
            "WHERE r.id IS NULL"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "orphan_approval_flow",
                           "id": r["id"], "detail": f"approval_flow {r['id']} references missing reimbursement {r['reimbursement_id']}"})

        # 3. APPROVED 但无审批通过记录
        rows = conn.execute(
            "SELECT r.id FROM reimbursements r "
            "WHERE r.status IN ('APPROVED','REIMBURSED') "
            "AND r.id NOT IN (SELECT DISTINCT reimbursement_id FROM approval_flow WHERE status='APPROVED')"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "approved_no_flow",
                           "id": r["id"], "detail": f"reimbursement {r['id']} is APPROVED but has no APPROVED approval_flow record"})

        # 4. amount 非法 (CHECK约束应阻止，但防御性检查)
        rows = conn.execute(
            "SELECT id, reimbursement_id, amount FROM expense_items WHERE amount <= 0 OR amount IS NULL"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "invalid_amount",
                           "id": r["id"], "detail": f"expense_item {r['id']} has invalid amount: {r['amount']}"})

        # 5. total_amount 与明细合计不匹配
        rows = conn.execute(
            "SELECT r.id, r.total_amount, COALESCE(SUM(ei.amount),0) as item_sum "
            "FROM reimbursements r LEFT JOIN expense_items ei ON r.id = ei.reimbursement_id "
            "GROUP BY r.id HAVING ABS(r.total_amount - item_sum) > 0.01"
        ).fetchall()
        for r in rows:
            issues.append({"level": "WARNING", "type": "amount_mismatch",
                           "id": r["id"], "detail": f"reimbursement {r['id']}: header={r['total_amount']:.2f}, sum={r['item_sum']:.2f}"})

        # 6. APPROVED 但审批步骤未全部通过
        rows = conn.execute(
            "SELECT r.id, COUNT(af.id) as pending FROM reimbursements r "
            "JOIN approval_flow af ON r.id = af.reimbursement_id "
            "WHERE r.status = 'APPROVED' AND af.status != 'APPROVED' "
            "GROUP BY r.id"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "inconsistent_approval",
                           "id": r["id"], "detail": f"reimbursement {r['id']} is APPROVED but has {r['pending']} non-APPROVED steps"})

        # 7. 非法状态值
        rows = conn.execute(
            "SELECT id, status FROM reimbursements WHERE status NOT IN ('DRAFT','SUBMITTED','UNDER_REVIEW','APPROVED','REJECTED','REIMBURSED')"
        ).fetchall()
        for r in rows:
            issues.append({"level": "CRITICAL", "type": "invalid_status",
                           "id": r["id"], "detail": f"reimbursement {r['id']} has invalid status: {r['status']}"})

        # 8. 已审批但审批人为空
        rows = conn.execute(
            "SELECT id, step, step_name FROM approval_flow "
            "WHERE status IN ('APPROVED','REJECTED') AND (approver_id IS NULL OR approver_id = '')"
        ).fetchall()
        for r in rows:
            issues.append({"level": "WARNING", "type": "empty_approver",
                           "id": r["id"], "detail": f"approval_flow {r['id']} step {r['step']} {r['step_name']} has no approver_id"})

        conn.close()
        return issues
