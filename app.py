#!/usr/bin/env python3
"""
财务报销系统 v1.5 — 主入口

架构: Controller / Service / Workflow / DAO 分层
核心: 状态机强约束 + 配置化审批流 + request_id幂等 + Data Guard

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
# 优先从 web/ 本地目录找 scripts，找不到再从上级 skill 目录找
sys.path.insert(0, BASE_DIR)  # web/validate_expense.py, web/file_parser.py
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

    # 确保 exports 目录存在
    export_dir = os.path.join(BASE_DIR, "exports")
    os.makedirs(export_dir, exist_ok=True)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/exports/<filename>")
    def download_file(filename):
        return send_from_directory(export_dir, filename, as_attachment=True)

    return app


# ============ 本模块直接运行 ============

app = create_app()

if __name__ == "__main__":
    init_db()
    print("=" * 56)
    print("  💰 财务报销系统 v1.5")
    print("=" * 56)
    print()
    print("  架构: Controller / Service / Workflow / DAO")
    print("  核心: 状态机 + 配置化审批流 + request_id幂等 + Data Guard")
    print("  API:  /api/v1/*")
    print()
    print("  预置用户:")
    print("    张三 (zhangsan)   - 申请人   [applicant]")
    print("    李主管 (lisi)     - 审批人   [approver]")
    print("    王财务 (wangwu)    - 审批人   [approver]")
    print("    赵总经理 (zhaoliu)- 审批人   [approver]")
    print("    admin             - 管理员   [admin]")
    print()
    print("  审批规则(配置化):")
    print("    ≤500      → 主管审批")
    print("    500~5000  → 主管 + 财务审批")
    print("    >5000     → 主管 + 财务 + 总经理审批")
    print()
    print("  本机访问: http://127.0.0.1:5000")
    print("  局域网访问: http://<你的IP>:5000")
    print("  按 Ctrl+C 停止服务")
    print()
    # host="0.0.0.0" 允许外部设备通过局域网/公网IP访问
    # port 优先使用环境变量 PORT（云平台部署需要）
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
