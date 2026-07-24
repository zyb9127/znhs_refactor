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
    "【上下文数据】（均为最终可直接引用的参数值：接口出参已按映射规则完成字段重命名与单位换算，"
    "直传字段为主服务入参原值；是你唯一可依据的事实来源，请勿使用未在此列出的信息）"
)

# 话术模板小节标题（其下为模板正文）
SCRIPT_TEMPLATE_HEADER = "【话术模板】"

# 生成规则（防编造 / 防串填 / 槽位填充；话术要求由 build_prompt 追加为第 5 条）
SCRIPT_GEN_RULES = (
    "【生成规则】\n"
    "1. 仅依据【上下文数据】中的事实填充话术模板，不得编造数据中不存在的"
    "数字、套餐名、优惠、功能或权益。\n"
    "2. 若某项信息缺失、为空或为 0，则跳过对应表述，既不提及、也不得用其他字段的值代替"
    "（例如语音为 0 则不谈语音；优惠月数为 0 则表述为“连续包月”而非“连续 0 个月”）。\n"
    "3. 占位符对应关系：【上下文数据】每行已用 {占位符} 标注其对应的槽位，"
    "请将【话术模板】中出现的同名 {占位符} 替换为该行的事实值（含 {域[子键]} 形式的子字段占位符，"
    "须整串同名精确对应）；严禁串填：尤其不得用套餐内包含量（套餐流量/语音额度/月费）"
    "冒充历史使用量（月均流量/主叫时长/月均消费），反之亦然；"
    "若该行事实包含多个指标（如历史用量、用户标签），不得原样罗列、也不得因内容多而整体略过该槽位，"
    "应提炼其中最能支撑推荐理由的 1-3 个要点，口语化融入话术"
    "（如“您月均流量已达37GB、接近饱和”）；"
    "模板中出现但【上下文数据】未列出的占位符按第 2 条处理（跳过、不臆造、不串填）。\n"
    "4. 保留话术模板的语义与结构，输出贴合用户痛点、可直接对客播报的完整话术，"
    "最终结果不得残留任何 {} 占位符或字段名。"
)

# 个性化润色规则（当上下文含用户标签/画像/性格类信息时由 build_prompt 自动追加）
SCRIPT_PERSONA_RULE = (
    "个性化润色：结合【上下文数据】中的用户标签/画像/性格信息调整称呼、语气与卖点顺序"
    "（如价格敏感型客户强调优惠与性价比、流量大户强调流量升级、性格沉稳者用平实可信的措辞），"
    "标签与画像仅用于选择表达风格和卖点侧重，不得把标签名或画像字段名原样写进话术。"
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
    "SCRIPT_PERSONA_RULE",
    "SCRIPT_OUTPUT_SUFFIX",
    "SCRIPT_LEGACY_USER_TEMPLATE",
]
