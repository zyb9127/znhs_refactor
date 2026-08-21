# 营销助手统一接口（直传模式）· 本地测试

用于本地验证「直传营销助手模式」的完整效果：省份/活动路由 → 营销标志过滤 → 逐产品并发生成
推荐/切入/挽留话术 → 归并成一条产品话术回调网关。**标准接口模式不受影响。**

## 一、测试配置（skills-runtime，海南省 provinceCode=898）

| 技能包（省份/意图） | 直传节点 | 话术模板环节 | 演示点 |
|---|---|---|---|
| `hainan/套餐升级活动` | `request_variant=marketing_assistant` | 推荐 + 切入 + 挽留 | 一个产品三行模板 → `words` + `aiPitchMarketingDesc` + `aiRetentionMarketingDesc` 归并成一条 |
| `hainan/流量包活动` | `request_variant=marketing_assistant` | 仅推荐 | 只回 `words`，两个 `ai*` 字段为空 |

- 意图名 = 活动名称（`activityTypeName`），报文里的产品按活动名称精确路由到同名技能包；
- 模板按 `productId → business_type → productName` 匹配（本例用 `business_type`：套餐升级 / 流量包）；
- `stage`（推荐环节/切入环节/挽留环节）命中 `config.json → cross_sell.recommend_stage / pitch_stage / retention_stage` 候选。

测试报文：[`preload_payload.json`](./preload_payload.json)（3 个产品：套餐升级·可营销、套餐升级·被营销标志挡掉、流量包·可营销）。

## 二、跑法一：离线脚本（推荐，直接打印回调 value，不依赖外部网关）

在 `znhs_refactor` 目录下，用与服务相同的 Python 环境执行：

```bash
python3 "docs/交叉营销接口/本地测试/run_preload_local.py"
# 也可指定自定义报文： python3 "docs/交叉营销接口/本地测试/run_preload_local.py" 你的报文.json
```

脚本把回调网关 `push_cache` 替换为本地捕获，跑完整条链路后打印最终回调 `value` 与逐产品话术概览。

## 三、跑法二：真实入口（灰度/生产服务在跑时）

营销助手真实入口是 **异步** 的：`POST /znhs/marketing/preload` 只回 ack，话术生成后回调交叉营销网关缓存。

```bash
curl -X POST http://127.0.0.1:8000/znhs/marketing/preload \
  -H "Content-Type: application/json" \
  --data-binary @"docs/交叉营销接口/本地测试/preload_payload.json"
# 立即返回 {"rtnMsg":"数据接收成功！","rtnCode":"0"}
```

生成的话术在哪看：
- 服务日志：`[preload] 话术生成完成 ... 回调项=N`；
- 分省日志：`logs/provinces/hainan/response_*.jsonl`（含逐条 `recommend_results`）；
- 完整回调 value：本地没有交叉营销网关时回调会失败（只告警不影响生成）。要抓完整回调体，可把
  `ZNHS_CROSS_SELL_CALLBACK_BASE` 指向一个本地回显服务，或直接用「跑法一」。

## 四、预期结果

```jsonc
"result": [
  { "productId": "P20260810001", "activityType": "ACT_UPGRADE",
    "words": "<推荐环节话术>", "aiPitchMarketingDesc": "<切入环节话术>", "aiRetentionMarketingDesc": "<挽留环节话术>" },
  { "productId": "P20260810003", "activityType": "ACT_FLOW",
    "words": "<推荐环节话术>", "aiPitchMarketingDesc": "", "aiRetentionMarketingDesc": "" }
]
```

- `P20260810002`（尊享套餐，`marketingProductFlag=0`）被营销标志挡掉，不出现在结果里；
- 3 个产品、可营销 2 个、生成 4 条话术（P1 三条 + P3 一条）、回调 2 项。

## 五、注意事项

- **新增技能包需让服务重新加载**：重启服务，或调用注册表热重载（`skill_registry.reload()`）。离线脚本会自行 `initialize()`。
- **真实话术需要 LLM 可达**：话术由大模型生成。LLM 不可达（如本机无外网返回 403）时走降级话术，
  此时推荐/切入/挽留可能是同一句兜底文案——链路与回调结构仍正确，换到能连 LLM 的环境即为差异化话术。
- 省份编码 `898`→`hainan` 来自 `config/province_mapping.json`；换省份改报文 `provinceCode` 即可。
