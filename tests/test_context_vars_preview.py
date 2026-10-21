"""未发布接口的模板参数与已发布接口同源推导，且预览不写配置。"""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import management as mg


def direct_node(sample, fields=None):
    return {
        "enabled": True, "source_type": "direct", "direct_mode": "passthrough",
        "mock_response": sample, "passthrough_fields": fields or [],
    }


class TestContextVarsPreview(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(mg.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def preview(self, nodes):
        response = self.client.post("/api/skills/context_vars/preview", json={"api_nodes": nodes})
        self.assertEqual(response.status_code, 200, response.text)
        return {v["key"]: v for v in response.json()["data"]}

    def test_unpublished_direct_fields_and_subfields_need_no_registry(self):
        nodes = {"main_api": direct_node({
            "current_package": {"package_name": "", "fee": ""},
            "portrait_style": {"communication_style": "直接"},
            "recommended_packages": [{"offerName": "套餐A", "recommend_actual_price": "58"}],
        }, ["current_package", "portrait_style", "recommended_packages.recommend_actual_price"])}
        original = copy.deepcopy(nodes)
        with mock.patch.object(mg.skill_registry, "get", side_effect=AssertionError("不得查询已发布技能")), \
                mock.patch("services.skill_publisher.publish_config") as publish:
            data = self.preview(nodes)
            publish.assert_not_called()
        self.assertEqual(nodes, original)
        self.assertEqual(data["current_package"]["source"], "passthrough")
        self.assertEqual({s["token"] for s in data["current_package"]["subfields"]},
                         {"current_package[package_name]", "current_package[fee]"})
        self.assertEqual(data["portrait_style"]["subfields"][0]["token"], "communication_style")
        tokens = {s["token"] for s in data["recommended_packages"]["subfields"]}
        self.assertIn("recommend_actual_price", tokens)
        self.assertNotIn("offerName", tokens)
        self.assertEqual(data["portrait_style"]["api_names"], ["main_api"])

    def test_preview_matches_published_inference_for_same_config(self):
        nodes = {"main_api": direct_node({"params": {"extra_info": {
            "current_package": {"package_name": "原套餐"},
            "portrait_style": {"communication_style": "直接"},
        }}}, ["current_package", "portrait_style.communication_style"])}
        draft = self.preview(nodes)
        pkg = SimpleNamespace(config={"api_nodes": nodes})
        with mock.patch.object(mg.skill_registry, "get", return_value=pkg):
            published = asyncio.run(mg.get_context_vars("fujian", "已发布"))["data"]
        self.assertEqual(draft, {v["key"]: v for v in published})

    def test_query_mapping_uses_mapped_fields_from_current_draft(self):
        nodes = {"query_api": {
            "enabled": True, "source_type": "api",
            "mock_response": {"bean": {"usage": {"monthly_flow": 35}}},
            "response_extract": {"raw_usage": "bean.usage"},
            "field_transform": {"usage": {"from": "raw_usage", "type": "passthrough"}},
        }}
        data = self.preview(nodes)
        self.assertEqual(data["usage"]["source"], "field_transform")
        self.assertIn("usage[monthly_flow]", {s["token"] for s in data["usage"]["subfields"]})

    def test_empty_draft_does_not_fall_back_to_published_data(self):
        with mock.patch.object(mg.skill_registry, "get", side_effect=AssertionError("不得读取其他技能")):
            data = self.preview({})
        self.assertFalse(any(v["source"] == "passthrough" for v in data.values()))

    def test_draft_changes_replace_previous_fields(self):
        first = self.preview({"api_a": direct_node({"old_field": {"a": "1"}})})
        second = self.preview({"api_b": direct_node({"new_field": {"b": "2"}})})
        self.assertIn("old_field", first)
        self.assertNotIn("old_field", second)
        self.assertEqual(second["new_field"]["api_names"], ["api_b"])

    def test_malformed_preview_body_is_rejected(self):
        response = self.client.post("/api/skills/context_vars/preview", json={"api_nodes": []})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
