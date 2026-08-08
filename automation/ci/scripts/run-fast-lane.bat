@echo off
setlocal
set PYTHONPATH=%~dp0..\..
cd /d "%~dp0..\.."
REM ============================================
REM Fast Lane ¡ª ¿ò¼Ü¿â²âÊÔ
REM ============================================
echo ===== Running fast-lane tests =====
pytest src\config\tests\ src\logger\tests\ src\clients\tests\ src\assertions\tests\ src\fixtures\tests\ src\runner\tests\ -v --alluredir=output\allure-results
echo.
call "%~dp0generate-allure-report.bat"
