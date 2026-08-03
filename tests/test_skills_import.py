"""AutoConfigAgent /skills/import 往返导入端点单测

锁定「导出文件可直接导入还原到 ES」的行为：
- 逐技能包按 config_type 走唯一写路径 publish_config（保存即自愈），batch 关广播；
- dry_run 只校验不写；
- 无写权限的技能包 skip、不阻断其余；缺配置的技能包记 failed。
不接触真实 ES/Redis（publish_config 全程 mock）。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
import unittest.mock
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "AutoConfigAgent"))

import AutoConfigAgent.server as srv  # noqa: E402
from AutoConfigAgent.server import SkillsImportRequest, import_all_skills  # noqa: E402
from services.skill_publisher import PublishResult  # noqa: E402


class _Req:  # 占位 Request
    pass


def _export(skills):
    return {"export_meta": {"total": len(skills)}, "skills": skills}


_ONE = {
    "province": "beijing", "intent": "营销推荐",
    "api_nodes": {"n_api": {"field_transform": {
        "usage.data_usage": {"from": "bean.tags", "type": "filter_include",
                             "include_keys": ["实际近6月平均流量（GB）"]}}}},
    "biz_config": {"script_templates_v2": [{"product_id": "1", "template_content": "x"}]},
}


class TestSkillsImport(unittest.TestCase):
    def setUp(self):
        self.captured = []

        def _fake_publish(province, intent, config_type, data, **kw):
            self.captured.append((province, intent, config_type,
                                  kw.get("broadcast"), kw.get("reload")))
            return PublishResult(True, "ok")

        self._patches = [
            unittest.mock.patch.object(srv, "_enforce_province_write", lambda *a, **k: None),
            unittest.mock.patch("services.skill_publisher.publish_config", _fake_publish),
            unittest.mock.patch("utils.skill_runtime.skill_registry.reload", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _run(self, req):
        return asyncio.run(import_all_skills(req, _Req()))

    def test_dry_run_validates_without_writing(self):
        res = self._run(SkillsImportRequest(**_export([_ONE]), dry_run=True))
        self.assertTrue(res["success"])
        self.assertEqual(res["summary"], {"ok": 1, "skipped": 0, "failed": 0, "total": 1})
        self.assertEqual(self.captured, [])   # dry_run 不写

    def test_import_writes_both_config_types_via_choke_batched(self):
        res = self._run(SkillsImportRequest(**_export([_ONE])))
        self.assertTrue(res["success"])
        self.assertEqual(res["summary"]["ok"], 1)
        cts = {c[2] for c in self.captured}
        self.assertEqual(cts, {"api_nodes", "biz_config"})
        # 批量：每次写入都关广播/热重载（收尾统一广播一次）
        self.assertTrue(all(c[3] is False and c[4] is False for c in self.captured))

    def test_accepts_bare_skills_list(self):
        res = self._run(SkillsImportRequest(skills=[_ONE]))
        self.assertTrue(res["success"])

    def test_skill_without_config_is_failed(self):
        res = self._run(SkillsImportRequest(skills=[{"province": "beijing", "intent": "空"}]))
        self.assertFalse(res["success"])
        self.assertEqual(res["results"][0]["status"], "failed")

    def test_no_write_permission_is_skipped_not_fatal(self):
        def _deny(request, province):
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="no perm")

        with unittest.mock.patch.object(srv, "_enforce_province_write", _deny):
            res = self._run(SkillsImportRequest(skills=[_ONE]))
        self.assertEqual(res["summary"]["skipped"], 1)
        self.assertEqual(res["results"][0]["status"], "skipped")
        self.assertEqual(self.captured, [])

    def test_empty_payload_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self._run(SkillsImportRequest(skills=[]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
