#!/usr/bin/env python3
"""
Controller 层 — v2.0 架构

职责: 仅负责参数接收、鉴权、调用 Service。
禁止: 业务逻辑、直接操作数据库、直接修改状态。
"""

import json
import os
import hashlib
import sys
from functools import wraps
from flask import Blueprint, jsonify, request, send_file, g

from app.service.services import (
    R, AuthService, ReimbursementService, AuditService,
    SummaryService, DataGuardService, RuleService,
)
from app.dao.db import Status, Role
from app.dao.models import UserDAO, AsyncTaskDAO, AuditLogDAO, AttachmentDAO

WEB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, WEB_DIR)

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
        g.user_agent = request.headers.get("User-Agent", "")
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
    data = request.json or {}
    username = data.get("username", "")
    if not username: return jsonify(R.error("请提供 username"))
    return jsonify(AuthService.login(username))


@bp.route("/auth/me", methods=["GET"])
@require_auth
def me():
    return jsonify(AuthService.get_current_user(g.user_id))


@bp.route("/auth/users", methods=["GET"])
def list_users():
    return jsonify(R.ok(UserDAO.list_all()))


# ============ 报销单 CRUD v2.0 ============

@bp.route("/reimbursements", methods=["GET"])
@require_auth
def list_reimbursements():
    status = request.args.get("status", "ALL")
    department = request.args.get("department", "")
    expense_type = request.args.get("expense_type", "")
    risk_level = request.args.get("risk_level", "")
    return jsonify(ReimbursementService.list_expenses(
        g.user_id, status, department or None, expense_type or None, risk_level or None))


@bp.route("/reimbursements", methods=["POST"])
@require_auth
def create_reimbursement():
    """创建报销单（完整字段 + request_id 幂等）"""
    data = request.json or {}
    items = data.get("items", [])
    request_id = data.get("request_id", "") or request.headers.get("X-Request-ID", "")
    return jsonify(ReimbursementService.create_expense(
        g.user_id, data, request_id or None, user_agent=g.user_agent))


@bp.route("/reimbursements/draft", methods=["POST"])
@require_auth
def save_draft():
    """保存草稿"""
    data = request.json or {}
    return jsonify(ReimbursementService.save_draft(g.user_id, data, user_agent=g.user_agent))


@bp.route("/reimbursements/<rid>", methods=["GET"])
@require_auth
def get_reimbursement(rid):
    is_audit = request.args.get("audit_view", "0") == "1"
    return jsonify(ReimbursementService.get_detail(rid, g.user_id, is_audit_view=is_audit))


@bp.route("/reimbursements/<rid>", methods=["DELETE"])
@require_auth
@require_role(Role.ADMIN)
def delete_reimbursement(rid):
    return jsonify(ReimbursementService.delete_expense(rid, g.user_id, user_agent=g.user_agent))


# ============ 审批流 v2.0 ============

@bp.route("/reimbursements/<rid>/action", methods=["POST"])
@require_auth
def process_action(rid):
    """统一审批动作入口"""
    data = request.json or {}
    action = data.get("action", "")
    comment = data.get("comment", "")
    if not action: return jsonify(R.error("请提供 action"))
    return jsonify(ReimbursementService.process_action(
        rid, g.user_id, action, comment, user_agent=g.user_agent))


# ============ 附件上传 ============

@bp.route("/reimbursements/<rid>/attachments", methods=["POST"])
@require_auth
def upload_attachment(rid):
    """上传附件"""
    if 'file' not in request.files:
        return jsonify(R.error("未找到上传文件"))
    file = request.files['file']
    if file.filename == '':
        return jsonify(R.error("未选择文件"))

    file_content = file.read()
    file_hash = hashlib.md5(file_content).hexdigest()
    file_size = len(file_content)
    attachment_type = request.form.get("attachment_type", "other")

    # 保存文件到 uploads 目录
    upload_dir = os.path.join(WEB_DIR, "uploads", rid)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)
    with open(file_path, "wb") as f:
        f.write(file_content)

    return jsonify(ReimbursementService.upload_attachment(
        rid, g.user_id, file.filename, file.content_type or "",
        file_size, file_hash, attachment_type, user_agent=g.user_agent))


@bp.route("/reimbursements/<rid>/attachments/<att_id>", methods=["GET"])
@require_auth
def download_attachment(rid, att_id):
    """下载附件"""
    att = AttachmentDAO.get_by_id(att_id)
    if not att: return jsonify(R.not_found("附件不存在"))
    file_path = os.path.join(WEB_DIR, "uploads", rid, att["file_name"])
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name=att["file_name"])
    return jsonify(R.not_found("文件不存在"))


# ============ 凭证审核 ============

@bp.route("/reimbursements/<rid>/voucher-review", methods=["POST"])
@require_auth
@require_role(Role.FINANCE_REVIEWER, Role.ADMIN)
def submit_voucher_review(rid):
    """凭证审核"""
    data = request.json or {}
    return jsonify(ReimbursementService.submit_voucher_review(
        rid, g.user_id, data, user_agent=g.user_agent))


# ============ 付款处理 ============

@bp.route("/reimbursements/<rid>/payment", methods=["POST"])
@require_auth
@require_role(Role.CASHIER, Role.ADMIN)
def submit_payment(rid):
    """付款处理"""
    data = request.json or {}
    return jsonify(ReimbursementService.submit_payment(
        rid, g.user_id, data, user_agent=g.user_agent))


# ============ 归档 ============

@bp.route("/reimbursements/<rid>/archive", methods=["POST"])
@require_auth
@require_role(Role.CASHIER, Role.ADMIN)
def archive_reimbursement(rid):
    return jsonify(ReimbursementService.archive_reimbursement(rid, g.user_id, user_agent=g.user_agent))


