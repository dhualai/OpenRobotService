"""Self-tests for retrieval_recall metric."""

from automation.src.ai_metrics import collection_hit, recall_score


class TestCollectionHit:
    def test_hit_on_title(self):
        items = [{"title": "充电桩更换指南", "content": "步骤..."}]
        assert collection_hit(items, ["充电桩"]) is True

    def test_hit_on_content(self):
        items = [{"title": "指南", "content": "如何更换充电桩"}]
        assert collection_hit(items, ["充电桩"]) is True

    def test_miss(self):
        items = [{"title": "指南", "content": "如何充电"}]
        assert collection_hit(items, ["充电桩"]) is False

    def test_duck_typed_object(self):
        class Item:
            title = "更换充电桩"
            content = "..."

        assert collection_hit([Item()], ["充电桩"]) is True

    def test_empty_results(self):
        assert collection_hit([], ["充电桩"]) is False

    def test_case_insensitive(self):
        items = [{"title": "MQTT Guide", "content": ""}]
        assert collection_hit(items, ["mqtt"]) is True


class TestRecallScore:
    def test_full_recall(self):
        result = recall_score(
            {"retrieve": True, "retrieve_faq": True},
            {"retrieve": ["充电桩"], "retrieve_faq": ["充电"]},
        )
        assert result["recall"] == 1.0
        assert result["missed"] == []
        assert result["skipped"] == []

    def test_partial_recall(self):
        result = recall_score(
            {"retrieve": True, "retrieve_faq": False},
            {"retrieve": ["充电桩"], "retrieve_faq": ["充电"]},
        )
        assert result["recall"] == 0.5
        assert result["missed"] == ["retrieve_faq"]

    def test_skipped_excluded_from_scoring(self):
        result = recall_score(
            {"retrieve": True, "retrieve_faq": None},
            {"retrieve": ["a"], "retrieve_faq": ["b"]},
        )
        assert result["recall"] == 1.0
        assert result["skipped"] == ["retrieve_faq"]

    def test_all_skipped_defaults_to_one(self):
        result = recall_score(
            {"retrieve": None},
            {"retrieve": ["a"]},
        )
        assert result["recall"] == 1.0
