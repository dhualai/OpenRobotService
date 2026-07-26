@echo off
set PYTHONPATH=%~dp0..\..
pytest automation\api\tests\ -m api -v --alluredir=output\allure-results
