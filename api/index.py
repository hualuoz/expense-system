"""
Vercel Serverless 入口 — 财务报销系统 v1.5

将 Flask App 适配为 Vercel Serverless Function。
部署后别人通过 https://xxx.vercel.app 即可访问。
"""

import os
import sys

# 确保 Python 能找到项目模块
# api/index.py 向上两级 = web/ 目录
WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WEB_DIR)

# scripts 已复制到 web/ 目录下，无需再查找上级目录

from app.dao.db import init_db
from app import create_app

# 初始化数据库（Vercel 每次冷启动都执行，幂等操作）
init_db()

# 创建 Flask 应用
app = create_app()

# Vercel 要求的 WSGI 入口
# noinspection PyUnresolvedReferences
from app import app as application
