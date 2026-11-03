"""配置上下线、列表与删除使用同一状态；备份不作为正式配置加载。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AutoConfigAgent"))
import AutoConfigAgent.server as srv
from utils import skill_runtime as runtime


class FakeRegistry:
    def __init__(self):
        self.packages = {}

    def get(self, province, intent):
        return self.packages.get(f"{province}:{intent}")

    def list_all(self):
        return [{"key": key} for key in self.packages]

    def remove(self, province, intent):
        return self.packages.pop(f"{province}:{intent}", None) is not None


class TestSkillStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "fujian").mkdir()
        self.registry = FakeRegistry()
        for patcher in (
            mock.patch.object(srv, "SKILLS_RUNTIME", self.root),
            mock.patch.object(srv, "_enforce_province_write"),
            mock.patch.object(runtime, "skill_registry", self.registry),
            mock.patch.object(runtime, "IS_DEV", True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(srv.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def package(self, intent="test", local="published", live=None, directory=True):
        base = self.root / "fujian" / intent
        if directory:
            base.mkdir(parents=True)
            if local is not None:
                (base / "_meta.json").write_text(
                    json.dumps({"name": "自定义名称", "status": local}), encoding="utf-8"
                )
        pkg = runtime.SkillScenarioPackage("fujian", intent)
        pkg.meta["config_source"] = "local" if directory else "es"
        if live is not None:
            pkg.meta["skill_meta"] = {"status": live}
        self.registry.packages[pkg.key] = pkg
        return pkg

    def rows(self):
        response = self.client.get("/skills", params={"province": "fujian"})
        self.assertEqual(response.status_code, 200)
        return {s["intent"]: s for s in response.json()["skills"]}

    def status(self, value, intent="test"):
        return self.client.patch(f"/skills/fujian/{intent}/status", json={"status": value})

    def test_offline_refresh_and_delete_use_local_state_over_stale_memory(self):
        self.package(local="offline", live="published")
        self.assertEqual(self.rows()["test"]["status"], "offline")
        response = self.client.delete("/skills/fujian/test", params={"backup": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows(), {})
        self.assertFalse((self.root / "fujian/test").exists())

    def test_published_is_listed_but_cannot_be_deleted(self):
        self.package(live="offline")
        self.assertEqual(self.rows()["test"]["status"], "published")
        response = self.client.delete("/skills/fujian/test")
        self.assertEqual(response.status_code, 409)
        self.assertTrue((self.root / "fujian/test").exists())

    def test_legacy_special_names_can_be_read_offlined_and_deleted_with_backup(self):
        import asyncio
        import httpx
        from urllib.parse import quote

        async def run():
            # 按生产 ASGI 行为只解码一次，避免 TestClient 对 %20 等字面名称二次解码。
            transport = httpx.ASGITransport(app=self.client.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                for intent in ("！@#￥%", "旧活动?渠道=A&B=1", "旧活动%20+1", "旧活动 (A),B、C", "旧活动%2F1"):
                    with self.subTest(intent=intent):
                        self.package(intent=intent, live="published")
                        path = "/skills/fujian/" + quote(intent, safe="")
                        response = await client.get(path + "/config")
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["intent"], intent)
                        self.assertEqual((await client.delete(path)).status_code, 409)
                        response = await client.patch(path + "/status", json={"status": "offline"})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(self.rows()[intent]["status"], "offline")
                        response = await client.delete(path)
                        self.assertEqual(response.status_code, 200)
                        self.assertTrue((self.root / response.json()["backup"]).is_dir())
                        self.assertFalse((self.root / "fujian" / intent).exists())
                        self.assertNotIn(intent, self.rows())
        asyncio.run(run())

    def test_all_states_remain_in_list(self):
        for value in ("published", "draft", "offline"):
            self.package(intent=value, local=value)
        self.assertEqual({s["status"] for s in self.rows().values()}, {"published", "draft", "offline"})

    def test_offline_persists_and_updates_memory_before_refresh(self):
        pkg = self.package(live="published")
        self.assertEqual(self.status("offline").status_code, 200)
        meta = json.loads((self.root / "fujian/test/_meta.json").read_text())
        self.assertEqual(meta["status"], "offline")
        self.assertEqual(meta["name"], "自定义名称")
        self.assertEqual(pkg.meta["skill_meta"]["status"], "offline")
        self.assertEqual(self.rows()["test"]["status"], "offline")
        # 模拟重载后内存元信息尚未加载，仍以持久化状态为准。
        pkg.meta.pop("skill_meta")
        self.assertEqual(self.rows()["test"]["status"], "offline")

    def test_local_write_failure_returns_failure_and_preserves_memory(self):
        pkg = self.package(live="published")
        with mock.patch.object(Path, "write_text", side_effect=PermissionError("read-only")):
            response = self.status("offline")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(pkg.meta["skill_meta"]["status"], "published")
        self.assertEqual(self.rows()["test"]["status"], "published")

    def test_malformed_local_metadata_is_not_overwritten(self):
        self.package()
        path = self.root / "fujian/test/_meta.json"
        path.write_text("{broken")
        self.assertEqual(self.status("offline").status_code, 500)
        self.assertEqual(path.read_text(), "{broken")

    def test_publish_creates_missing_metadata_and_protects_delete(self):
        self.package(local=None)
        with mock.patch.object(srv, "_trigger_reload", new_callable=mock.AsyncMock,
                               return_value={"success": True}):
            response = self.client.post("/skills/fujian/test/reload")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows()["test"]["status"], "published")
        self.assertTrue((self.root / "fujian/test/_meta.json").exists())
        self.assertEqual(self.client.delete("/skills/fujian/test").status_code, 409)

    def test_republish_after_offline_returns_published(self):
        self.package(local="offline", live="offline")
        with mock.patch.object(srv, "_trigger_reload", new_callable=mock.AsyncMock,
                               return_value={"success": True}):
            response = self.client.post("/skills/fujian/test/reload")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows()["test"]["status"], "published")

    def test_production_runtime_status_wins_for_list_and_delete(self):
        self.package(local="offline", live="published")
        with mock.patch.object(runtime, "IS_DEV", False):
            self.assertEqual(self.rows()["test"]["status"], "published")
            self.assertEqual(self.client.delete("/skills/fujian/test").status_code, 409)

    def test_production_falls_back_to_local_status_when_meta_missing(self):
        self.package(local="draft")
        with mock.patch.object(runtime, "IS_DEV", False):
            self.assertEqual(self.rows()["test"]["status"], "draft")

    def test_production_es_failure_does_not_change_snapshot_or_memory(self):
        pkg = self.package(live="published")
        with mock.patch.object(runtime, "IS_DEV", False), \
                mock.patch("services.skill_meta_service.upsert_skill_meta", return_value=False):
            self.assertEqual(self.status("offline").status_code, 500)
        self.assertEqual(pkg.meta["skill_meta"]["status"], "published")
        self.assertEqual(self.rows()["test"]["status"], "published")

    def test_production_es_only_package_can_be_offlined_and_listed(self):
        pkg = self.package(live="published", directory=False)
        with mock.patch.object(runtime, "IS_DEV", False), \
                mock.patch("services.skill_meta_service.upsert_skill_meta", return_value=True) as save, \
                mock.patch("services.redis_config_bus.redis_config_bus") as bus:
            bus.enabled = False
            self.assertEqual(self.status("offline").status_code, 200)
            self.assertEqual(self.rows()["test"]["status"], "offline")
            self.assertEqual(save.call_args.kwargs["overrides"]["status"], "offline")
        self.assertEqual(pkg.meta["skill_meta"]["status"], "offline")
        self.assertFalse((self.root / "fujian/test").exists())

    def test_production_snapshot_failure_keeps_successful_es_state(self):
        self.package(live="published")
        with mock.patch.object(runtime, "IS_DEV", False), \
                mock.patch("services.skill_meta_service.upsert_skill_meta", return_value=True), \
                mock.patch("services.redis_config_bus.redis_config_bus") as bus, \
                mock.patch.object(Path, "write_text", side_effect=PermissionError("read-only")):
            bus.enabled = False
            self.assertEqual(self.status("offline").status_code, 200)
            self.assertEqual(self.rows()["test"]["status"], "offline")

    def test_backups_excluded_from_list_discovery_and_loader(self):
        self.package()
        for name in ("test.bak.123", "__pycache__", ".hidden", "config"):
            self.package(intent=name)
        self.assertEqual(set(self.rows()), {"test"})
        loader = runtime.SkillRuntimeLoader(self.root)
        self.assertEqual(loader.list_intents("fujian"), ["test"])
        self.assertIsNone(loader.load_package("fujian", "test.bak.123"))
        discovered = srv._discover_skills()["fujian"]["intents"]
        self.assertEqual([s["intent"] for s in discovered], ["test"])

    def test_es_backup_is_not_reloaded_as_formal_package(self):
        with mock.patch.object(runtime, "IS_DEV", False), \
                mock.patch("services.es_config_store.es_config_store") as store:
            store.enabled = True
            store.load_all_published.return_value = {
                "fujian:test.bak.123": {"api_nodes": {"node": {"enabled": True}}}
            }
            self.assertEqual(runtime.SkillRuntimeLoader(self.root).load_all(), {})


if __name__ == "__main__":
    unittest.main()
