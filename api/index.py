"""
Vercel Serverless 入口 — 财务报销系统 v1.5

Vercel Python Serverless 要求:
  - 文件在 api/ 目录下
  - 导出名为 app 的 WSGI 应用实例
"""

import os
import sys

# 确保 Python 能找到项目模块
# Vercel 部署时，项目根目录 = /var/task/
# api/index.py 位于 /var/task/api/index.py
# 所以向上两级到达 /var/task/
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.dirname(CURRENT_DIR)

# 添加到 Python 路径
if WEB_DIR not in sys.path:
    sys.path.insert(0, WEB_DIR)

# 初始化数据库
from app.dao.db import init_db
init_db()

# 创建 Flask 应用
from app import create_app

app = create_app()
application = app
