@echo off
REM ============================================
REM Generate Allure HTML Report
REM ============================================
setlocal enabledelayedexpansion

echo ===== Generating Allure report =====

set PROJECT_DIR=%~dp0..\..
set RESULTS_DIR=%PROJECT_DIR%\output\allure-results
set REPORT_DIR=%PROJECT_DIR%\output\allure-report

if not exist "%RESULTS_DIR%" (
    echo ===== No allure results found at %RESULTS_DIR% =====
    echo Run tests first to generate raw data.
    exit /b 1
)

allure generate "%RESULTS_DIR%" -o "%REPORT_DIR%" --clean

if %errorlevel% equ 0 (
    echo ===== Allure report generated =====
    echo ===== Starting HTTP server at http://localhost:8080 =====
    start "Allure Report" cmd /c python -m http.server 8080 --directory "%REPORT_DIR%" ^& pause
    timeout /t 2 /nobreak >nul
    start http://localhost:8080
) else (
    echo ===== Allure report generation failed =====
    echo Make sure Java 17+ is installed and on PATH.
)
