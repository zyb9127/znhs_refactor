# 北京 · 套餐推荐 Skill

**约定：一个业务意图对应一个 Skill 包。** 本目录 `skills-runtime/beijing/套餐推荐/` 即「省份 = 北京 + 意图 = 套餐推荐」的完整技能包；对外 **`intent`** 与目录名一致。若新增意图（如套餐升档），应新建同级目录并自带 `_meta.json`、`config/`、`scripts/main_flow.py` 与 **本 SKILL.md**。

与主工程 **LangGraph 三智能体**语义对齐，便于 Skill 与编排切换、联调。

---

## 入口与 HTTP

| 项目 | 说明 |
|------|------|
| 本 Skill 根目录 | `beijing/套餐推荐/`（本文档与 `_meta.json` 同级） |
| 入口函数 | `scripts/main_flow.py` → `run_scenario_flow(context, request_data)` |
| HTTP | `/marketing/recommend` 默认 `workflow=skill`，`intent=套餐推荐` 且省份为北京时加载本包 |

---

## 执行流（与 LangGraph 同源三节点）

运行时 **不**在 Skill 内用 `mock_data` 拼用户态；用户数据与主站一致：

1. **用户融合** — `UserProfileAgent(beijing)`  
   北京单接口经 `BeijingAdapter` / 意图流 `query_package`，产出 `user_package_info`、`recommend_results`、`package_api_raw_data`（bean）等。

2. **推荐策略** — `RecommendationAgent(beijing)`  
   有上游候选列表时走 **`direct`**：按 `rank` 取 **`top_n`**（与 `biz_config.strategy`、请求 `topN` 一致），**不**跑推荐侧 LLM。

3. **话术生成** — `ScriptGeneratorAgent(beijing)`  
   **`llm_service`** 按条生成；`biz_config.prompts.<template_id>.user_prompt_template` 若存在，**优先**作为 LLM 用户模板（覆盖仅读 `prompt_config.json` 的路径）。

智能体实例来自 **`LangGraphMarketingOrchestrator.get_agent_bundle("beijing")`**，与主流程 **共用单例**，避免每次请求重复初始化。

---

## 配置说明

### `config/biz_config.json`

- **`strategy`**：`default_strategy`（北京推荐为 `direct`）、`top_n`、`intent_strategies` 按意图覆盖。
- **`script_templates`**：按意图键（如 `套餐推荐`）配置 `template_id`、`template_content`（业务文案参考）、`required_plugins`（如 `package_diff`）。
- **`prompts`**：键与 `template_id` 对齐（如 `package_recommendation`），**`user_prompt_template`** 为发给大模型的指令模板，占位符含 `cur_brief`、`pkg_brief`、`diff_str`、`usage_line` 等。

### `config/api_nodes.json`

- 描述北京 **`user_package_api`** 及 **`bean_field_node_mapping`**（接口字段 → 节点 + `transform`），供 **管理端 / 契约** 与本地工具函数对齐语义。
- **运行时用户数据以 UserProfileAgent 为准**；映射逻辑仍可用于文档、Mock、离线调试（如 `build_nodes_from_mapping`）。

---

## 返回与对外约定（北京 + `/marketing/recommend`）

- **`data.recommend_results`**：由 **`marketing_scripts`** 按请求 **`topN`** 截断组装，条数与排名与推荐一致。
- **`other_info`**（北京）：**仅**透传 **`package_api_raw_data`**（单接口 **bean**），不再混入 `workflow` 等额外字段。
- 成功 **`data`** 中还包含 `generated_script`（首条主话术）、`diff_calculated`（若模板要求 `package_diff`）、`package_api_raw_data` 等，供联调。

---

## 目录结构（本 Skill）

```text
套餐推荐/                    # 意图目录 = 一个 Skill
├── SKILL.md                 # 本文档（与 _meta.json 同级）
├── _meta.json               # 场景元数据（加载必需）
├── config/
│   ├── api_nodes.json
│   └── biz_config.json
└── scripts/
    └── main_flow.py
```

省份级 `beijing/manifest.json` 仅为省份元数据，**不**替代本意图 Skill 包。

---

## 管理 API

配置经 **`GET/POST /api/v3/skills/beijing/套餐推荐/config/{api_nodes|biz_config}`**（`intent` 路径段需 URL 编码时与目录名一致）读写；保存后内存中的 `SkillExecutionContext` 会更新，**无需**为改配置重建 Skill 执行器（与 Registry 缓存策略一致）。

---

## 修改本 Skill 时建议

- 改话术优先级：先改 **`biz_config.prompts.*.user_prompt_template`**，再考虑全局 `config/prompt_config.json`。
- 改推荐条数：`**strategy.top_n**`、请求 **`topN`**、以及接口返回的 **`recommend_results`** 数量需一并核对。
