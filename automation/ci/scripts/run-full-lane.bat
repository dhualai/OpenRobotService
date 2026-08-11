@echo off
setlocal
set PYTHONPATH=%~dp0..\..
cd /d "%~dp0..\.."
REM ============================================
REM Full Lane — 全部 API Mock 测试
REM ============================================
echo ===== Running full-lane API tests =====
pytest tests\ -m api -v --alluredir=output\allure-results
echo.
call "%~dp0generate-allure-report.bat"
