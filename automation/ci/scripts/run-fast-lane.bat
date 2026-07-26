@echo off
set PYTHONPATH=%~dp0..\..
pytest automation\config\tests\ automation\logger\tests\ automation\clients\tests\ automation\assertions\tests\ automation\fixtures\tests\ -v --alluredir=output\allure-results
