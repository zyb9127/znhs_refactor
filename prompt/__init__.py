"""
prompt —— 大模型提示词集中管理目录（一类提示词一个文件，方便后续统一优化）

设计原则：
- 本目录只收拢"工程/框架级"硬编码提示词（原先散落在各 .py 里的 system/user 模板常量），
  抽取时保持字符串**逐字节不变**，仅把定义位置外置，调用处 import 引用，
  所有 .format()/f-string 拼接逻辑仍保留在原业务代码中，**不改变任何模型调用行为**。
- "运营/省份/产品级"的可配置提示词（biz_config.json / ES 里的 prompt_template、
  script_requirement、template_content、prompts.user_prompt_template 等）**不在此目录**，
  仍由运营在配置层维护。

文件划分：
  script_generation.py  话术生成（新格式 system 头/尾、旧格式默认 user 模板）
  script_defaults.py    导入/代码生成时写入 biz_config 的默认 prompt_template
  placeholder_fill.py   话术模板编辑「智能填充占位符」（基于语义改写为 {key} 占位符）
  interface_mapping.py  接口出参智能映射（auto_map / parse_docx / refine + 共用片段）
  interface_agent.py    接口映射 ReAct Agent 首轮任务模板
  config_agent.py       配置智能体 system 提示词

未收拢（本身已是独立文件 / 属结构化 schema，可后续按需迁入）：
  management/interface_mapper/SKILL.md   接口映射 Agent 的 system 行动手册（已是独立文件）
  agent_runner._TOOL_SCHEMAS / config_agent.agent_tools.TOOLS  Function Calling 工具 schema
"""
