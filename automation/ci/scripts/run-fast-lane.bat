@echo off
setlocal
set PYTHONPATH=%~dp0..\..
cd /d "%~dp0..\.."
REM ============================================
REM Fast Lane ¡ª ¿ò¼Ü¿â²âÊÔ
REM ============================================
echo ===== Running fast-lane tests =====
pytest config\tests\ src\logger\tests\ src\clients\tests\ src\assertions\tests\ src\fixtures\tests\ src\ai_metrics\tests\ src\runtime\tests\ src\client\tests\ src\contract\tests\ src\remote\tests\ src\ticket_pipeline\tests\ src\reporting\tests\ ci_ai_gen\tests\ mcp_server\tests\ -v --alluredir=output\allure-results
echo.
call "%~dp0generate-allure-report.bat"
