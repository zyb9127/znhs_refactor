# ES 与 Redis：配置保存、加载和同步的实际顺序

本文只讨论**技能包配置**：`api_nodes`、`biz_config`、`skill_meta`，以及相关的 `test_cases`。

> 先给结论：在灰度/生产环境中，**ES 是唯一真源**；Redis 只是 ES 配置的热缓存和变更通知通道。Redis 丢数据或不可用不会让配置丢失；ES 写失败时，普通技能配置发布会失败，不能用本地文件或 Redis 替代发布成功。

## 1. 各存储中的数据是什么

| 位置 | 写入的数据 | 是否可作为生产真源 | 主要目的 |
|---|---|---:|---|
| ES `znhs-agent-skill-configs` | 每个配置类型的完整版本数据 | 是 | 持久化、版本记录、回滚 |
| ES `znhs-agent-skill-config-meta` | `published_version` 和历史版本列表 | 是 | 指向当前生效版本 |
| Redis | 当前发布版本的一份 JSON 缓存 | 否 | 快速读取，减轻 ES 压力 |
| Redis Pub/Sub | `{province}:{intent}` 变更消息 | 否 | 通知其他服务实例 reload |
| `skills-runtime/.../config/*.json` | 当前配置的本地快照 | 否（生产）/ 是（开发） | 开发环境唯一配置；生产降级读取备用 |

ES 配置的文档 ID 为：

```text
{province}:{intent}:{config_type}:{version}
```

例如一次 `shandong / 套餐升档 / biz_config` 的发布，会产生一个新的版本文档；旧的已发布版本会被标记为 `archived`，meta 文档改为指向新版本。

## 2. 保存/发布普通技能配置时的写入顺序

入口是 `services.skill_publisher.publish_config()`；管理后台保存 `api_nodes`、`biz_config` 等普通技能配置都会走这里。

```text
① ES 写新版本并发布       ← 必须成功，失败立即结束
② 本地 JSON 写快照        ← 失败只记 warning
③ Redis 写该配置的缓存    ← 失败只记 warning
④ Redis Pub/Sub 广播变更  ← 失败只记 warning
⑤ 当前进程 reload 内存配置
```

更细的行为如下：

| 顺序 | 对 ES 的操作 | 对 Redis 的操作 | 失败后结果 |
|---:|---|---|---|
| 1 | 读取当前 `published_version`；旧版标记 `archived`；写入新版本；meta 指针切到新版本 | 无 | **发布失败**；不写本地、不写 Redis、不 reload |
| 2 | 已成功 | 无 | 本地快照缺失，但 ES 新版本仍是生效配置 |
| 3 | 已成功 | `SETEX cfg:{province}:{intent}:{config_type}` 写入新 JSON | ES 发布仍成功；下次加载会从 ES 补缓存 |
| 4 | 已成功 | `PUBLISH config:changed {province}:{intent}` | ES 发布仍成功；其他实例最迟由 ES 轮询发现变更 |
| 5 | 无 | 无 | 当前实例 reload 异常只影响当前进程，ES 已发布版本不回滚 |

所以，以下理解是正确的：

- **ES 写失败 = 这次普通配置保存失败。** 即使 Redis 可写、本地文件可写，代码也不会继续写它们，避免出现“页面说成功、生产真源没变”的半发布状态。
- **Redis 写/广播失败 ≠ 保存失败。** 因为 ES 已经保存了真源；只是缓存与跨实例生效速度下降。
- 发布时 Redis 先更新缓存、再广播，订阅到消息的其他实例 reload 后能优先拿到新缓存。

## 3. 服务启动时的加载顺序

### 3.1 development 环境

```text
不初始化 ES
不初始化 Redis
直接读取 skills-runtime 下的本地 JSON
```

本地文件是开发环境的唯一来源。

### 3.2 gray / production 环境：完整启动加载

启动时先初始化 ES，再初始化 Redis，然后调用 `skill_registry.initialize()`。

对“加载全部技能包”这一启动场景，代码会优先一次性从 ES 批量扫描全部已发布配置：

```text
① 扫描本地目录，得到本地已有的省份/意图
② ES `load_all_published()` 一次批量读取所有 published 配置
③ 将 ES 中有、但本地目录没有的技能包也加入加载列表
④ 每个技能包：优先采用该批 ES 结果
⑤ 某个配置类型在 ES 中缺失时，才读取对应本地 JSON 补齐
```

因此：**完整启动时，ES 批量结果优先，Redis 不是这一条批量加载路径的第一读源。**这样可一次发现“仅存在 ES、容器本地没有目录”的技能包，避免逐个读取 ES。

启动时某个技能包的最终字段级补齐逻辑是：

