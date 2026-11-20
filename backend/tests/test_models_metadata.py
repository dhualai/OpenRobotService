"""模型元数据加载守卫（ORS-#981，1016 部署事故回归）。

事故：dar_report 模型漏 import Integer——ast 语法检查通过、create_all 也不
解析列类型，直到部署脚本加载 metadata 时才 NameError，炸掉整个部署流程。
本用例把每个模型的列类型完整解析一遍，把「模型文件写了但跑不通」拦在测试期。
"""


def test_metadata_tables_and_types_resolve():
    from app.models import Base

    assert Base.metadata.tables, "模型元数据为空"
    assert "dar_weekly_reports" in Base.metadata.tables, "dar_report 模型未注册"
    for tname, table in Base.metadata.tables.items():
        for col in table.columns:
            try:
                str(col.type)
            except Exception as e:  # noqa: BLE001
                raise AssertionError(f"{tname}.{col.name} 类型解析失败: {e}") from e
