"""
test_es_rest_adapter — ES 客户端版本兼容层单元测试

背景：本模块全程用 elasticsearch-py 7.x 的调用风格（body= / ignore= / doc_type=）。
8.x 起移除了 http_auth、body、doc_type、ignore 等用法，且默认拒连非 8.x 服务端，
线上装到 8.x/9.x 时官方客户端不可用 → 配置读写全失败 → 页面查不到 ES 配置。
兼容层做法：装 7.x 走官方客户端（行为不变），未装或 8.x+ 自动改走内置 REST 适配器。

覆盖：
  - _lib_major 版本号解析（元组 / 字符串 / 未安装）
  - _build_client 传输方式选择（<=7 官方客户端；>=8 或未安装 → REST 适配器）
  - REST 适配器的 HTTP 方法与路径翻译（index/get/update/delete/search/indices.*）
  - 文档 _id 含 ':' 与中文时的百分号编码（否则 ES 按路径分隔符解析致 404）
  - ignore 语义（命中状态码返回响应体，否则抛异常）
  - 多节点故障转移与全部不可达
  - 端到端：用内存版 ES 驱动真实 ESConfigStore（建索引→发布→读取→归档）

不发任何真实网络请求。
运行：cd ROOT && python -m unittest tests.test_es_rest_adapter -v
"""
import json
import unittest
from unittest import mock
from urllib.parse import unquote

import httpx

from services import es_config_store as es_mod
from services.es_config_store import (
    ESConfigStore,
    _EsHttpError,
    _RestEsClient,
    _quote_seg,
)

_ORIG_HTTPX_CLIENT = httpx.Client


class MiniES:
    """内存版 ES：仅实现本模块用到的 REST 端点，用于驱动 _RestEsClient。"""

    def __init__(self, version: str = "7.10.2") -> None:
        self.version = version
        self.docs: dict = {}      # (index, id) -> _source
        self.indices: dict = {}   # index -> mappings
        self.seen: list = []      # [(method, decoded_path)]

    # ── 路由 ──────────────────────────────────────────────
    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        self.seen.append((method, unquote(path)))
        body = json.loads(request.content) if request.content else None
        segs = [unquote(s) for s in path.strip("/").split("/") if s]

        if not segs:  # GET / → 版本信息
            return httpx.Response(200, json={"version": {"number": self.version}})

        index = segs[0]

        if len(segs) == 1 and method == "PUT":  # 建索引
            if index in self.indices:
                return httpx.Response(400, json={
                    "error": {"type": "resource_already_exists_exception"}, "status": 400,
                })
            self.indices[index] = (body or {}).get("mappings", {})
            return httpx.Response(200, json={"acknowledged": True})

        if len(segs) == 2 and segs[1] == "_mapping" and method == "GET":
            if index not in self.indices:
                return httpx.Response(404, json={"error": {"type": "index_not_found_exception"}})
            return httpx.Response(200, json={index: {"mappings": self.indices[index]}})

        if len(segs) == 2 and segs[1] == "_settings" and method == "PUT":
            return httpx.Response(200, json={"acknowledged": True})

        if len(segs) == 2 and segs[1] == "_search" and method == "POST":
            return httpx.Response(200, json=self._search(index, body or {}))

        # 7.x：POST /{index}/_update/{id}；5.x/6.x：POST /{index}/{type}/{id}/_update
        if len(segs) == 3 and segs[1] == "_update" and method == "POST":
            return self._update(index, segs[2], body)
        if len(segs) == 4 and segs[3] == "_update" and method == "POST":
            return self._update(index, segs[2], body)

        if len(segs) == 3:  # 文档级 /{index}/{type}/{id}
            key = (index, segs[2])
            if method == "PUT":
                created = key not in self.docs
                self.docs[key] = body or {}
                return httpx.Response(200, json={"result": "created" if created else "updated"})
            if method == "GET":
                if key not in self.docs:
                    return httpx.Response(404, json={"_id": segs[2], "found": False})
                return httpx.Response(200, json={"found": True, "_id": segs[2],
                                                 "_source": self.docs[key]})
            if method == "DELETE":
                if key not in self.docs:
                    return httpx.Response(404, json={"result": "not_found"})
                del self.docs[key]
                return httpx.Response(200, json={"result": "deleted"})

        return httpx.Response(400, json={"error": {"type": "unsupported", "path": path}})

    def _update(self, index: str, doc_id: str, body) -> httpx.Response:
        key = (index, doc_id)
        if key not in self.docs:
            return httpx.Response(404, json={"result": "not_found"})
        self.docs[key].update((body or {}).get("doc", {}))
        return httpx.Response(200, json={"result": "updated"})

    def _search(self, index: str, body: dict) -> dict:
        q = body.get("query") or {}
        hits = []
        for (idx, doc_id), src in self.docs.items():
            if idx != index:
                continue
            if "exists" in q and not src.get(q["exists"].get("field")):
                continue
            fields = body.get("_source")
            out = {k: src[k] for k in fields if k in src} if isinstance(fields, list) else src
            hits.append({"_id": doc_id, "_source": out})
        return {"hits": {"hits": hits, "total": {"value": len(hits)}}}