```text
api_nodes：ES 批量结果有 → 用 ES；没有 → 用本地文件
biz_config：ES 批量结果有 → 用 ES；没有 → 用本地文件
```

## 4. 单个技能包 reload 时的读取顺序

配置发布后、收到 Pub/Sub 后、或者管理接口手动 reload 时，通常是单个 `province + intent` 的 reload。

这条路径才是严格的“Redis → ES → 本地”顺序，而且 `api_nodes` 和 `biz_config` **逐类型独立处理**：

```text
读取 api_nodes：Redis → ES → 本地文件
读取 biz_config：Redis → ES → 本地文件
```

伪代码可理解为：

```python
api_nodes = redis.get(api_nodes_key)
if api_nodes is None:
    api_nodes = es.get_published(api_nodes)
    if api_nodes exists:
        redis.set(api_nodes_key, api_nodes)
if api_nodes is empty:
    api_nodes = local_file.read()

biz_config = redis.get(biz_config_key)
if biz_config is None:
    biz_config = es.get_published(biz_config)
    if biz_config exists:
        redis.set(biz_config_key, biz_config)
if biz_config is empty:
    biz_config = local_file.read()
```

这里最重要的细节：不能因为 Redis 命中了 `api_nodes`，就认定整个技能包都命中 Redis。`biz_config` 仍可能没有缓存，必须继续从 ES 读取并回填 Redis；项目已经按这种方式实现。

| Redis / ES 状态 | reload 后使用的配置 | 是否回填 Redis |
|---|---|---:|
| 两类配置都命中 Redis | Redis 缓存 | 否 |
| `api_nodes` 命中、`biz_config` 未命中 | `api_nodes` 用 Redis；`biz_config` 用 ES | 回填 `biz_config` |
| Redis 两类都未命中，ES 有 | ES 配置 | 分别回填两类缓存 |
| Redis 不可用，ES 有 | ES 配置 | 否 |
| Redis、ES 都没有该类型 | 本地 JSON | 否 |
| Redis/ES 调用报异常 | 整个外部读取降级到本地 JSON | 否 |

## 5. 多实例如何得到新配置

默认同步模式是 `hybrid`，即两条通路同时工作：

```text
实例 A 发布成功
  ├─ 本实例立刻 reload
  ├─ Redis Pub/Sub 消息 → 实例 B/C 收到后立即 reload
  └─ ES 版本轮询（默认每 300 秒）→ 漏掉消息的实例最终 reload
```

### 快路径：Redis Pub/Sub

1. 发布方将新缓存写进 Redis；
2. 发布方广播 `{province}:{intent}`；
3. 每个订阅实例收到消息后 reload 该技能包；
4. reload 会优先从 Redis 读到刚写入的新版配置。

Redis Pub/Sub 是瞬时消息，不是可靠消息队列；实例断线期间可能错过消息。代码在订阅重连后触发一次全量 reload，作为第一层补偿。

### 兜底路径：ES 轮询

每个实例保存一份“已发布版本号”基线；每个轮询周期从 ES 批量读取版本摘要，发现版本变化就 reload 对应技能包。即使 Redis 长时间不可用，只要 ES 可用，配置也会最终同步。

## 6. 回滚时的顺序

`rollback_config()` 的顺序与保存不同：

```text
① ES 将指定历史版本重新设为 published
② 从 ES 读出回滚后的 published 数据
③ 写本地 JSON 快照
④ 删除 Redis 旧缓存
⑤ 将 ES 数据写回 Redis 新缓存
⑥ Redis Pub/Sub 广播
⑦ 当前实例 reload
```

删除再写 Redis 的目的是避免回滚窗口中继续读取旧缓存。

## 7. 特例：测试用例 `test_cases`

测试用例也复用 ES 的版本化存储，但保存时 `notify=False`，不会因为测试用例变更触发技能运行时 reload。其写入策略是：

```text
生产且 ES 可用：优先写 ES
ES 不可用或写失败：写本地 test_cases.json
```

这与普通 `api_nodes` / `biz_config` 不同：普通配置在生产环境不能把“ES 写失败”当成功；测试用例允许本地降级保存。

## 8. 一句话记忆

```text
保存：ES 先成功，Redis 后加速并通知。
单包 reload：Redis 先读，未命中再读 ES，最后读本地。
完整启动：ES 批量优先，保障发现 ES-only 技能包。
同步：Redis 追求立即生效，ES 轮询保证最终一致。
```

## 对应实现

- `services/skill_publisher.py`：普通配置发布、回滚编排
- `services/es_config_store.py`：ES 版本化读写
- `services/redis_config_bus.py`：Redis 热缓存与 Pub/Sub
- `utils/skill_runtime.py`：技能包加载、单包 reload、同步启动
- `services/config_poller.py`：ES 版本轮询
- `services/test_case_store.py`：测试用例特例
