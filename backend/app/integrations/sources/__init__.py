"""任务源插件目录（INTEGRATION_DESIGN.md §7）。

每个子目录是一个可插拔任务源插件（如 ``zentao``），其 ``__init__.py`` 在 import 时
自注册到 ``registry``。``app/integrations/__init__.py`` 的 ``_load_sources`` 按
``TASK_SOURCES_ENABLED`` 装载启用的插件。
"""
