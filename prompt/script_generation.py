"""
话术生成提示词（engine/prompt_builder.py 使用的框架级提示词常量）

「新格式」（linked_vars / 话术模板正文驱动）Prompt 采用 context 工程结构，
将接口映射得到的标准域数据作为「事实上下文」前置，再给出话术模板与生成规则，
引导大模型仅依据映射结果填充模板槽位、缺失/为 0 的信息不臆造不串填。

常量分段（拼接顺序见 engine.prompt_builder.build_prompt 新格式分支）：
  SCRIPT_SYSTEM_HEADER    角色/任务说明（首行）
  SCRIPT_CONTEXT_HEADER   事实上下文区块标题（其下逐行列出映射域 标签：值）
  SCRIPT_TEMPLATE_HEADER  话术模板区块标题（其下为模板正文，含 {槽位}）
  SCRIPT_GEN_RULES        生成规则（防编造 / 防串填 / 槽位填充 / 模板为主体框架）
                          = build_gen_rules() 的默认结果（passthrough 照实填槽口径）
  SCRIPT_LENGTH_RULE      字数规则（按 max_length 追加，让位于「话术要求」）
  SCRIPT_OUTPUT_SUFFIX    结尾输出指令
  SCRIPT_LEGACY_USER_TEMPLATE 旧格式（user_prompt_tpl 为空时）默认 user 模板（保持不变）
"""

# 新格式 Prompt 首行（角色 / 任务）
SCRIPT_SYSTEM_HEADER = (
    "你是套餐营销推荐坐席，负责将【上下文数据】填充进【话术模板】，"
    "生成自然、口语化的个性化套餐营销推荐话术。"
)

# 事实上下文区块标题（其下为映射域逐行 标签 {占位符}：值）
# 强调：映射模式下值为「接口出参→标准域」加工后的最终结果；行内 {占位符} 与模板槽位一一对应。
SCRIPT_CONTEXT_HEADER = (
    "【上下文数据】（映射模式最终事实：接口出参已按映射规则写入标准域并完成字段重命名与单位换算；"
    "直传字段为主服务入参原值。下列每行「{占位符}：值」即为该槽位唯一正确取值，"
    "请按同名占位符原样填入话术，勿反推接口原始字段名、勿改写数值。"
    "未列出的占位符表示映射结果为空——是你唯一可依据的事实来源，请勿使用未在此列出的信息）"
)

# 话术模板区块标题（其下为模板正文）
SCRIPT_TEMPLATE_HEADER = "【话术模板】"

# 缺失事实区块（模板引用了槽位、但映射结果为空时由 build_prompt 自动追加）
# 背景：仅靠「上下文里没有就别提」的隐式规则，模型仍会拿手边最像的数字顶上——
# 北京生产曾把当前套餐的 128 元/月、30GB、200 分钟当成用户的月均消费/流量/通话播报出去。
# 把缺口显式列出来并给出负向约束，比让模型自行推断"哪些信息不存在"可靠得多。
SCRIPT_MISSING_FACTS_HEAD = "【缺失事实】本次未取到以下槽位的数据（映射结果为空）："
SCRIPT_MISSING_FACTS_TAIL = (
    "。上述槽位没有任何可用事实：严禁编造，严禁用其他行的值代替"
    "——尤其不得用当前套餐或推荐套餐的包含量（套餐流量、语音额度、月费）"
    "冒充用户的历史使用量（月均流量、主叫时长、月均消费）；"
    "请在话术中整体略过相关表述，也不得保留占位符原文。"
)

# ── 空槽位口径：照实透传填槽（默认） / 删句（旧口径，可按技能包回退）────────
# 由 biz_config.slot_fallback.mode 选择，默认 passthrough。
#
# passthrough（默认）：平台只负责忠实填槽，不替业务做「这句该不该说」的判断。
#   - 有事实 → 原样填入，值为 0 也照实说 0；
#   - 缺失 / 值为空 / 入参是掩码（**）→ 保留原句，槽位处写占位符（默认 **）。
#   这样总部与分中心各自人工维护的模板槽位始终对称，坐席照读不会漏项或解释错位。
#   要「某句无数据时干脆不说」属于个性化诉求，改由该模板自己的【话术要求】表达。
#
# drop（旧口径）：空值/零值连句子一起删。保留它是给已按此调优过话术的省份留回退开关，
#   以及万一 passthrough 在生产上不合适时能按技能包快速回滚。
#
# 规则正文里的 <PH> 由 build_gen_rules / build_missing_facts_tail 替换为实际占位符文本
# （用哨兵而非 str.format：规则正文本身含 {占位符}/{current_package} 等花括号）。
SLOT_FALLBACK_PASSTHROUGH = "passthrough"
SLOT_FALLBACK_DROP = "drop"
DEFAULT_SLOT_PLACEHOLDER = "**"
# 历史别名：passthrough 口径最初以 "placeholder" 命名（当时零值仍走删句），沿用不报错
_MODE_ALIASES = {"placeholder": SLOT_FALLBACK_PASSTHROUGH}
_PH_SENTINEL = "<PH>"

