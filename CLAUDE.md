# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

营销话术推荐智能体（znhs_refactor）— 为运营商呼叫中心提供个性化营销话术生成的 FastAPI 服务。采用 "配置驱动 + 三步管道 + 大模型生成" 架构，按省份+意图维度隔离配置。

## 常用命令

### 后端（Python）

```bash
cd znhs_refactor

# 本地开发（无鉴权，访问 /znhs-gray/）
python main.py

# 灰度模式（强制鉴权）
LINGYUN_ENV=gray python main.py

# 生产模式（强制鉴权，访问 /znhs/）
LINGYUN_ENV=production python main.py

# 安装依赖
pip install -r requirements.txt
```

### 前端（Vue3 + Vite）

```bash
cd znhs_refactor/frontend

# 安装依赖
npm ci

# 本地开发
npm run dev

# ⚠️ main.py 只挂载 dist-gray / dist-prod / dist-dev，不挂载 dist/
# 前端代码改动后统一打三套包（推荐，dist-gray 即灰度页面 /znhs-gray/ 所用的资源）
npm run build:artifacts

# 仅重建灰度包（单独改灰度前端时用）
npm run build:artifact:gray
```

> ⚠️ **前端改了但页面没变化？先强刷浏览器（Cmd+Shift+R）。**
> 这是 Vue SPA：后端部署了新版带 hash 的 JS 后，浏览器里跑的还是内存中的旧版，
> 路由切换不会重新请求资源，JS 走 last-modified/ETag 启发式缓存，同名文件直接复用旧的。
> 正常访问 index.html 是 no-store，但 SPA 运行中不刷新就看不到新代码——**不是构建问题**。
> 验证部署是否生效时，用构建产物里【真正渲染的文案】比对（注释会被压缩剥掉，如
> "取数模式"只出现在源码注释里，产物里永远找不到，不能当"没部署"的证据）。

### 测试

```bash
cd znhs_refactor
python -m pytest tests/ -v
```

## 架构

### 核心执行管道

三步串行管道（`core/pipeline.py` → `MarketingPipeline`），无框架依赖，纯 Python async：

1. **DataStep** (`steps/data_step.py`) — 并发调用外部接口获取用户/产品数据，映射到 7 大标准数据域
2. **RecommendStep** (`steps/recommend_step.py`) — 按策略筛选 TopN 推荐产品
3. **ScriptStep** (`steps/script_step.py`) — 匹配话术模板 + 调用大模型生成话术

Step 实例按 `province:intent` 维度缓存（`StepBundle`），同省同意图复用。

### 7 大标准数据域

各省异构接口数据统一收敛：`current_package`、`usage`、`tags`、`user_info`、`recommended_packages`、`user_profile`、`domain_ext`。定义见 `schemas/standard_domains.json`。

### 组件化引擎（engine/）

可选的 JSON 驱动管道执行器（`engine/pipeline_runner.py`），通过 `biz_config["pipeline"]` 配置自定义步骤序列，替代硬编码三步管道。组件注册在 `engine/components/`，通过 `engine/registry.py` 管理。

### 目录结构

| 目录 | 职责 |
|------|------|
| `core/` | `FlowContext` 上下文对象、`MarketingPipeline` 管道 |
| `steps/` | 三步具体实现（DataStep/RecommendStep/ScriptStep） |
| `engine/` | 组件化执行引擎、Prompt构建器、模板选择器 |
| `routers/` | FastAPI 路由：`realtime.py`（推荐接口）、`management.py`（运营管理API）、`spa.py`（Vue前端页面） |
| `services/` | 外部服务：LLM调用、ES配置存储、Redis配置总线、Kafka、鉴权、缓存 |
| `management/` | 运营后台功能模块（接口映射器、自动配置Agent） |
| `prompt/` | LLM Prompt 模板（话术生成、接口映射、配置Agent等） |
| `plugins/` | 可插拔策略（推荐策略、套餐差异计算、单位转换） |
| `schemas/` | JSON Schema 定义（接口节点、业务配置、管道、话术模板） |
| `skills-runtime/` | 各省技能包运行时目录（beijing/fujian/guangdong/liaoning/shandong） |
| `AutoConfigAgent/` | 自动配置智能体（上传JSON一键生成Skill包） |
| `config/` | 应用配置（`config.json`）+ Agent业务配置（`agents_config.json`）+ 省份映射 |
| `utils/` | 工具：日志、环境配置、鉴权、可观测性、配置加载、变量推断 |

### 环境模式

通过环境变量 `LINGYUN_ENV` 控制，`config/config.json` 中 `app.environment` 为默认值：

| 模式 | 前缀 | 鉴权 | 用途 |
|------|------|------|------|
| `development` | `/znhs-gray` | 关 | 本地开发 |
| `gray` | `/znhs-gray` | 开 | 灰度验证 |
| `production` | `/znhs` | 开 | 生产 |
| `production_noauth` | `/znhs-gray` | 关 | 受控测试 |

### 前端双重挂载

`main.py` 启动时同时挂载灰度和生产两套前端静态资源（若对应 `dist/` 存在），按 URL 前缀分流，互不干扰。

### 配置存储与多实例同步

- ES 持久化存储技能配置（`znhs-agent-skill-configs` index）
- Redis Pub/Sub + 定时轮询混合同步（`redis_bus.sync_mode: hybrid`）
- 降级链：ES → Redis → 本地文件

### 主接口入参

`POST /znhs/marketing/recommend`，必填：`callId`、`intent`、`phone`、`topN`、`province`；可选：`extra_data`、`extra_info`、`extra_context`（详见 `主服务入参.txt`）。

## 新包替换与本地启动流程

**触发条件**：当 `/Users/zyb/Documents/python/` 下出现新的 `znhs_refactor*.zip` 压缩包时，执行以下流程。

**原则**：Git 管理自定义修改，新包为唯一真实来源，直接全覆盖。

### 替换步骤

```bash
# 1. 停掉当前服务
lsof -ti :8000 | xargs kill -9 2>/dev/null; sleep 1

# 2. 解压新包到临时目录
unzip -o "/Users/zyb/Documents/python/znhs_refactor(最新版).zip" -d /tmp/znhs_extract

# 3. rsync 覆盖项目文件（排除缓存和 node_modules）
rsync -av \
  --exclude='__MACOSX' --exclude='.DS_Store' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='node_modules' --exclude='logs' \
  /tmp/znhs_extract/znhs_refactor/ /Users/zyb/Documents/python/znhs_refactor/

# 4. 修改 config/config.json 为本地开发环境：
#    - app.environment → "development"
#    - elasticsearch.hosts → []
#    - elasticsearch.username → ""
#    - elasticsearch.password → ""
#    - redis_bus.cluster_nodes → []
#    - redis_bus.password → ""
```

### 启动步骤

```bash
cd /Users/zyb/Documents/python/znhs_refactor
LINGYUN_ENV=development python main.py > /tmp/znhs_server.log 2>&1 &

# 等待 ~12 秒后验证
sleep 12 && curl -s http://localhost:8000/health
# 期望返回: {"status":"ok"}
```

- 前端入口：`http://localhost:8000/znhs-gray/SkillManager`
- Python 虚拟环境：`/Users/zyb/Documents/python/.venv/bin/python3`