def _mock_httpx_client(handler):
    """把 httpx.Client 替换为走 MockTransport 的实例（丢弃 verify/auth 等真实参数）。"""
    def _factory(**_kwargs):
        return _ORIG_HTTPX_CLIENT(transport=httpx.MockTransport(handler))
    return mock.patch.object(httpx, "Client", _factory)


def _make_client(mini: MiniES, hosts=None) -> _RestEsClient:
    with _mock_httpx_client(mini.handler):
        return _RestEsClient(hosts or ["http://es.test:9200"], "developer", "pw")


class TestLibMajor(unittest.TestCase):
    """已安装客户端主版本号解析"""

    def _patched(self, version):
        fake_mod = mock.MagicMock()
        fake_mod.__version__ = version
        with mock.patch.dict("sys.modules", {"elasticsearch": fake_mod}):
            return ESConfigStore._lib_major()

    def test_tuple_version(self):
        # 7.x 的 __version__ 是元组
        self.assertEqual(self._patched((7, 17, 9)), 7)

    def test_string_version(self):
        self.assertEqual(self._patched("8.13.0"), 8)

    def test_unparsable_version(self):
        self.assertIsNone(self._patched(None))

    def test_not_installed(self):
        # import elasticsearch 抛 ImportError → None
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *a, **kw):
            if name == "elasticsearch":
                raise ImportError("no module")
            return real_import(name, *a, **kw)

        with mock.patch("builtins.__import__", fake_import):
            self.assertIsNone(ESConfigStore._lib_major())


class TestBuildClientSelection(unittest.TestCase):
    """传输方式选择：<=7 官方客户端；>=8 或未安装 → REST 适配器"""

    def setUp(self):
        self.store = ESConfigStore()
        self.mini = MiniES()

    def test_v8_uses_rest_adapter(self):
        with mock.patch.object(ESConfigStore, "_lib_major", staticmethod(lambda: 8)), \
                _mock_httpx_client(self.mini.handler):
            client, desc = self.store._build_client(["http://es.test:9200"], "u", "p")
        self.assertIsInstance(client, _RestEsClient)
        self.assertIn("REST", desc)

    def test_v9_uses_rest_adapter(self):
        with mock.patch.object(ESConfigStore, "_lib_major", staticmethod(lambda: 9)), \
                _mock_httpx_client(self.mini.handler):
            client, _ = self.store._build_client(["http://es.test:9200"], "u", "p")
        self.assertIsInstance(client, _RestEsClient)

    def test_not_installed_uses_rest_adapter(self):
        with mock.patch.object(ESConfigStore, "_lib_major", staticmethod(lambda: None)), \
                _mock_httpx_client(self.mini.handler):
            client, _ = self.store._build_client(["http://es.test:9200"], "u", "p")
        self.assertIsInstance(client, _RestEsClient)

    def test_v7_uses_official_client(self):
        sentinel = object()
        fake_es_mod = mock.MagicMock()
        fake_es_mod.Elasticsearch = mock.MagicMock(return_value=sentinel)
        with mock.patch.object(ESConfigStore, "_lib_major", staticmethod(lambda: 7)), \
                mock.patch.dict("sys.modules", {"elasticsearch": fake_es_mod}):
            client, desc = self.store._build_client(["http://es.test:9200"], "u", "p")
        self.assertIs(client, sentinel)
        self.assertIn("官方客户端", desc)
        # 7.x 必须使用 http_auth（8.x 才改名 basic_auth）
        kwargs = fake_es_mod.Elasticsearch.call_args.kwargs
        self.assertEqual(kwargs["http_auth"], ("u", "p"))


