#!/usr/bin/env python3
"""
Gunicorn 启动配置 — 云服务器部署用

用法: gunicorn -c gunicorn_config.py app:app
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 4
threads = 2
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = "info"
chdir = BASE_DIR