SCRIPT_MISSING_FACTS_TAIL_PASSTHROUGH = (
    "。上述槽位没有任何可用事实：严禁编造，严禁用其他行的值代替"
    "——尤其不得用当前套餐或推荐套餐的包含量（套餐流量、语音额度、月费）"
    "冒充用户的历史使用量（月均流量、主叫时长、月均消费）；"
    "但必须保留包含这些槽位的句子，把槽位本身写成 <PH>"
    "（不得删句、不得改写句式），由坐席对客时口头补充。"
)

# 生成规则（防编造 / 防串填 / 槽位精确对应；话术要求由 build_prompt 追加为第 5 条）
# 拆成片段而非两份全文：只有「槽位取不到值 / 值为 0 时怎么办」这一处口径随模式变化，
# 防编造、防串填、模板为主体框架等条文只有一份，避免日后改了一份忘了另一份
# （tests/test_slot_fallback.py 断言两种模式复用同一批共用片段）。
_RULES_HEAD = (
    "【生成规则】\n"
    "1. 仅依据【上下文数据】中的事实填充话术模板，不得编造数据中不存在的"
    "数字、套餐名、优惠、功能或权益。\n"
)
_RULE2_DROP = (
    "2. 空值/零值必须略过（本条优先于第 3 条）：某 {占位符} 缺失、无对应行，"
    "或其对应行的值为 0、0元、0分钟、0个月、0GB、“无”、“—” 等空/零值时，"
    "一律视为该项无有效内容，必须整体删除包含该占位符的那句话/短语，"
    "严禁说出“0元/0分钟/0个月/0GB”这类表述，也不得改用其他字段的值顶替"
    "（例如语音为 0 则不谈语音、不说“0分钟”；优惠月数为 0 则表述为“连续包月”而非“连续 0 个月”）；"
    "若整句仅剩该项则删除整句。严禁把占位符原文（如 {current_package}）留在输出中。\n"
)
_RULE2_PASSTHROUGH = (
    "2. 槽位照实填充、一律不得删句（本条优先于第 3 条）：【上下文数据】中有对应行的"
    " {占位符}，一律原样填入该行的值——值为 0、0元、0分钟、0个月、0GB 时也照实说出，"
    "不得因为是 0 就略过该句、改写成别的说法或换用其他字段的值；"
    "某 {占位符} 缺失、无对应行，或其对应行的值为空时，保留包含该占位符的那句话/短语，"
    "只把该占位符本身替换为 <PH>（例如月均消费无数据时输出“您月均消费<PH>元”），"
    "由坐席对客时口头补充，不得删句、不得改写句式、不得调整句序，也不得编造数值顶替。"
    "若下方【话术要求】明确指出某项无数据（或为 0）时不要提及，则该项以【话术要求】为准，"
    "按其所述范围整体略过（可为一句、连续几句或一整段）"
    "——各分中心的模板对同一槽位可有相反要求，以该模板自己的【话术要求】为准。"
    "严禁把占位符原文（如 {current_package}）留在输出中。\n"
)
_RULE3_HEAD = (
    "3. 占位符一一对应：【上下文数据】每行已用 {占位符} 标注槽位，"
    "请将【话术模板】中出现的同名 {占位符} 替换为该行冒号后的事实值"
    "（含 {域[子键]} 子字段占位符，须整串同名精确对应，不得拆开或改名）；"
)
_RULE3_VALID_DROP = (
    "有对应行且其值有效（非空、非 0，见第 2 条）则必须填入该行的值，不得留空或改用其他行；"
    "若该行的值为空/零值，则按第 2 条略过整句、不得填 0。"
)
_RULE3_VALID_PASSTHROUGH = (
    "有对应行则必须填入该行的值（含 0，见第 2 条），不得留空或改用其他行；"
    "若该行的值为空，则按第 2 条保留整句、在该占位符处填 <PH>。"
)
_RULE3_TAIL = (
    "严禁串填：{current_package}/{current_package[…]} 只用当前套餐行，"
    "{pkg_brief}/{pkg_name}/{recommended_package} 只用推荐产品行，"
    "不得用推荐套餐名/资费冒充当前套餐，也不得用套餐内包含量"
    "（套餐流量/语音额度/月费）冒充历史使用量（月均流量/主叫时长/月均消费），反之亦然；"
    "若该行事实包含多个指标（如历史用量、用户标签），不得原样罗列、也不得因内容多而整体略过该槽位，"
    "应提炼其中最能支撑推荐理由的 1-3 个要点，口语化融入话术"
    "（如“您月均流量已达37GB、接近饱和”）。\n"
)
_RULE4_HEAD = (
    "4. 以【话术模板】为话术主体框架：模板正文就是本次话术的骨架，"
    "必须沿用它的句子顺序、段落结构与表达方式，逐句保留后只做两件事——"
    "填充占位符、把语句改顺（补必要的连接词、去掉填充后的语法瑕疵）；"
    "不得改写成自己的行文、不得调整句序、不得增删模板里没有的环节或卖点"
    "（模板未提到的优惠、权益、活动、办理方式一律不得自行补充）。"
)
_RULE4_EMPTY_DROP = "只有第 2 条要求略过空值/零值时，才删除模板中对应的那句话。"
_RULE4_EMPTY_PASSTHROUGH = (
    "不得因槽位取不到值、或值为 0 而删除模板里的句子"
    "（按第 2 条填 <PH> 或照实填 0 即可）；"
    "只有【话术要求】明确要求不说的内容，才删除模板中对应的语句。"
)
_RULE4_TAIL = (
    "输出为贴合用户痛点、可直接对客播报的完整话术，"
    "最终结果不得残留任何 {} 占位符或字段名。"
)


