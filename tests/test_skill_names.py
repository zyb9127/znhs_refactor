"""新建名称校验：保留业务标点，拒绝会影响 URL/目录/内部标识的名称。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AutoConfigAgent"))
import AutoConfigAgent.server as srv
from code_generator import validate_template
from services.skill_publisher import PublishResult
from utils import skill_runtime
from utils.skill_names import skill_name_error

CASES = json.loads((Path(__file__).parent / "fixtures/skill_name_cases.json").read_text())


class TestSkillNameCreation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        registry = SimpleNamespace(get=lambda *args: None, list_all=lambda: [])
        for patcher in (
            mock.patch.object(srv, "SKILLS_RUNTIME", self.root),
            mock.patch.object(srv, "_enforce_province_write"),
            mock.patch.object(skill_runtime, "skill_registry", registry),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(srv.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_allowed_punctuation_and_invalid_names(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                self.assertEqual(not skill_name_error(case["name"]), case["valid"])
                errors = validate_template({"meta": {"province": "fujian", "intent": case["name"]}})
                self.assertEqual(not errors, case["valid"])

    def test_length_and_non_text_values(self):
        self.assertEqual(skill_name_error("中" * 50), "")
        self.assertTrue(skill_name_error("中" * 51))
        for value in (None, 12, [], {}):
            with self.subTest(value=value):
                self.assertTrue(skill_name_error(value))

    def test_availability_requires_valid_format_before_duplicate_check(self):
        for case in CASES:
            with self.subTest(name=case["name"]):
                response = self.client.get("/skills/exists", params={"province": "fujian", "intent": case["name"]})
                self.assertEqual(response.status_code, 200 if case["valid"] else 422)
                if case["valid"]:
                    self.assertFalse(response.json()["exists"])

    def test_valid_but_duplicate_name_is_unavailable(self):
        name = "活动（新）、A"
        (self.root / "fujian" / name).mkdir(parents=True)
        response = self.client.get("/skills/exists", params={"province": "fujian", "intent": name})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["exists"])

    def test_disabling_optional_validation_cannot_publish_invalid_names(self):
        with mock.patch.object(srv, "_write_skill_package") as write:
            for case in CASES:
                if case["valid"]:
                    continue
                with self.subTest(name=case["name"]):
                    response = self.client.post("/import/publish", json={
                        "template": {"meta": {"province": "fujian", "intent": case["name"]}},
                        "validate_before_publish": False, "reload": False,
                    })
                    self.assertEqual(response.status_code, 422)
            write.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_valid_name_is_trimmed_and_persisted_without_changing_punctuation(self):
        name = "活动（新），A、B_1-2"
        with mock.patch("services.skill_publisher.publish_config", return_value=PublishResult(True, "ok")):
            response = self.client.post("/import/publish", json={
                "template": {"meta": {"province": "fujian", "intent": f"  {name}  "}},
                "validate_before_publish": False, "reload": False,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["intent"], name)
        meta = json.loads((self.root / "fujian" / name / "_meta.json").read_text())
        self.assertEqual(meta["scenario_id"], name)

    def test_batch_import_cannot_bypass_name_validation_including_dry_run(self):
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), mock.patch("services.skill_publisher.publish_config") as publish:
                response = self.client.post("/skills/import", json={
                    "dry_run": dry_run,
                    "skills": [{"province": "fujian", "intent": "活动#1", "api_nodes": {"n": {"enabled": True}}}],
                })
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["success"])
                self.assertEqual(response.json()["results"][0]["status"], "failed")
                publish.assert_not_called()

    def test_existing_invalid_name_remains_readable_and_is_not_migrated(self):
        name = "旧活动#1"
        folder = self.root / "fujian" / name
        (folder / "config").mkdir(parents=True)
        original = '{"status":"offline","name":"旧名称"}'
        (folder / "_meta.json").write_text(original)
        from urllib.parse import quote
        response = self.client.get(f"/skills/fujian/{quote(name, safe='')}/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((folder / "_meta.json").read_text(), original)


if __name__ == "__main__":
    unittest.main()