class TestRestAdapterTranslation(unittest.TestCase):
    """HTTP 方法 / 路径翻译与 ignore 语义"""

    def setUp(self):
        self.mini = MiniES()
        self.c = _make_client(self.mini)

    def test_info(self):
        self.assertEqual(self.c.info()["version"]["number"], "7.10.2")
        self.assertEqual(self.mini.seen[-1], ("GET", "/"))

    def test_index_and_get_roundtrip(self):
        self.c.index(index="idx", id="a1", body={"v": 1})
        self.assertEqual(("PUT", "/idx/_doc/a1"), self.mini.seen[-1])
        got = self.c.get(index="idx", id="a1")
        self.assertTrue(got["found"])
        self.assertEqual(got["_source"], {"v": 1})

    def test_doc_type_changes_path(self):
        """5.x/6.x 带 doc_type 时走 /{index}/{type}/{id}"""
        self.c.index(index="idx", id="a1", body={"v": 1}, doc_type="doc")
        self.assertEqual(("PUT", "/idx/doc/a1"), self.mini.seen[-1])

    def test_update_path_7x_vs_6x(self):
        self.c.index(index="idx", id="a1", body={"status": "published"})
        self.c.update(index="idx", id="a1", body={"doc": {"status": "archived"}})
        self.assertEqual(("POST", "/idx/_update/a1"), self.mini.seen[-1])
        self.assertEqual(self.mini.docs[("idx", "a1")]["status"], "archived")

        self.c.update(index="idx", id="a1", body={"doc": {"status": "published"}}, doc_type="doc")
        self.assertEqual(("POST", "/idx/doc/a1/_update"), self.mini.seen[-1])

    def test_delete(self):
        self.c.index(index="idx", id="a1", body={"v": 1})
        self.assertEqual(self.c.delete(index="idx", id="a1")["result"], "deleted")
        self.assertEqual(("DELETE", "/idx/_doc/a1"), self.mini.seen[-1])

    def test_search(self):
        self.c.index(index="idx", id="a1", body={"province": "tianjin"})
        resp = self.c.search(index="idx", body={"query": {"match_all": {}}, "size": 10})
        self.assertEqual(("POST", "/idx/_search"), self.mini.seen[-1])
        self.assertEqual(len(resp["hits"]["hits"]), 1)

    def test_indices_api(self):
        self.assertTrue(self.c.indices.create(index="idx", body={"mappings": {}})["acknowledged"])
        self.assertEqual(("PUT", "/idx"), self.mini.seen[-1])
        self.assertIn("idx", self.c.indices.get_mapping(index="idx"))
        self.assertEqual(("GET", "/idx/_mapping"), self.mini.seen[-1])
        self.c.indices.put_settings(index="idx", body={"index": {}})
        self.assertEqual(("PUT", "/idx/_settings"), self.mini.seen[-1])

    def test_chinese_and_colon_doc_id_encoded(self):
        """真实 _id 形如 tianjin:营销活动:biz_config:1，必须整段百分号编码"""
        doc_id = "tianjin:营销活动:biz_config:1"
        self.c.index(index="idx", id=doc_id, body={"v": 1})
        raw_path = self.c._doc_path("idx", doc_id)
        self.assertNotIn(":", raw_path.split("/_doc/")[1])
        self.assertNotIn("营销活动", raw_path)
        self.assertIn("%3A", raw_path)
        # 编码后服务端仍能解回原始 id
        self.assertTrue(self.c.get(index="idx", id=doc_id)["found"])

    def test_ignore_404_returns_body(self):
        got = self.c.get(index="idx", id="missing", ignore=[404])
        self.assertFalse(got["found"])

    def test_ignore_accepts_scalar(self):
        self.assertFalse(self.c.get(index="idx", id="missing", ignore=404)["found"])

    def test_unignored_error_raises(self):
        with self.assertRaises(_EsHttpError) as ctx:
            self.c.get(index="idx", id="missing")
        self.assertEqual(ctx.exception.status, 404)

    def test_create_existing_index_with_ignore_400(self):
        """indices.create(ignore=[400]) 需返回 already_exists 错误体，供 _create_resp_status 判定"""
        self.c.indices.create(index="idx", body={"mappings": {}}, ignore=[400])
        resp = self.c.indices.create(index="idx", body={"mappings": {}}, ignore=[400])
        self.assertEqual(ESConfigStore._create_resp_status(resp), "exists")


