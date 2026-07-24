# 自动配置智能体（新版）

> 话术智能体 Skill 包一键导入与自动生成工具。运营人员通过上传或粘贴标准配置模板，即可自动生成并发布到话术智能体的 `skills-runtime` 目录，无需手动编写代码。

---

## 功能概览

| 功能 | 说明 |
|------|------|
| **一键导入** | 上传 JSON 文件或粘贴文本，自动解析并验证配置 |
| **预览编辑** | 在线查看和编辑生成的 `api_nodes.json`、`biz_config.json` 等文件 |
| **一键发布** | 将 Skill 包写入 `话术智能体-生产/skills-runtime` 并触发热重载 |
| **格式验证** | 导入时自动检查必填字段，给出明确提示 |
| **自动备份** | 覆盖已有配置时自动备份原文件 |

---

## 快速启动

**推荐：与 znhs 主服务统一启动（无需单独起 :8001）**

```bash
cd znhs
python main.py
```

AutoConfig API 挂载在：
- 开发/灰度：`/znhs-gray/api/auto-config/*`
- 生产：`/znhs/api/auto-config/*`

前端页面通过 znhs 前端访问：`npm run dev` → `/znhs-gray/SkillManager`

---

**独立调试（不推荐，仅兼容旧用法）**

```bash
cd znhs/AutoConfigAgent
pip install -r requirements.txt
python server.py   # 仍监听 8001，API 路径 /api/*
```

---

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `AUTO_CONFIG_PORT` | `8001` | 服务端口 |
| `SKILLS_RUNTIME_PATH` | `../话术智能体-生产/skills-runtime` | skills-runtime 绝对路径 |
| `PROD_API_BASE` | `http://localhost:8000/znhs` | 话术智能体 API 地址（用于热重载） |

---

## 导入模板格式

```json
{
  "meta": {
    "province": "shandong",        // 省份代码（英文）
    "province_name": "山东",        // 省份名称（可选）
    "intent": "套餐推荐",            // 业务意图
    "description": "山东套餐推荐",
    "version": "1.0.0",
    "author": "ops-team"
  },
  "api": {
    "name": "shandong_package_api",
    "url": "http://your-api/recommend",
    "method": "POST",
    "headers": {"Content-Type": "application/json"},
    "request_template": {
      "phone": "{{PHONE}}",
      "intent": "{{INTENT}}"
    },
    "response_extract": {
      "recommended_packages": "bean.recommend_results",
      "current_package": "bean.mainoffer",
      "raw_tags": "bean.tags"
    },
    "mock_mode": true,
    "mock_response": { ... }
  },
  "strategy": {
    "default_strategy": "direct",
    "top_n": 3
  },
  "templates": [
    {
      "template_name": "套餐推荐话术",
      "stage": "",
      "template_content": "您好，您当前套餐为{cur_brief}...",
      "script_requirement": "直接输出话术文本，口语化，字数80字以内。"
    }
  ]
}
```

完整示例：访问 `GET /api/template/example` 获取。

---

## 生成文件说明

每次导入会在 `skills-runtime/{province}/{intent}/` 下生成：

```
{province}/{intent}/
├── SKILL.md                  # Skill 说明文档
├── _meta.json                # 场景元数据
├── config/
│   ├── api_nodes.json        # 接口节点配置
│   └── biz_config.json       # 业务配置（策略+话术模板）
└── scripts/
    └── main_flow.py          # 主流程脚本（标准模板）
```

---

## API 端点

| 端点 | 说明 |
|------|------|
| `GET /` | 前端页面 |
| `GET /api/health` | 健康检查 |
| `GET /api/provinces` | 省份+意图列表 |
| `GET /api/template/example` | 获取示例导入模板 |
| `POST /api/import/parse` | 解析上传文件（multipart） |
| `POST /api/import/parse-text` | 解析粘贴文本 |
| `POST /api/import/generate` | 生成 Skill 包预览 |
| `POST /api/import/validate` | 跑通校验（接口/映射/模板槽位） |
| `POST /api/import/publish` | 发布到 skills-runtime |
| `GET /api/skills/{province}/{intent}/config` | 读取现有 Skill 配置 |

---

## 架构说明

```
自动配置智能体-新的/
├── server.py           # FastAPI 后端（导入/预览/发布接口）
├── code_generator.py   # Skill 包代码生成核心逻辑
├── static/
│   └── index.html      # Vue 3 + Element Plus 前端（CDN，无需构建）
├── requirements.txt
└── README.md
```

与话术智能体-生产的关系：
- **读取**：从 `skills-runtime` 读取已有省份/意图列表
- **写入**：将生成的 Skill 包写入 `skills-runtime/{province}/{intent}/`
- **热重载**：通过 `POST /api/skills/reload` 触发生产服务重载
