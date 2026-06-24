@echo off
chcp 65001 >nul
title 财务报销系统 v1.5
echo ========================================================
echo   财务报销系统 v1.5
echo ========================================================
echo.
echo   架构: Controller / Service / Workflow / DAO
echo   核心: 状态机 + 配置化审批流 + request_id幂等 + Data Guard
echo.
cd /d "%~dp0"
python app.py
pause
