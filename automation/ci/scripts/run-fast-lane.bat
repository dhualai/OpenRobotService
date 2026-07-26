@echo off
set PYTHONPATH=%~dp0..\..
REM ============================================
REM Fast Lane — 基础设施层测试
REM ============================================
echo ===== Running fast-lane tests =====
pytest automation\config\tests\ automation\logger\tests\ automation\clients\tests\ automation\assertions\tests\ automation\fixtures\tests\ -v --alluredir=output\allure-results
echo.
call "%~dp0generate-allure-report.bat"
