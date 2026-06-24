#!/usr/bin/env python3
"""
Controller 层 — v1.5 架构

职责: 仅负责参数接收、鉴权、调用 Service。
禁止: 业务逻辑、直接操作数据库、直接修改状态。
"""

import json
import os
import sys
from functools import wraps
from flask import Blueprint, jsonify, request, send_file, g

from app.service.services import (
    R, AuthService, ReimbursementService, AuditService,
    SummaryService, DataGuardService, RuleService,
)
from app.dao.db import Status, Role
from app.dao.models import UserDAO, AsyncTaskDAO, AuditLogDAO

WEB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, WEB_DIR)  # web/validate_expense.py, web/file_parser.py

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


# ============ 鉴权中间件 ============

def require_auth(f):
    """鉴权：从 Header 获取 user_id"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_id = request.headers.get("X-User-ID", "")
        if not user_id:
            return jsonify(R.forbidden("未登录，请提供 X-User-ID")), 401
        user = UserDAO.get_by_id(user_id)
        if not user:
            return jsonify(R.forbidden("用户不存在")), 401
        g.user = user
        g.user_id = user_id
        g.user_role = user["role"]
        return f(*args, **kwargs)
    return wrapper


def require_role(*roles):
    """角色检查"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if g.user_role not in roles:
                return jsonify(R.forbidden(f"需要角色: {', '.join(roles)}")), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ============ 认证 ============

@bp.route("/auth/login", methods=["POST"])
def login():
    """登录"""
    data = request.json or {}
    username = data.get("username", "")
    if not username:
        return jsonify(R.error("请提供 username"))
    return jsonify(AuthService.login(username))


@bp.route("/auth/me", methods=["GET"])
@require_auth
def me():
    return jsonify(AuthService.get_current_user(g.user_id))


@bp.route("/auth/users", methods=["GET"])
def list_users():
    """公开: 登录页获取用户列表"""
    return jsonify(R.ok(UserDAO.list_all()))


# ============ 报销单 CRUD ============

@bp.route("/reimbursements", methods=["GET"])
@require_auth
def list_reimbursements():
    """列表"""
    status = request.args.get("status", "ALL")
    return jsonify(ReimbursementService.list_expenses(g.user_id, status))


@bp.route("/reimbursements", methods=["POST"])
@require_auth
@require_role(Role.APPLICANT, Role.ADMIN)
def create_reimbursement():
    """创建报销单（支持 request_id 幂等）"""
    data = request.json or {}
    items = data.get("items", [])
    request_id = data.get("request_id", "") or request.headers.get("X-Request-ID", "")
    return jsonify(ReimbursementService.create_expense(g.user_id, items, request_id or None))


@bp.route("/reimbursements/<rid>", methods=["GET"])
@require_auth
def get_reimbursement(rid):
    """详情"""
    return jsonify(ReimbursementService.get_detail(rid, g.user_id))


@bp.route("/reimbursements/<rid>", methods=["DELETE"])
@require_auth
@require_role(Role.ADMIN)
def delete_reimbursement(rid):
    """删除"""
    return jsonify(ReimbursementService.delete_expense(rid, g.user_id))


# ============ 审批流 ============

@bp.route("/reimbursements/<rid>/action", methods=["POST"])
@require_auth
def process_action(rid):
    """统一审批动作入口"""
    data = request.json or {}
    action = data.get("action", "")
    comment = data.get("comment", "")
    if not action:
        return jsonify(R.error("请提供 action"))

    # 分发到对应 Service 方法（Controller 不写业务逻辑）
    ACTION_MAP = {
        "submit":       lambda: ReimbursementService.submit_expense(rid, g.user_id),
        "start_review": lambda: ReimbursementService.start_review(rid, g.user_id),
        "approve":      lambda: ReimbursementService.approve_expense(rid, g.user_id, comment),
        "reject":       lambda: ReimbursementService.reject_expense(rid, g.user_id, comment),
        "reimburse":    lambda: ReimbursementService.reimburse_expense(rid, g.user_id),
        "resubmit":     lambda: ReimbursementService.resubmit_expense(rid, g.user_id),
    }
    handler = ACTION_MAP.get(action)
    if not handler:
        return jsonify(R.error(f"未知操作: {action}"))
    return jsonify(handler())


# ============ 合规审计 ============

@bp.route("/audit/<rid>", methods=["POST"])
@require_auth
@require_role(Role.APPROVER, Role.ADMIN)
def audit_reimbursement(rid):
    """合规审计"""
    detail = ReimbursementService.get_detail(rid, g.user_id)
    if detail["code"] != 0:
        return jsonify(detail)
    items = detail["data"]["items"]
    from validate_expense import validate_batch, load_policy
    from datetime import datetime as dt
    policy = load_policy()
    today = dt.now().date()
    findings = validate_batch(items, policy, today)
    result = {str(k): v for k, v in findings.items()}
    return jsonify(R.ok({"findings": result, "items": items, "total": len(items)}))


# ============ 汇总统计 ============

@bp.route("/summary", methods=["GET"])
@require_auth
def get_summary():
    return jsonify(SummaryService.get_summary(g.user_id))


