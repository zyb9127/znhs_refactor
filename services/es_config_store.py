"""
ES 配置存储服务

职责：
  - 持久化存储省份技能包配置（biz_config / api_nodes）
  - 版本管理：published / archived
  - 发布后通过 Redis Pub/Sub 通知所有实例热重载

ES Index：
  znhs-agent-skill-configs       — 配置版本数据
  znhs-agent-skill-config-meta   — 版本指针（published_version / versions）

文档 _id：
  configs : {province}:{intent}:{config_type}:{version}
  meta    : {province}:{intent}:{config_type}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

INDEX_CONFIGS = "znhs-agent-skill-configs"
INDEX_META    = "znhs-agent-skill-config-meta"
MAX_ARCHIVED  = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ESConfigStore:
    """ES 配置版本存储（单例）"""

    _instance: Optional["ESConfigStore"] = None
    _client = None
    _enabled: bool = False

    def __new__(cls) -> "ESConfigStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 初始化 ──────────────────────────────────────────────────

    def init(self, cfg: Dict[str, Any]) -> None:
        global INDEX_CONFIGS, INDEX_META, MAX_ARCHIVED
        INDEX_CONFIGS = cfg.get("index_configs", INDEX_CONFIGS)
        INDEX_META    = cfg.get("index_meta",    INDEX_META)
        MAX_ARCHIVED  = int(cfg.get("max_versions", MAX_ARCHIVED))

        hosts    = cfg.get("hosts", [])
        username = cfg.get("username", "")
        password = cfg.get("password", "")

        if not hosts:
            logger.warning("[ESConfigStore] 未配置 hosts，ES 存储不可用")
            return

        try:
            from elasticsearch import Elasticsearch
            kwargs: Dict[str, Any] = {"hosts": hosts, "verify_certs": False, "ssl_show_warn": False}
            if username:
                kwargs["http_auth"] = (username, password)
            self._client = Elasticsearch(**kwargs)
            self._enabled = True
            self._detect_doc_type()
            self._ensure_indices()
            logger.info("[ESConfigStore] ES 初始化完成")
        except ImportError:
            logger.warning("[ESConfigStore] elasticsearch-py 未安装，ES 存储不可用")
        except Exception as e:
            logger.warning(f"[ESConfigStore] ES 初始化失败（不影响主服务）: {e}")

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    # ── 版本 / doc_type 适配 ─────────────────────────────────────
    # ES 5.x：type 名不能以 '_' 开头（用 "doc"），文档 API 必须带 type
    # ES 6.x：约定用 "_doc"（6.2+）
    # ES 7.x+：无 type 概念，es-py 客户端默认路径 /_doc/ 即可
    _doc_type: Optional[str] = None

    def _detect_doc_type(self) -> None:
        """探测 ES 服务端主版本，决定索引 mapping 与文档 API 的 type 用法。"""
        self._doc_type = None
        try:
            ver = str(self._client.info().get("version", {}).get("number", ""))
            major = int(ver.split(".")[0])
            if major <= 5:
                self._doc_type = "doc"
            elif major == 6:
                self._doc_type = "_doc"
            logger.info(f"[ESConfigStore] ES server v{ver}，doc_type={self._doc_type or '(无)'}")
        except Exception as e:
            logger.warning(f"[ESConfigStore] 无法探测 ES 版本（按 7.x 处理，异常再回退）: {e}")

    def _dt_kwargs(self) -> Dict[str, Any]:
        """文档级 API（index/get/update/delete）的 doc_type 参数。"""
        return {"doc_type": self._doc_type} if self._doc_type else {}

    def _probe_index_doc_type(self, index: str) -> Optional[str]:
        """读取指定索引的真实 mapping，返回其中的 type 名。

        - ES 5.x/6.x：mappings 下第一层键即为 type 名（如 "doc" / "biz_config"）
        - ES 7.x+：mappings 直接是 properties/dynamic，无 type → 返回 None

        返回 None 表示「无 type（7.x）」或「无法确定（空索引/无权限）」，
        由调用方结合上下文决定是否覆盖。
        """
        try:
            resp = self._client.indices.get_mapping(index=index)
            mappings = (resp.get(index) or {}).get("mappings") or {}
            type_keys = [k for k in mappings if k not in ("properties", "dynamic", "_meta", "_source")]
            return type_keys[0] if type_keys else None
        except Exception as e:
            logger.debug(f"[ESConfigStore] 读取 {index} mapping 失败: {e}")
            return None

    def _resolve_existing_doc_type(self, index: str) -> None:
        """从已存在索引的 mapping 中解析真实 type 名并覆盖探测值。

        已有索引可能是历史代码/人工用其他 type 创建的，读写必须与之一致。
        注意：即使版本探测得到 None（探测失败或误判 7.x），也要尝试解析，
        否则会一直用错误的默认 /_doc/ 路径导致读写 404。
        """
        real = self._probe_index_doc_type(index)
        if real and real != self._doc_type:
            logger.info(
                f"[ESConfigStore] index {index} 实际 type='{real}'，"
                f"覆盖 doc_type（原='{self._doc_type}'）"
            )
            self._doc_type = real

    def _sync_doc_type_from_indices(self) -> None:
        """初始化收尾：以真实索引 mapping 中的 type 名为最终权威 doc_type。

        版本号探测（_detect_doc_type）只是猜测，真正决定读写路径的是索引里
        实际存在的 type。这里对 configs / meta 两个索引做一次权威校准，
        彻底避免「索引里是 doc、代码却用 _doc」这类读 404 问题。
        """
        for index in (INDEX_CONFIGS, INDEX_META):
            real = self._probe_index_doc_type(index)
            if real and real != self._doc_type:
                logger.info(
                    f"[ESConfigStore] 校准 doc_type：index {index} 实际 type='{real}'"
                    f"（原 doc_type='{self._doc_type}'）"
                )
                self._doc_type = real
                return

    # ── Index 初始化 ─────────────────────────────────────────────

    def _index_mapping_body(self) -> List[Tuple[str, Optional[str], Dict[str, Any]]]:
        """按 ES 版本返回候选建索引 body（7.x+/6.x/5.x）。"""
        props = {
            "province":    {"type": "keyword"},
            "intent":      {"type": "keyword"},
            "config_type": {"type": "keyword"},
            "status":      {"type": "keyword"},
            "version":     {"type": "integer"},
            "updated_at":  {"type": "date"},
            # 配置正文：仅存储、不索引其内部字段。避免各技能包 mock_response /
            # response_extract / 模板等任意 key 被动态映射，撑爆索引字段数（1000 上限）。
            "data":        {"type": "object", "enabled": False},
        }
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 1,
            # 建索引时一并设置字段上限（无需 settings/update 权限）
            "index.mapping.total_fields.limit": 100000,
        }
        candidates = [
            ("ES 7.x+", None,   {"settings": settings, "mappings": {"dynamic": True, "properties": props}}),
            ("ES 6.x",  "_doc", {"settings": settings, "mappings": {"_doc": {"dynamic": True, "properties": props}}}),
            ("ES 5.x",  "doc",  {"settings": settings, "mappings": {"doc":  {"dynamic": True, "properties": props}}}),
        ]
        # 已探测到版本时，把对应格式排到最前，减少无谓的失败请求
        if self._doc_type is not None:
            candidates.sort(key=lambda c: c[1] != self._doc_type)
        return candidates

    @staticmethod
    def _create_resp_status(resp: Any) -> str:
        """indices.create(ignore=[400]) 响应 → 'ok' / 'exists' / 具体错误串。"""
        if not isinstance(resp, dict) or "error" not in resp:
            return "ok"
        err = resp.get("error") or {}
        err_type = err.get("type", "") if isinstance(err, dict) else str(err)
        # ES 6.x+ 叫 resource_already_exists_exception，5.x 叫 index_already_exists_exception
        if "already_exists" in err_type:
            return "exists"
        return str(resp.get("error"))

    def _try_create_index(self, index: str) -> str:
        """创建单个索引（多版本格式依次尝试），返回 'ok' / 'exists' / 错误串。"""
        last_err = ""
        for mode, dt, mapping in self._index_mapping_body():
            try:
                resp = self._client.indices.create(index=index, body=mapping, ignore=[400])
            except Exception as e:
                # ES 6.x 对 v7 mapping 会 500 ClassCast 抛异常，继续尝试下一格式
                last_err = str(e)
                continue
            status = self._create_resp_status(resp)
            if status == "ok":
                logger.info(f"[ESConfigStore] index 已创建（{mode}）: {index}")
                if self._doc_type != dt:
                    self._doc_type = dt
                return "ok"
            if status == "exists":
                logger.info(f"[ESConfigStore] index 已存在: {index}")
                # 已有索引的 type 可能与探测值不同，以实际 mapping 为准
                self._resolve_existing_doc_type(index)
                return "exists"
            last_err = status
        return last_err or "unknown error"

    def _ensure_indices(self) -> None:
        """直接 create + ignore=[400]，不需要 indices.exists（避免触发 monitor 权限）。
        按服务端版本自动兼容：7.x+（无 type）/ 6.x（_doc）/ 5.x（doc，'_' 开头非法）。
        最后对 configs 索引做「字段数超限」自愈检查（抬限或迁移 -v2）。
        """
        for index in (INDEX_CONFIGS, INDEX_META):
            status = self._try_create_index(index)
            if status not in ("ok", "exists"):
                logger.error(
                    f"[ESConfigStore] index {index} 创建失败，后续读写将持续报错。"
                    f"最后错误: {status}。请检查 ES 账号建索引权限，或手动创建该 index。"
                )
        self._heal_configs_index()

    # ── 字段数超限自愈：抬限（需权限）→ 迁移 -v2（仅需建索引/读写权限）──────

    def _configs_mapping_poisoned(self, index: str) -> bool:
        """判断 configs 索引是否被动态映射污染（data 下挂了大量动态子字段）。

        历史索引 dynamic:true 时，每个技能包 data 里的任意 key 都会被映射成
        索引字段，最终撑爆 total_fields 上限（默认 1000）。新索引 data 为
        enabled:false，不会有 properties。
        """
        try:
            resp = self._client.indices.get_mapping(index=index)
            mappings = (resp.get(index) or {}).get("mappings") or {}
            # 5.x/6.x 有 type 层，取第一个 type 的内容
            for k, v in list(mappings.items()):
                if k not in ("properties", "dynamic", "_meta", "_source") and isinstance(v, dict):
                    mappings = v
                    break
            data_m = (mappings.get("properties") or {}).get("data") or {}
            if data_m.get("enabled") is False:
                return False
            return bool(data_m.get("properties"))
        except Exception as e:
            logger.debug(f"[ESConfigStore] 读取 {index} mapping 失败（跳过自愈检查）: {e}")
            return False

    def _raise_field_limit(self, index: str, limit: int = 100000) -> bool:
        """抬高单索引的 mapping.total_fields.limit，返回是否成功。

        需要 indices:admin/settings/update 权限；生产 developer 账号无此权限时
        返回 False，由调用方转入索引迁移路径。
        """
        try:
            resp = self._client.indices.put_settings(
                index=index,
                body={"index": {"mapping": {"total_fields": {"limit": limit}}}},
                ignore=[400, 404],
            )
            ok = isinstance(resp, dict) and bool(resp.get("acknowledged"))
            if ok:
                logger.info(f"[ESConfigStore] 已设置 {index} total_fields.limit={limit}")
            else:
                logger.warning(f"[ESConfigStore] 设置 {index} 字段上限未生效: {resp}")
            return ok
        except Exception as e:
            logger.warning(f"[ESConfigStore] 设置 {index} 字段上限失败（不阻断）: {e}")
            return False

    def _copy_all_docs(self, src: str, dst: str) -> int:
        """把 src 索引全部文档按原 _id 复制到 dst（幂等，覆盖写）。"""
        try:
            resp = self._client.search(
                index=src, body={"query": {"match_all": {}}, "size": 5000},
            )
            hits = (resp.get("hits") or {}).get("hits") or []
        except Exception as e:
            logger.warning(f"[ESConfigStore] 读取 {src} 文档失败，跳过迁移复制: {e}")
            return 0
        n = 0
        for h in hits:
            try:
                self._client.index(
                    index=dst, id=h["_id"], body=h["_source"], **self._dt_kwargs(),
                )
                n += 1
            except Exception as e:
                logger.warning(f"[ESConfigStore] 迁移文档 {h.get('_id')} 失败: {e}")
        logger.info(f"[ESConfigStore] 索引迁移复制完成: {src} → {dst}（{n}/{len(hits)} 条）")
        return n

    def _heal_configs_index(self) -> None:
        """configs 索引字段数超限自愈（幂等）：

        旧索引 dynamic:true 导致 data 内任意字段被动态映射，超过 1000 上限后所有
        发布写入失败。修复顺序：
          1) 尝试 put_settings 抬高上限（需要 settings/update 权限，生产 developer
             账号通常没有 → 403）；
          2) 抬限失败则迁移：创建 {INDEX_CONFIGS}-v2（data 不索引 + 高上限，建索引
             时随 body 带 settings，无需额外权限），把旧索引文档全量复制过去，
             然后本进程切换到 -v2 读写。再次启动时 -v2 已存在，直接切换（不重复
             复制，避免旧数据覆盖新版本）。
        """
        global INDEX_CONFIGS
        if INDEX_CONFIGS.endswith("-v2"):
            return
        if not self._configs_mapping_poisoned(INDEX_CONFIGS):
            return
        logger.warning(
            f"[ESConfigStore] 检测到 {INDEX_CONFIGS} 存在动态映射污染（data 子字段被索引），"
            f"尝试自愈：先抬高字段上限，失败则迁移到 -v2 索引"
        )
        if self._raise_field_limit(INDEX_CONFIGS):
            return

        new_index = f"{INDEX_CONFIGS}-v2"
        status = self._try_create_index(new_index)
        if status == "ok":
            self._copy_all_docs(INDEX_CONFIGS, new_index)
        elif status != "exists":
            logger.error(
                f"[ESConfigStore] 迁移失败：无法创建 {new_index}（{status}）。"
                f"请联系 ES 管理员执行：PUT {INDEX_CONFIGS}/_settings "
                f'{{"index":{{"mapping":{{"total_fields":{{"limit":100000}}}}}}}}'
            )
            return
        INDEX_CONFIGS = new_index
        logger.warning(
            f"[ESConfigStore] 已切换配置索引到 {new_index}（原索引保留不动）。"
            f"建议后续在 config.json 的 elasticsearch.index_configs 固化为该名称。"
        )

    def _index_doc_with_retry(self, doc_id: str, body: Dict[str, Any]) -> None:
        """写入文档；若因 total_fields 上限失败，则触发自愈（抬限/迁移 -v2）后重试。"""
        try:
            self._client.index(
                index=INDEX_CONFIGS, id=doc_id, body=body, **self._dt_kwargs(),
            )
            return
        except Exception as e:
            msg = str(e)
            if "total fields" not in msg and "illegal_argument_exception" not in msg:
                raise
            logger.warning(
                f"[ESConfigStore] 写入命中字段数上限，触发自愈（抬限/迁移）后重试: {msg}"
            )
            self._heal_configs_index()
            # 重试一次（自愈成功时 INDEX_CONFIGS 可能已切到 -v2）；仍失败则抛出
            self._client.index(
                index=INDEX_CONFIGS, id=doc_id, body=body, **self._dt_kwargs(),
            )

    # ── 保存并发布 ────────────────────────────────────────────────

    def save_and_publish(
        self,
        province: str,
        intent: str,
        config_type: str,
        data: Dict[str, Any],
        operator: str = "system",
        comment: str = "",
        notify: bool = True,
    ) -> Tuple[bool, str, int]:
        """写入新版本并发布，旧 published → archived，通知 Redis 热重载。
        notify=False 时跳过内部 publish_change（调用方自行统一广播，避免重复/风暴）。
        返回 (success, message, new_version)
        """
        if not self.enabled:
            return False, "ES 不可用", 0
        try:
            meta    = self._get_meta(province, intent, config_type)
            old_v   = meta.get("published_version")
            new_v   = (old_v or 0) + 1

            # 旧版本归档
            if old_v:
                try:
                    self._client.update(
                        index=INDEX_CONFIGS,
                        id=f"{province}:{intent}:{config_type}:{old_v}",
                        body={"doc": {"status": "archived"}},
                        ignore=[404],
                        **self._dt_kwargs(),
                    )
                except Exception:
                    pass

            # 写新版本（字段数超限时自动抬高上限并重试一次，自愈历史动态映射问题）
            self._index_doc_with_retry(
                doc_id=f"{province}:{intent}:{config_type}:{new_v}",
                body={
                    "province": province, "intent": intent,
                    "config_type": config_type, "version": new_v,
                    "status": "published", "data": data,
                    "published_at": _now_iso(), "published_by": operator,
                    "comment": comment, "updated_at": _now_iso(),
                },
            )

            # 更新 meta，超限清理最旧归档
            versions: List[int] = list(meta.get("versions", []))
            if old_v and old_v not in versions:
                versions.append(old_v)
            while len(versions) > MAX_ARCHIVED:
                oldest = versions.pop(0)
                try:
                    self._client.delete(
                        index=INDEX_CONFIGS,
                        id=f"{province}:{intent}:{config_type}:{oldest}",
                        ignore=[404],
                        **self._dt_kwargs(),
                    )
                except Exception:
                    pass

            self._save_meta(province, intent, config_type,
                            {"published_version": new_v, "versions": versions})

            logger.info(f"[ESConfigStore] 已发布 {province}/{intent}/{config_type} v{new_v}")

            # Redis 通知其他实例热重载（notify=False 时由调用方统一广播）
            if notify:
                try:
                    from services.redis_config_bus import redis_config_bus
                    if redis_config_bus.enabled:
                        redis_config_bus.publish_change(province, intent)
                except Exception as re:
                    logger.warning(f"[ESConfigStore] Redis 通知失败（不影响发布）: {re}")

            return True, f"发布成功，版本 v{new_v}", new_v
        except Exception as e:
            logger.error(f"[ESConfigStore] save_and_publish 失败: {e}")
            return False, f"发布失败: {e}", 0

    # ── 删除技能包全部配置 ─────────────────────────────────────────
    def delete_skill_configs(
        self,
        province: str,
        intent: str,
        config_types: Tuple[str, ...] = ("api_nodes", "biz_config", "skill_meta", "test_cases"),
    ) -> Tuple[bool, int, str]:
        """删除某技能包在 ES 中的全部配置文档（各 config_type 的所有版本 + meta 指针）。

        供「删除 Skill」使用：生产读取以 ES 为真源，只删本地目录不删 ES 的话，
        下次 reload/重启后技能包会从 ES 复活。返回 (ok, deleted_docs, message)。
        """
        if not self.enabled:
            return False, 0, "ES 不可用"
        deleted = 0
        try:
            # 1) 按 meta 指针删除各版本文档 + meta 指针本身
            for ct in config_types:
                meta = self._get_meta(province, intent, ct)
                version_ids = set(meta.get("versions") or [])
                if meta.get("published_version"):
                    version_ids.add(meta["published_version"])
                for v in version_ids:
                    try:
                        resp = self._client.delete(
                            index=INDEX_CONFIGS,
                            id=f"{province}:{intent}:{ct}:{v}",
                            ignore=[404],
                            **self._dt_kwargs(),
                        )
                        if resp.get("result") == "deleted":
                            deleted += 1
                    except Exception:
                        pass
                try:
                    self._client.delete(
                        index=INDEX_META,
                        id=f"{province}:{intent}:{ct}",
                        ignore=[404],
                        **self._dt_kwargs(),
                    )
                except Exception:
                    pass

            # 2) 兜底：match_all 扫描 configs 索引，清掉 id 格式漂移的残留文档
            #    （与 _search_published_data 相同策略，不对 mapping 做假设）
            try:
                resp = self._client.search(
                    index=INDEX_CONFIGS,
                    body={"query": {"match_all": {}}, "size": 2000},
                )
                for h in resp.get("hits", {}).get("hits", []):
                    src = h.get("_source", {})
                    if (src.get("province") == province
                            and src.get("intent") == intent
                            and src.get("config_type") in config_types):
                        try:
                            r = self._client.delete(
                                index=INDEX_CONFIGS, id=h["_id"],
                                ignore=[404], **self._dt_kwargs(),
                            )
                            if r.get("result") == "deleted":
                                deleted += 1
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"[ESConfigStore] 删除残留文档扫描失败（已删 {deleted} 个）: {e}")

            logger.info(
                f"[ESConfigStore] 已删除技能包 ES 配置 {province}/{intent}，共 {deleted} 个文档"
            )
            return True, deleted, f"已删除 {deleted} 个 ES 配置文档"
        except Exception as e:
            logger.error(f"[ESConfigStore] delete_skill_configs 失败 {province}/{intent}: {e}")
            return False, deleted, f"删除 ES 配置失败: {e}"

    # ── 回滚 ─────────────────────────────────────────────────────

    def rollback(
        self,
        province: str,
        intent: str,
        config_type: str,
        target_version: int,
        operator: str = "system",
    ) -> Tuple[bool, str]:
        """回滚到指定历史版本：目标版本重新 published，当前 published 归档。
        返回 (ok, msg)。所有 ES 异常捕获后返回 (False, str(exc))。
        """
        if not self.enabled:
            return False, "ES 未启用"
        try:
            meta  = self._get_meta(province, intent, config_type)
            pub_v = meta.get("published_version")
            if not pub_v:
                return False, f"配置 {province}/{intent}/{config_type} 不存在，无法回滚"
            if target_version == pub_v:
                return False, "已是当前版本"

            versions: List[int] = list(meta.get("versions", []))
            if target_version not in versions:
                return False, "目标版本不存在或已被清理"

            # 取目标版本文档
            target_id = f"{province}:{intent}:{config_type}:{target_version}"
            resp = self._client.get(index=INDEX_CONFIGS, id=target_id, ignore=[404], **self._dt_kwargs())
            if not resp.get("found"):
                return False, f"目标版本 v{target_version} 文档不存在"

            # 当前 published 文档归档
            try:
                self._client.update(
                    index=INDEX_CONFIGS,
                    id=f"{province}:{intent}:{config_type}:{pub_v}",
                    body={"doc": {"status": "archived"}},
                    ignore=[404],
                    **self._dt_kwargs(),
                )
            except Exception:
                pass

            # 目标文档重新发布，comment 追加回滚来源
            old_comment = resp["_source"].get("comment", "") or ""
            new_comment = (
                f"{old_comment}; rollback from v{pub_v}" if old_comment
                else f"rollback from v{pub_v}"
            )
            self._client.update(
                index=INDEX_CONFIGS,
                id=target_id,
                body={"doc": {
                    "status":       "published",
                    "published_at": _now_iso(),
                    "published_by": operator,
                    "comment":      new_comment,
                    "updated_at":   _now_iso(),
                }},
                **self._dt_kwargs(),
            )

            # meta：目标版本移出归档列表，旧 published 追加进去（保持 FIFO 截断）
            versions = [v for v in versions if v != target_version] + [pub_v]
            while len(versions) > MAX_ARCHIVED:
                oldest = versions.pop(0)
                try:
                    self._client.delete(
                        index=INDEX_CONFIGS,
                        id=f"{province}:{intent}:{config_type}:{oldest}",
                        ignore=[404],
                        **self._dt_kwargs(),
                    )
                except Exception:
                    pass

            self._save_meta(province, intent, config_type,
                            {"published_version": target_version, "versions": versions})

            logger.info(
                f"[ESConfigStore] 已回滚 {province}/{intent}/{config_type} "
                f"v{pub_v} -> v{target_version}"
            )
            return True, f"已回滚到版本 {target_version}"
        except Exception as e:
            logger.error(f"[ESConfigStore] rollback 失败: {e}")
            return False, str(e)

    def get_current_version_info(
        self, province: str, intent: str, config_type: str
    ) -> Optional[Dict[str, Any]]:
        """获取当前版本指针信息。未启用返回 None。"""
        if not self.enabled:
            return None
        try:
            meta = self._get_meta(province, intent, config_type)
            return {
                "published_version": meta.get("published_version"),
                "archived_versions": list(meta.get("versions", [])),
                "updated_at":        meta.get("updated_at"),
            }
        except Exception as e:
            logger.error(f"[ESConfigStore] get_current_version_info 失败: {e}")
            return None

    # ── 读取 ─────────────────────────────────────────────────────

    def get_all_published_versions(self) -> Dict[str, int]:
        """批量获取全部技能包配置的当前发布版本号（供定时轮询低成本检测变更用）。

        只搜一次 meta index，不逐个 get，量级可控（技能包数 << 5000）。
        返回 {"{province}:{intent}:{config_type}": published_version}，
        跳过尚无 published_version 的条目。未启用或异常返回 {}（调用方据此判定本轮
        不可用，不应把 {} 当作"全部已删除"处理）。
        """
        if not self.enabled:
            return {}
        try:
            resp = self._client.search(
                index=INDEX_META,
                body={
                    "query": {"exists": {"field": "published_version"}},
                    "size": 5000,
                    "_source": ["published_version"],
                },
            )
            result: Dict[str, int] = {}
            for hit in resp["hits"]["hits"]:
                pv = hit["_source"].get("published_version")
                if pv:
                    result[hit["_id"]] = pv
            return result
        except Exception as e:
            es = str(e)
            # index 不存在 / 字段无 mapping（空索引）均视为「暂无已发布配置」，一次性降噪
            if ("index_not_found" in es or "No mapping found" in es
                    or "search_phase_execution" in es):
                if not getattr(self, "_meta_missing_warned", False):
                    self._meta_missing_warned = True
                    logger.warning(
                        f"[ESConfigStore] meta index '{INDEX_META}' 无有效已发布数据"
                        f"（{es[:120]}）。视为暂无已发布配置——首次经 SkillManager 发布后写入。"
                    )
                return {}
            logger.error(f"[ESConfigStore] get_all_published_versions 失败: {e}")
            return {}

    def get_published(self, province: str, intent: str, config_type: str) -> Optional[Dict[str, Any]]:
        """获取当前已发布的配置数据。

        优先走 meta 指针 + get by id（最快）；若 meta 无指针或按 id 取不到
        （meta 缺失 / 历史 id 格式不一致），回退到按字段 _search 直接查 configs
        索引里 status=published 的最新版本，保证「ES 里有数据就一定能读到」。
        """
        if not self.enabled:
            return None
        try:
            meta  = self._get_meta(province, intent, config_type)
            pub_v = meta.get("published_version")
            if pub_v:
                resp = self._client.get(
                    index=INDEX_CONFIGS,
                    id=f"{province}:{intent}:{config_type}:{pub_v}",
                    ignore=[404],
                    **self._dt_kwargs(),
                )
                if resp.get("found"):
                    return resp["_source"]["data"]
                logger.warning(
                    f"[ESConfigStore] meta 指向 v{pub_v} 但按 id 未取到 "
                    f"{province}/{intent}/{config_type}，改用字段搜索兜底"
                )
            # 兜底：直接搜 configs 索引（不依赖 meta / id 格式）
            return self._search_published_data(province, intent, config_type)
        except Exception as e:
            logger.error(f"[ESConfigStore] get_published 失败: {e}")
            return None

    def _search_published_data(
        self, province: str, intent: str, config_type: str
    ) -> Optional[Dict[str, Any]]:
        """搜索 configs 索引中匹配的 published 最新版本，返回其 data。

        为兼容「字段被动态映射为 text（中文 term 不命中）」「version 字段无 mapping
        无法 ES 排序」等历史索引问题，这里用 match_all 拉回全部文档后在 Python 端
        按 _source 过滤 + 取最大 version，不对 ES mapping 做任何假设。
        """
        try:
            resp = self._client.search(
                index=INDEX_CONFIGS,
                body={"query": {"match_all": {}}, "size": 2000},
            )
            hits = resp.get("hits", {}).get("hits", [])
            best = None
            best_v = -1
            for h in hits:
                src = h.get("_source", {})
                if (src.get("province") == province
                        and src.get("intent") == intent
                        and src.get("config_type") == config_type
                        and src.get("status", "published") == "published"):
                    v = src.get("version") or 0
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        v = 0
                    if v >= best_v:
                        best_v, best = v, src
            if best is not None:
                logger.info(
                    f"[ESConfigStore] 字段搜索命中 {province}/{intent}/{config_type} "
                    f"v{best.get('version')}（Python 端过滤，未走 meta 指针）"
                )
                return best.get("data")
            return None
        except Exception as e:
            if "index_not_found" in str(e):
                return None
            logger.error(f"[ESConfigStore] _search_published_data 失败: {e}")
            return None

    def get_versions(self, province: str, intent: str, config_type: str) -> List[Dict[str, Any]]:
        """获取历史版本列表（published + archived）"""
        if not self.enabled:
            return []
        try:
            meta   = self._get_meta(province, intent, config_type)
            pub_v  = meta.get("published_version")
            all_vs = sorted(
                ([pub_v] if pub_v else []) + list(meta.get("versions", [])),
                reverse=True,
            )
            result = []
            for v in all_vs:
                resp = self._client.get(
                    index=INDEX_CONFIGS,
                    id=f"{province}:{intent}:{config_type}:{v}",
                    ignore=[404],
                    **self._dt_kwargs(),
                )
                if resp.get("found"):
                    src = resp["_source"]
                    result.append({
                        "version":      src.get("version"),
                        "status":       src.get("status"),
                        "published_at": src.get("published_at"),
                        "published_by": src.get("published_by"),
                        "comment":      src.get("comment", ""),
                        "is_current":   v == pub_v,
                    })
            return result
        except Exception as e:
            logger.error(f"[ESConfigStore] get_versions 失败: {e}")
            return []

    def load_all_published(self) -> Dict[str, Dict[str, Any]]:
        """批量加载所有省份/意图的 published 配置。
        返回：{"{province}:{intent}": {"biz_config": {...}, "api_nodes": {...}}}
        """
        if not self.enabled:
            return {}
        try:
            # match_all + Python 端过滤，避免历史索引字段 mapping 不一致导致 term 不命中
            # size 需覆盖「技能包数 × 配置类型数 × (1 + 归档版本数)」，5000 留足余量
            resp = self._client.search(
                index=INDEX_CONFIGS,
                body={"query": {"match_all": {}}, "size": 5000},
            )
            # 先按 (key, config_type) 收敛出最大 version 的 published 文档
            latest: Dict[tuple, tuple] = {}  # (key, ct) -> (version, data)
            for hit in resp.get("hits", {}).get("hits", []):
                src = hit.get("_source", {})
                if src.get("status", "published") != "published":
                    continue
                prov, intent, ct = src.get("province"), src.get("intent"), src.get("config_type")
                if not (prov and intent and ct):
                    continue
                try:
                    v = int(src.get("version") or 0)
                except (TypeError, ValueError):
                    v = 0
                k = (f"{prov}:{intent}", ct)
                if k not in latest or v >= latest[k][0]:
                    latest[k] = (v, src.get("data", {}))
            result: Dict[str, Dict[str, Any]] = {}
            for (key, ct), (_v, data) in latest.items():
                result.setdefault(key, {})[ct] = data
            logger.info(f"[ESConfigStore] 批量加载 {len(result)} 个技能包（match_all 过滤）")
            return result
        except Exception as e:
            if "index_not_found" in str(e):
                return {}
            logger.error(f"[ESConfigStore] load_all_published 失败: {e}")
            return {}

    # ── Meta ─────────────────────────────────────────────────────

    def _get_meta(self, province: str, intent: str, config_type: str) -> Dict[str, Any]:
        try:
            resp = self._client.get(
                index=INDEX_META,
                id=f"{province}:{intent}:{config_type}",
                ignore=[404],
                **self._dt_kwargs(),
            )
            if resp.get("found"):
                return dict(resp["_source"])
        except Exception:
            pass
        return {"published_version": None, "versions": []}

    def _save_meta(self, province: str, intent: str, config_type: str, meta: Dict[str, Any]) -> None:
        meta["updated_at"] = _now_iso()
        self._client.index(
            index=INDEX_META,
            id=f"{province}:{intent}:{config_type}",
            body=meta,
            **self._dt_kwargs(),
        )


# 全局单例
es_config_store = ESConfigStore()
