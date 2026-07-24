"""
话术默认 prompt_template（迁移自 AutoConfigAgent/code_generator.py）

用于导入/代码生成时，当模板未提供 prompt_template 时写入 biz_config 的默认值。
仅外置字符串常量，生成逻辑仍在 code_generator.py 中，行为不变。

注意：这是"写入配置的默认种子"，写入后即成为可被运营在 biz_config/ES 中覆盖的配置数据，
与运行态 prompt/script_generation.py 的框架级提示词分工不同。
"""

# 默认 Prompt 模板（含 {template_content}/{max_length} 占位符，由话术生成时 format）
DEFAULT_PROMPT_TEMPLATE = (
    "你是移动套餐营销推荐坐席。\n"
    "请基于以下用户信息和话术模板生成个性化套餐营销推荐话术：\n\n"
    "话术模板：{template_content}\n\n"
    "直接输出话术文本，不需要前缀，口语化；"
    "营销推荐话术贴合用户痛点，字数{max_length}字以内。"
)

__all__ = ["DEFAULT_PROMPT_TEMPLATE"]
