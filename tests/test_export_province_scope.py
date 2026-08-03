"""/skills/export 分省权限收敛测试

省份账号只能导出本省：
- 传了其它 province → 403；
- 未传或传本省 → 收敛到本省后正常导出（不越权拿到其它省）。
本部/鉴权未启用（user_province=None）→ 不限制。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "AutoConfigAgent"))

import AutoConfigAgent.server as srv  # noqa: E402
from AutoConfigAgent.server import export_all_skills  # noqa: E402


class _Req:
    pass


class _FakeReg:
    """最小 registry：两个省各一个技能包。"""
    _DATA = {
        "beijing:营销推荐": {"api_nodes": {"a": 1}, "biz_config": {"b": 1}},
        "liaoning:套餐推荐": {"api_nodes": {"c": 1}, "biz_config": {"d": 1}},
    }

    def list_all(self):
        return [{"key": k} for k in self._DATA]

    def get(self, p, i):
        cfg = self._DATA.get(f"{p}:{i}")
        if cfg is None:
            return None
        return mock.Mock(config=cfg, meta={"config_source": "es"})


def _export(request, province="", intent=""):
    with mock.patch("utils.skill_runtime.skill_registry", _FakeReg()):
        resp = asyncio.run(export_all_skills(request, province=province, intent=intent, download=False))
    return json.loads(resp.body)


class TestExportProvinceScope(unittest.TestCase):
    def test_province_user_cannot_export_other_province(self):
        from fastapi import HTTPException
        with mock.patch.object(srv, "_get_user_province_safe", return_value="beijing"):
            with self.assertRaises(HTTPException) as ctx:
                _export(_Req(), province="liaoning")
            self.assertEqual(ctx.exception.status_code, 403)

    def test_province_user_scoped_to_own_province(self):
        with mock.patch.object(srv, "_get_user_province_safe", return_value="beijing"):
            # 未传 province，也应被收敛为只导出 beijing
            data = _export(_Req(), province="")
        pcodes = {s["province"] for s in data["skills"]}
        self.assertEqual(pcodes, {"beijing"})   # 拿不到 liaoning

    def test_hq_user_exports_all(self):
        with mock.patch.object(srv, "_get_user_province_safe", return_value=None):
            data = _export(_Req(), province="")
        pcodes = {s["province"] for s in data["skills"]}
        self.assertEqual(pcodes, {"beijing", "liaoning"})

    def test_hq_user_can_filter_province(self):
        with mock.patch.object(srv, "_get_user_province_safe", return_value=None):
            data = _export(_Req(), province="liaoning")
        pcodes = {s["province"] for s in data["skills"]}
        self.assertEqual(pcodes, {"liaoning"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
