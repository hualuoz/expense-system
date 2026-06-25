"""
Vercel Serverless 入口 — 财务报销系统 v1.5

Vercel Python Runtime 注意事项:
  - 文件在 api/ 目录下
  - 导出名为 app 的 WSGI 应用实例
  - 项目根目录自动加入 sys.path
  - Vercel 文件系统只读，SQLite 必须放在 /tmp
"""

import os
import sys

# ---- 路径设置 ----
# Vercel 部署: 项目根目录 = /var/task/
# api/index.py = /var/task/api/index.py
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

# 将项目根目录加入搜索路径，确保能找到 app/ 包
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---- 数据库初始化 ----
from app.dao.db import init_db
init_db()

# ---- 创建 Flask 应用 ----
from flask import Flask, render_template, send_from_directory
from app.controller.routes import bp

TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "templates")
EXPORT_DIR = os.path.join(PROJECT_ROOT, "exports")

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.register_blueprint(bp)

# 确保 exports 目录存在（Vercel 上不需要，但防止报错）
try:
    os.makedirs(EXPORT_DIR, exist_ok=True)
except OSError:
    pass


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/exports/<filename>")
def download_file(filename):
    return send_from_directory(EXPORT_DIR, filename, as_attachment=True)


# Vercel 要求导出名为 app 的 WSGI 实例
application = app