def canon_slot_fallback_mode(mode: str) -> str:
    """归一 mode 取值（含历史别名）；无法识别时返回默认 passthrough。"""
    m = str(mode or "").strip().lower()
    m = _MODE_ALIASES.get(m, m)
    return m if m in (SLOT_FALLBACK_PASSTHROUGH, SLOT_FALLBACK_DROP) else SLOT_FALLBACK_PASSTHROUGH


def build_gen_rules(
    mode: str = SLOT_FALLBACK_PASSTHROUGH,
    placeholder: str = DEFAULT_SLOT_PLACEHOLDER,
) -> str:
    """生成规则正文：passthrough=照实填槽（缺失填占位符、0 照实填）；drop=空值/零值删整句。"""
    if canon_slot_fallback_mode(mode) == SLOT_FALLBACK_DROP:
        return (
            _RULES_HEAD
            + _RULE2_DROP
            + _RULE3_HEAD + _RULE3_VALID_DROP + _RULE3_TAIL
            + _RULE4_HEAD + _RULE4_EMPTY_DROP + _RULE4_TAIL
        )
    return (
        _RULES_HEAD
        + _RULE2_PASSTHROUGH
        + _RULE3_HEAD + _RULE3_VALID_PASSTHROUGH + _RULE3_TAIL
        + _RULE4_HEAD + _RULE4_EMPTY_PASSTHROUGH + _RULE4_TAIL
    ).replace(_PH_SENTINEL, placeholder or DEFAULT_SLOT_PLACEHOLDER)


def build_missing_facts_tail(
    mode: str = SLOT_FALLBACK_PASSTHROUGH,
    placeholder: str = DEFAULT_SLOT_PLACEHOLDER,
) -> str:
    """【缺失事实】区块结尾：passthrough=保留原句填占位符；drop=整体略过相关表述。"""
    if canon_slot_fallback_mode(mode) == SLOT_FALLBACK_DROP:
        return SCRIPT_MISSING_FACTS_TAIL
    return SCRIPT_MISSING_FACTS_TAIL_PASSTHROUGH.replace(
        _PH_SENTINEL, placeholder or DEFAULT_SLOT_PLACEHOLDER)


# 默认生成规则（= passthrough 口径）。历史名保留，供测试 oracle 与旧调用方引用。
SCRIPT_GEN_RULES = build_gen_rules()

# 字数规则（由 build_prompt 按 max_length 追加为生成规则的一条；{max_length} 由调用方替换）
# 单独成条而非并入 SCRIPT_GEN_RULES：字数随技能包 strategy.max_script_length 变化，
# 且要显式让位于运营在模板里写的「话术要求」（更具体的人工约束优先）。
SCRIPT_LENGTH_RULE = (
    "字数控制：整段话术控制在 {max_length} 字以内（含标点）。"
    "超长时优先压缩铺垫与修饰语，保留推荐产品、核心权益与办理引导；"
    "不得为压缩字数而丢掉【话术模板】里的关键环节。"
    "若下方【话术要求】另有字数规定，以【话术要求】为准。"
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
    "SCRIPT_MISSING_FACTS_HEAD",
    "SCRIPT_MISSING_FACTS_TAIL",
    "SCRIPT_GEN_RULES",
    "SCRIPT_LENGTH_RULE",
    "SCRIPT_PERSONA_RULE",
    "SCRIPT_OUTPUT_SUFFIX",
    "SCRIPT_LEGACY_USER_TEMPLATE",
]
