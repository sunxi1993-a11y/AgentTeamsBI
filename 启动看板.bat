@echo off
chcp 65001 >nul
title AgentTeamsBI 看板启动器

echo.
echo ========================================
echo     AgentTeamsBI 看板启动器
echo ========================================
echo.

REM 检查并启动后端 (7891)
echo [1/2] 检查后端服务...
netstat -ano | findstr ":7891" >nul
if %errorlevel% neq 0 (
    echo     后端未运行，正在启动...
    start "AgentTeamsBI-Backend" cmd /k "cd /d C:\Users\Administrator\.openclaw\workspace\AgentTeamsBI\dashboard && python server.py --port 7891"
    timeout /t 3 >nul
) else (
    echo     后端已在运行 ✓
)

REM 检查并启动前端 (5174)
echo [2/2] 检查前端服务...
netstat -ano | findstr ":5174" >nul
if %errorlevel% neq 0 (
    echo     前端未运行，正在启动...
    start "AgentTeamsBI-Frontend" cmd /k "cd /d C:\Users\Administrator\.openclaw\workspace\AgentTeamsBI\edict\frontend && npm run dev"
    timeout /t 3 >nul
) else (
    echo     前端已在运行 ✓
)

echo.
echo ========================================
echo  启动完成！
echo  前端: http://localhost:5174
echo  后端: http://localhost:7891
echo ========================================
echo.
pause