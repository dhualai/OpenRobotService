"""派单概览遇到 JSON null 时不要对 None 调用 get。"""

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import (
    _parse_metadata,
    build_index_record,
)


def test_parse_metadata_blank_shapes_are_empty_dict():
    """null 与 {} 都是空白户，解析后是空字典。"""
    for raw in (None, "null", b"null", {}, "{}", " {} ", "[]"):
        assert _parse_metadata(raw) == {}
    assert _parse_metadata('{"fault_code": "E1"}')["fault_code"] == "E1"


def test_dispatch_fields_treat_null_and_empty_object_as_blank():
    from ai.agents.AiDiagnosisPlatform.assigner.pipeline.worker import _meta_fields

    for raw in (None, "null", {}, "{}"):
        fields = _meta_fields(raw)
        assert fields["robot_type"] == ""
        assert fields["fault_code"] == ""
        assert fields["diagnosis_hypotheses"] is None
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import _ticket_meta

    assert _ticket_meta("null") == {}
    assert _ticket_meta("{}") == {}
    assert _ticket_meta(None) == {}


def test_index_record_blank_metadata_keeps_empty_fields():
    for raw in ("null", "{}", None, {}):
        row = build_index_record(
            ticket_id=1,
            engineer_id="u1",
            title="车停了",
            description="规划中",
            task_type="bug",
            metadata=raw,
            keyword_dict={},
        )
        assert row["fault_code"] == ""
        assert row["robot_type"] == ""


def test_module_tree_skips_null_nodes():
    classify, keywords, anchors = AssignerConfig._build_from_tree({
        "USP": None,
        "车端": {
            "interfaces": [
                None,
                {"name": "导航", "functions": [None, {"name": "路径规划", "keywords": ["规划"]}]},
            ],
        },
    })
    assert "路径规划" in classify["车端"]
    assert keywords["车端-路径规划"] == ["规划"]
    assert anchors["车端-路径规划"] == "路径规划"


def test_cluster_snapshot_skips_null_members():
    from ai.agents.AiDiagnosisPlatform.assigner.recall import expertise_recall as er

    er._cache.update({
        "hash": "x",
        "centroids": object(),
        "cluster_people": [{"u-a": None}],
        "cluster_titles": [[]],
        "cluster_tickets": [[None, {"ticket_id": "1", "title": "停了", "engineer_id": "u-a"}]],
        "ticket_total": 1,
        "ticket_points": [None],
    })
    snap = er.cluster_snapshot_from_cache({"u-a": "甲"})
    assert snap["clusters"][0]["people"][0]["count"] == 0
    assert snap["clusters"][0]["tickets"][0]["engineer_name"] == "甲"
    assert snap["points"] == []
    er.invalidate_expertise_cache()
