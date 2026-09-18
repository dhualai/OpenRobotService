@echo off
rem 数据库结构补丁命令（幂等，可重复运行）—— 补齐代码模型与存量库缺失的列/索引
rem 用法：双击运行，或命令行执行本文件；连接参数见 scripts\apply_schema_patch.py
cd /d "%~dp0..\"
.venv\Scripts\python.exe scripts\apply_schema_patch.py
pause
