#!/usr/bin/env python3
"""
财务报销系统 v2.0 — 主入口

架构: Controller / Service / Workflow / DAO 分层
核心: 状态机强约束 + 多级审批 + RBAC + 凭证审核 + 付款复核 + 归档 + Data Guard

部署方式:
  本地: python app.py
  Vercel: vercel deploy
  云服务器: gunicorn app:app
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)
scripts_dir = os.path.join(SKILL_DIR, "scripts")
if os.path.isdir(scripts_dir):
    sys.path.insert(0, scripts_dir)

from flask import Flask, render_template, send_from_directory
from app.dao.db import init_db
from app.controller.routes import bp


def create_app():
    """应用工厂 — 本地 / Vercel / 云服务器通用"""
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
    app.register_blueprint(bp)

    # 确保 exports 和 uploads 目录存在
    for d in ["exports", "uploads"]:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/exports/<filename>")
    def download_file(filename):
        export_dir = os.path.join(BASE_DIR, "exports")
        return send_from_directory(export_dir, filename, as_attachment=True)

    return app


# ============ 本模块直接运行 ============

app = create_app()

if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("  💰 财务报销系统 v2.0")
    print("=" * 60)
    print()
    print("  架构: Controller / Service / Workflow / DAO")
    print("  核心: 状态机 + 多级审批 + RBAC + 凭证审核 + 付款复核 + 归档")
    print("  API:  /api/v1/*")
    print()
    print("  预置用户:")
    print("    张三 (zhangsan)      - 申请人       [applicant]")
    print("    李主管 (lisi)        - 部门负责人   [dept_manager]")
    print("    王财务 (wangwu)      - 财务审核     [finance_reviewer]")
    print("    赵总经理 (zhaoliu)   - 部门负责人   [dept_manager]")
    print("    陈出纳 (cashier1)    - 出纳         [cashier]")
    print("    孙审计 (auditor1)    - 审计员       [auditor]")
    print("    admin               - 管理员       [admin]")
    print()
    print("  审批流(配置化):")
    print("    ≤500      → 部门负责人审批")
    print("    500~5000  → 部门负责人 + 财务审核")
    print("    >5000     → 部门负责人 + 财务审核 + 总经理审批")
    print()
    print("  状态流转:")
    print("    草稿 → 已提交 → 部门审批中 → 财务审核中 → 已通过")
    print("    → 待付款 → 已打款 → 已归档")
    print()
    print("  本机访问: http://127.0.0.1:5000")
    print("  按 Ctrl+C 停止服务")
    print()
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
