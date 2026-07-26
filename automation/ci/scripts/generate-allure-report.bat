@echo off
REM ============================================
REM Generate Allure HTML Report
REM ============================================
echo ===== Generating Allure report =====

set JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot
set PATH=%JAVA_HOME%\bin;%PATH%

allure generate automation\output\allure-results -o automation\output\allure-report --clean

if %errorlevel% equ 0 (
    echo ===== Allure report generated: automation\output\allure-report\index.html =====
    start "" automation\output\allure-report\index.html
) else (
    echo ===== Allure report generation failed =====
    echo Make sure Java 17+ is installed and JAVA_HOME is set correctly.
)