@bp.route("/reimbursements/<rid>/archive", methods=["GET"])
@require_auth
def get_archive(rid):
    return jsonify(ReimbursementService.get_archive(rid, g.user_id))


# ============ 合规审计 ============

@bp.route("/audit/<rid>", methods=["POST"])
@require_auth
@require_role(Role.FINANCE_REVIEWER, Role.ADMIN)
def audit_reimbursement(rid):
    detail = ReimbursementService.get_detail(rid, g.user_id)
    if detail["code"] != 0: return jsonify(detail)
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


# ============ 审计日志 v2.0 ============

@bp.route("/logs", methods=["GET"])
@require_auth
def get_logs():
    target_id = request.args.get("target_id", "")
    action = request.args.get("action", "")
    operator_id = request.args.get("operator_id", "")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    limit = int(request.args.get("limit", "200"))
    return jsonify(AuditService.get_logs(g.user_id, target_id or None, action or None,
                                          operator_id or None, start_date or None,
                                          end_date or None, limit))


# ============ Data Guard ============

@bp.route("/guard/check", methods=["POST"])
@require_auth
@require_role(Role.ADMIN, Role.AUDITOR)
def run_data_guard():
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
    if file.filename == '': return jsonify(R.error("未选择文件"))
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
    if not task: return jsonify(R.not_found("任务不存在"))
    result = {"task_id":task["id"],"task_type":task["task_type"],"status":task["status"]}
    if task["status"] == "COMPLETED" and task["output_data"]:
        try: result["output"] = json.loads(task["output_data"])
        except (json.JSONDecodeError, TypeError): result["output"] = task["output_data"]
    if task["status"] == "FAILED": result["error"] = task["error"]
    return jsonify(R.ok(result))


# ============ Excel 导出 ============

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
    from app.dao.db import EXPENSE_TYPES
    tree = {
        "TRAVEL":    {"name":"差旅费","children":{"TRAVEL-TRANSPORT":"交通费","TRAVEL-LODGING":"住宿费","TRAVEL-MEAL":"餐饮补贴","TRAVEL-OTHER":"其他差旅"}},
        "OFFICE":    {"name":"办公费","children":{"OFFICE-SUPPLIES":"办公用品","OFFICE-EQUIPMENT":"办公设备","OFFICE-SOFTWARE":"软件订阅"}},
        "ENTERTAIN": {"name":"招待费","children":{"ENTERTAIN-MEAL":"业务餐费","ENTERTAIN-GIFT":"商务礼品"}},
        "TRAINING":  {"name":"培训费","children":{"TRAINING-COURSE":"培训课程","TRAINING-CERT":"认证考试"}},
        "TRANSPORT": {"name":"交通费","children":{"TRANSPORT-TAXI":"出租车","TRANSPORT-PARKING":"停车费","TRANSPORT-FUEL":"油费"}},
        "LODGING":   {"name":"住宿费","children":{"LODGING-HOTEL":"酒店","LODGING-OTHER":"其他住宿"}},
        "PURCHASE":  {"name":"采购费","children":{"PURCHASE-MATERIAL":"物料采购","PURCHASE-ASSET":"资产采购"}},
        "OTHER":     {"name":"其他","children":{"OTHER-MISC":"杂项"}},
    }
    return jsonify(R.ok({"expense_types": EXPENSE_TYPES, "tree": tree}))


@bp.route("/validate", methods=["POST"])
@require_auth
def validate_expense():
    data = request.json or {}
    from validate_expense import validate_single_expense, load_policy
    from datetime import datetime as dt
    policy = load_policy()
    today = dt.now().date()
    expense = {k: data.get(k,"") for k in ["date","category","description","receipt_no","city","employee_level"]}
    expense["amount"] = float(data.get("amount",0))
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
    results = [{"expense":exp,"findings":validate_single_expense(exp,policy,today)} for exp in expenses]
    return jsonify(R.ok({"results": results}))


# ============ 仪表盘统计 v2.0 ============

@bp.route("/dashboard/stats", methods=["GET"])
@require_auth
def get_dashboard_stats():
    """工作台统计数据"""
    from app.dao.models import ReimbursementDAO
    all_r = ReimbursementDAO.list_by_status("ALL")
    role = g.user_role
    uid = g.user_id
    # 按角色过滤
    if role == Role.APPLICANT:
        all_r = [r for r in all_r if r["applicant_id"] == uid]
    elif role == Role.DEPT_MANAGER:
        dept = g.user.get("department","")
        all_r = [r for r in all_r if r.get("department") == dept or r["applicant_id"] == uid]

    by_status = {}
    for r in all_r:
        s = r["status"]
        if s not in by_status: by_status[s] = {"count":0,"amount":0}
        by_status[s]["count"] += 1
        by_status[s]["amount"] += r["total_amount"]

    high_risk = sum(1 for r in all_r if r.get("risk_level") == "high")

    return jsonify(R.ok({
        "total_amount": sum(r["total_amount"] for r in all_r),
        "total_count": len(all_r),
        "by_status": {k: {**v, "display": Status.DISPLAY.get(k,k)} for k,v in by_status.items()},
        "high_risk_count": high_risk,
        "pending_dept": by_status.get(Status.DEPT_APPROVING, {}).get("count", 0),
        "pending_finance": by_status.get(Status.FINANCE_REVIEWING, {}).get("count", 0),
        "pending_payment": by_status.get(Status.PAYMENT_PENDING, {}).get("count", 0),
        "paid_count": by_status.get(Status.PAID, {}).get("count", 0),
        "rejected_count": by_status.get(Status.REJECTED, {}).get("count", 0),
    }))