class TestRestAdapterFailover(unittest.TestCase):
    """多节点故障转移"""

    def test_first_host_down_second_ok(self):
        mini = MiniES()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if request.url.host == "down.test":
                raise httpx.ConnectError("connection refused")
            return mini.handler(request)

        with _mock_httpx_client(handler):
            c = _RestEsClient(["http://down.test:9200", "http://es.test:9200"], "u", "p")
        self.assertEqual(c.info()["version"]["number"], "7.10.2")
        self.assertEqual(calls["n"], 2)  # 第一个节点失败后切换到第二个

    def test_all_hosts_down(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _mock_httpx_client(handler):
            c = _RestEsClient(["http://a.test:9200", "http://b.test:9200"], "u", "p")
        with self.assertRaises(ConnectionError) as ctx:
            c.info()
        self.assertIn("所有节点均不可达", str(ctx.exception))

    def test_empty_hosts_rejected(self):
        with self.assertRaises(ValueError):
            _RestEsClient([], "u", "p")


class TestStoreEndToEndOverRest(unittest.TestCase):
    """端到端：用内存版 ES 驱动真实 ESConfigStore（强制走 REST 适配器）"""

    def setUp(self):
        self.store = ESConfigStore()
        # 单例状态与模块级索引名需完整备份，避免污染其它测试
        self._saved = (
            self.store._client, self.store._enabled, self.store._doc_type,
            es_mod.INDEX_CONFIGS, es_mod.INDEX_META,
        )
        self.mini = MiniES()
        with mock.patch.object(ESConfigStore, "_lib_major", staticmethod(lambda: 9)), \
                _mock_httpx_client(self.mini.handler):
            self.store.init({
                "hosts": ["http://es.test:9200"],
                "username": "developer", "password": "pw",
                "index_configs": "t-configs", "index_meta": "t-meta",
            })

    def tearDown(self):
        (self.store._client, self.store._enabled, self.store._doc_type,
         es_mod.INDEX_CONFIGS, es_mod.INDEX_META) = self._saved

    def test_init_uses_rest_and_creates_indices(self):
        self.assertTrue(self.store.enabled)
        self.assertIsInstance(self.store._client, _RestEsClient)
        # 两个索引都已建出来
        self.assertIn("t-configs", self.mini.indices)
        self.assertIn("t-meta", self.mini.indices)
        # 7.x 服务端 → 不带 doc_type
        self.assertIsNone(self.store._doc_type)

    def test_publish_then_read_back(self):
        ok, msg, v = self.store.save_and_publish(
            "tianjin", "营销活动", "biz_config",
            {"strategy": {"script_temperature": 0.8}}, operator="tester", notify=False,
        )
        self.assertTrue(ok, msg)
        self.assertEqual(v, 1)

        info = self.store.get_current_version_info("tianjin", "营销活动", "biz_config")
        self.assertEqual(info.get("published_version"), 1)

        published = self.store.get_all_published_versions()
        self.assertEqual(published.get("tianjin:营销活动:biz_config"), 1)

        allcfg = self.store.load_all_published()
        self.assertEqual(
            allcfg["tianjin:营销活动"]["biz_config"]["strategy"]["script_temperature"], 0.8,
        )

    def test_second_publish_archives_old_version(self):
        self.store.save_and_publish("tianjin", "营销活动", "biz_config",
                                    {"n": 1}, notify=False)
        ok, _, v2 = self.store.save_and_publish("tianjin", "营销活动", "biz_config",
                                                {"n": 2}, notify=False)
        self.assertTrue(ok)
        self.assertEqual(v2, 2)
        # v1 被归档、v2 为 published
        self.assertEqual(self.mini.docs[("t-configs", "tianjin:营销活动:biz_config:1")]["status"],
                         "archived")
        self.assertEqual(self.mini.docs[("t-configs", "tianjin:营销活动:biz_config:2")]["status"],
                         "published")
        # 读回的是最新版
        allcfg = self.store.load_all_published()
        self.assertEqual(allcfg["tianjin:营销活动"]["biz_config"], {"n": 2})


if __name__ == "__main__":
    unittest.main(verbosity=2)