# ============ 审计日志 ============

@bp.route("/logs", methods=["GET"])
@require_auth
@require_role(Role.ADMIN)
def get_logs():
    target_id = request.args.get("target_id", "")
    limit = int(request.args.get("limit", "100"))
    return jsonify(AuditService.get_logs(g.user_id, target_id or None, limit))


# ============ Data Guard ============

@bp.route("/guard/check", methods=["POST"])
@require_auth
@require_role(Role.ADMIN)
def run_data_guard():
    """执行数据一致性检查"""
    return jsonify(DataGuardService.run_check(g.user_id))


# ============ 审批规则 ============

@bp.route("/rules", methods=["GET"])
@require_auth
@require_role(Role.ADMIN)
def list_rules():
    return jsonify(RuleService.list_rules(g.user_id))


# ============ 文件解析 (OCR 异步) ============

@bp.route("/parse-file", methods=["POST"])
@require_auth
def parse_file_api():
    if 'file' not in request.files:
        return jsonify(R.error("未找到上传文件"))
    file = request.files['file']
    if file.filename == '':
        return jsonify(R.error("未选择文件"))
    file_content = file.read()
    from tasks.task_runner import submit_ocr_task
    task_id = submit_ocr_task(file.filename, file_content)
    AuditLogDAO.log(g.user_id, g.user["display_name"], "ocr", "file", file.filename)
    return jsonify(R.ok({"task_id": task_id, "status": "PENDING"}))


# ============ 异步任务状态 ============

@bp.route("/tasks/<task_id>", methods=["GET"])
@require_auth
def get_task_status(task_id):
    task = AsyncTaskDAO.get(task_id)
    if not task:
        return jsonify(R.not_found("任务不存在"))
    result = {"task_id": task["id"], "task_type": task["task_type"], "status": task["status"]}
    if task["status"] == "COMPLETED" and task["output_data"]:
        try:
            result["output"] = json.loads(task["output_data"])
        except (json.JSONDecodeError, TypeError):
            result["output"] = task["output_data"]
    if task["status"] == "FAILED":
        result["error"] = task["error"]
    return jsonify(R.ok(result))


# ============ Excel 导出 (异步) ============

@bp.route("/export/<rid>", methods=["POST"])
@require_auth
def export_reimbursement(rid):
    return jsonify(ReimbursementService.export_expense(rid, g.user_id))


@bp.route("/export-download/<rid>", methods=["GET"])
@require_auth
def download_export(rid):
    export_dir = os.path.join(WEB_DIR, "exports")
    filepath = os.path.join(export_dir, f"报销单_{rid}.xlsx")
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True, download_name=f"报销单_{rid}.xlsx")
    return jsonify(R.not_found("导出文件不存在"))


# ============ 报销政策 / 类别 / 验证 ============

@bp.route("/policy", methods=["GET"])
@require_auth
def get_policy():
    from validate_expense import load_policy
    return jsonify(R.ok(load_policy()))


@bp.route("/categories", methods=["GET"])
def get_categories():
    tree = {
        "TRAVEL":    {"name": "差旅费", "children": {"TRAVEL-TRANSPORT": "交通费", "TRAVEL-LODGING": "住宿费", "TRAVEL-MEAL": "餐饮补贴", "TRAVEL-OTHER": "其他差旅"}},
        "OFFICE":    {"name": "办公费", "children": {"OFFICE-SUPPLIES": "办公用品", "OFFICE-EQUIPMENT": "办公设备", "OFFICE-SOFTWARE": "软件订阅"}},
        "ENTERTAIN": {"name": "招待费", "children": {"ENTERTAIN-MEAL": "业务餐费", "ENTERTAIN-GIFT": "商务礼品"}},
        "TRAINING":  {"name": "培训费", "children": {"TRAINING-COURSE": "培训课程", "TRAINING-CERT": "认证考试"}},
        "TRANSPORT": {"name": "交通费", "children": {"TRANSPORT-TAXI": "出租车", "TRANSPORT-PARKING": "停车费", "TRANSPORT-FUEL": "油费"}},
        "OTHER":     {"name": "其他",   "children": {"OTHER-MISC": "杂项"}},
    }
    return jsonify(R.ok(tree))


@bp.route("/validate", methods=["POST"])
@require_auth
def validate_expense():
    data = request.json or {}
    from validate_expense import validate_single_expense, load_policy
    from datetime import datetime as dt
    policy = load_policy()
    today = dt.now().date()
    expense = {k: data.get(k, "") for k in ["date", "category", "description", "receipt_no", "city", "employee_level"]}
    expense["amount"] = float(data.get("amount", 0))
    findings = validate_single_expense(expense, policy, today)
    return jsonify(R.ok({"findings": findings}))


@bp.route("/validate-batch", methods=["POST"])
@require_auth
def validate_batch():
    data = request.json or {}
    expenses = data.get("expenses", [])
    from validate_expense import validate_single_expense, load_policy
    from datetime import datetime as dt
    policy = load_policy()
    today = dt.now().date()
    results = [{"expense": exp, "findings": validate_single_expense(exp, policy, today)} for exp in expenses]
    return jsonify(R.ok({"results": results}))
