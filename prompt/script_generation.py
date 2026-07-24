"""
话术生成提示词（engine/prompt_builder.py 使用的框架级提示词常量）

「新格式」（linked_vars / 话术模板正文驱动）Prompt 采用 context 工程结构，
将接口映射得到的标准域数据作为「事实上下文」前置，再给出话术模板与生成规则，
引导大模型仅依据映射结果填充模板槽位、缺失/为 0 的信息不臆造不串填。

常量分段（拼接顺序见 engine.prompt_builder.build_prompt 新格式分支）：
  SCRIPT_SYSTEM_HEADER    角色/任务说明（首行）
  SCRIPT_CONTEXT_HEADER   事实上下文小节标题（其下逐行列出映射域 标签：值）
  SCRIPT_TEMPLATE_HEADER  话术模板小节标题（其下为模板正文，含 {槽位}）
  SCRIPT_GEN_RULES        生成规则（防编造 / 防串填 / 槽位填充）
  SCRIPT_OUTPUT_SUFFIX    结尾输出指令
  SCRIPT_LEGACY_USER_TEMPLATE 旧格式（user_prompt_tpl 为空时）默认 user 模板（保持不变）
"""

# 新格式 Prompt 首行（角色 / 任务）
SCRIPT_SYSTEM_HEADER = (
    "你是套餐营销推荐坐席，负责将【上下文数据】填充进【话术模板】，"
    "生成自然、口语化的个性化套餐营销推荐话术。"
)

# 事实上下文小节标题（其下为映射域逐行 标签：值）
SCRIPT_CONTEXT_HEADER = (
    "【上下文数据】（经接口映射得到的真实用户与套餐数据，"
    "是你唯一可依据的事实来源，请勿使用未在此列出的信息）"
)

# 话术模板小节标题（其下为模板正文）
SCRIPT_TEMPLATE_HEADER = "【话术模板】"

# 生成规则（防编造 / 防串填 / 槽位填充；话术要求由 build_prompt 追加为第 5 条）
SCRIPT_GEN_RULES = (
    "【生成规则】\n"
    "1. 仅依据【上下文数据】中的事实填充话术模板，不得编造数据中不存在的"
    "数字、套餐名、优惠、功能或权益。\n"
    '2. 若某项信息缺失、为空或为 0，则跳过对应表述，既不提及、也不得用其他字段的值代替'
    '（例如语音为 0 则不谈语音；优惠月数为 0 则表述为“连续包月”而非“连续 0 个月”）。\n'
    '3. 占位符对应关系：【上下文数据】每行已用 {占位符} 或 [占位符] 标注其对应的槽位，'
    '请将【话术模板】中出现的同名 {占位符} 或 [占位符] 替换为该行的事实值（按语义就近对应，不要张冠李戴）；'
    '模板中出现但【上下文数据】未列出的占位符按第 2 条处理（跳过、不臆造、不串填）。'
    '含运算的复合占位符（如 {a-b}、{a+b}）若任一参与计算的值缺失或未在上下文中出现，整句跳过，'
    '严禁从其他字段借用数值凑数计算。\n'
    '4. 话术模板是你的语言基准：已有具体措辞的句子不得改写为同义表达，只需替换其中的占位符；'
    '只有模板明确为空或仅含方向性提示（如"介绍套餐卖点"）时，才可依据上下文数据自行组织话术。'
    '若第5条话术要求（如字数限制、风格侧重）与模板篇幅或内容冲突，以第5条为准，'
    '可对模板做必要的精简或调整以满足第5条要求。'
    '最终结果不得残留任何 {} 或 [] 占位符。'
)

# 新格式 Prompt 结尾输出指令
SCRIPT_OUTPUT_SUFFIX = "请直接输出话术文本，不需要任何前缀标签：\n话术："

# 旧格式默认 user 模板（user_prompt_tpl 为空时兜底；含 {占位符}，由 build_prompt 做 format_map）
SCRIPT_LEGACY_USER_TEMPLATE = (
    "用户当前套餐：{cur_brief}\n"
    "推荐套餐：{pkg_brief}\n"
    "套餐差异：{diff_str}\n"
    "近期用量：{usage_line}\n"
    "用户标签：{user_tags}\n"
    "用户基础信息：{user_info}\n"
    "用户画像：{user_profile}\n"
    "扩展信息：{domain_ext}\n"
    "意图：{intent}\n\n"
    "请用中文写一句{max_length}字以内的营销推荐话术，结尾带办理引导。\n话术："
)

__all__ = [
    "SCRIPT_SYSTEM_HEADER",
    "SCRIPT_CONTEXT_HEADER",
    "SCRIPT_TEMPLATE_HEADER",
    "SCRIPT_GEN_RULES",
    "SCRIPT_OUTPUT_SUFFIX",
    "SCRIPT_LEGACY_USER_TEMPLATE",
]
