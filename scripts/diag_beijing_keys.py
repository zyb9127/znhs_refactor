"""只读诊断：比对线上 ES 里「接口出参键名 / field_transform 配置键名 / 话术模板占位符」三方是否对得上。

不写任何数据。用法：python scripts/diag_beijing_keys.py [province]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.data_step import DataStep
from utils.config_loader import config_loader
from utils.field_naming import canon_key

_SUB_RE = re.compile(r"\{(\w+)((?:\[[^\[\]]+\])+)\}")


def _iter_sub_placeholders(content):
    for root, subs in _SUB_RE.findall(content or ""):
        keys = re.findall(r"\[([^\[\]]+)\]", subs)
        if keys:
            yield root, keys


def main(province="beijing"):
    from services.es_config_store import es_config_store as store

    store.init(config_loader.get("elasticsearch", {}) or {})
    if not store.enabled:
        print("ES 未启用")
        return

    published = store.load_all_published()
    intents = sorted({
        key.split("/")[1]
        for key in published
        if key.startswith(f"{province}/")
    })
    print(f"线上 {province} 共 {len(intents)} 个技能: {intents}\n")

    for intent in intents:
        api_nodes = (published.get(f"{province}/{intent}/api_nodes") or {})
        biz = (published.get(f"{province}/{intent}/biz_config") or {})
        if not api_nodes:
            continue
        print("=" * 100)
        print(f"【{province}/{intent}】")

        produced = {}
        for node_name, node in api_nodes.items():
            if str(node_name).startswith("_") or not isinstance(node, dict):
                continue
            mock = node.get("mock_response") or {}
            if not mock:
                print(f"  [{node_name}] 无样例出参，跳过模拟")
                continue
            out = DataStep.__new__(DataStep)._transform_fields({}, node, mock)
            usage = out.get("usage") or {}
            for dom, val in usage.items():
                if isinstance(val, dict):
                    produced.setdefault(f"usage.{dom}", set()).update(val.keys())
            print(f"  [{node_name}] 样例产出:")
            for dom in sorted(usage):
                got = usage[dom]
                print(f"      usage.{dom}: {list(got) if isinstance(got, dict) else got}")
            print(f"      tags: {list((out.get('tags') or {}).keys())}")

        tpls = biz.get("script_templates_v2") or biz.get("script_templates") or []
        refs = {}
        for t in tpls:
            for root, keys in _iter_sub_placeholders(t.get("template_content")):
                refs.setdefault(f"{root}.{keys[0]}" if root == "usage" else root, set()).add(
                    keys[-1] if root == "usage" else keys[0]
                )
        if not refs:
            print("  模板未使用子字段占位符")
            continue

        print("  ── 模板子字段占位符 vs 样例产出键名 ──")
        for dom in sorted(refs):
            have = produced.get(dom, set())
            canon_have = {canon_key(k): k for k in have}
            for sub in sorted(refs[dom]):
                if sub in have:
                    print(f"      ✅ {dom}[{sub}]")
                elif canon_key(sub) in canon_have:
                    print(f"      ✅ {dom}[{sub}]（按归一命中 {canon_have[canon_key(sub)]}）")
                else:
                    near = [k for k in have if canon_key(sub)[:5] and canon_key(sub)[:5] in canon_key(k)]
                    hint = f"，最接近: {near}" if near else f"，该域产出: {sorted(have) or '空'}"
                    print(f"      ❌ {dom}[{sub}] 取不到{hint}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "beijing")
