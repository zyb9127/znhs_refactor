"""
接口映射 ReAct Agent 首轮任务提示词（迁移自
management/interface_mapper/scripts/agent_runner.py 的 task f-string）

仅外置字符串模板，run() 中的拼接与工具编排逻辑不变，模型调用行为不变。
Agent 的 system 提示词仍来自 management/interface_mapper/SKILL.md（本身已是独立文件，未收拢）。

占位符：
  {province}   省份
  {intent}     意图
  {docx_head}  base64 文档前 200 字符（用于日志展示的截断预览）
  {docx_full}  完整 base64 文档内容
"""

# Agent 首轮 user 消息模板（原为 agent_runner.run() 内的 f-string，改为等价 .format）
INTERFACE_AGENT_TASK_TEMPLATE = (
    "请处理以下接口文档，自动生成 Skill 包。\n"
    "省份：{province}\n"
    "意图：{intent}\n"
    "docx 文档内容（base64）：{docx_head}...已截断\n"
    "\n请按以下顺序执行："
    "1. 调用 parse_docx 解析完整文档（使用完整的 base64 内容：{docx_full}）\n"
    "2. 调用 match_params 匹配入参【重要：若 parse_docx 返回了 input_example 字段，"
    "必须将其 json.dumps 后作为 input_example_json 参数传入，以正确处理嵌套结构和 wrapper】\n"
    "3. 调用 map_output 映射出参\n"
    "4. 调用 detect_units 检测单位\n"
    "5. 调用 generate_skill 生成 Skill 包，province='{province}' intent='{intent}'，"
    "【重要1】必须将 parse_docx 返回的 success_example 字段原样作为 mock_response 参数传入"
    "（若文档无 JSON 示例，success_example 已根据出参表自动生成骨架），用于 mock 模式下的数据模拟，不可省略。"
    "【重要2】必须将 map_output 返回的 output_params 字段原样作为 output_params 参数传入，"
    "系统将自动扫描套餐域字段说明推断 field_aliases；"
    "同时根据出参说明手动推断 field_aliases（pkg_name/pkg_fee/pkg_flow/pkg_voice/product_id），"
    "两者都传入以确保 biz_config.json 中的 field_aliases 包含省份专属字段名。"
)

__all__ = ["INTERFACE_AGENT_TASK_TEMPLATE"]
