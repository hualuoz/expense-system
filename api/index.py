"""
Vercel Serverless 入口 — 财务报销系统 v1.5

Vercel 要求:
  - 文件在 api/ 目录下
  - 导出名为 app 的 WSGI 应用实例
  - 每个请求由 Vercel 路由到此文件处理
"""

import os
import sys

# 确保 Python 能找到项目模块
# api/index.py 向上两级 = web/ 目录
WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WEB_DIR)

from app.dao.db import init_db

# 初始化数据库（幂等操作，重复调用安全）
init_db()

from app import create_app

# ✅ Vercel 要求: 导出名为 app 的 WSGI 应用实例
app = create_app()

# ✅ 同时导出 application，兼容某些 WSGI 服务器
application = app
