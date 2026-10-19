"""
大模型调用服务
基于现有llm_dialogue.py代码改造，支持营销话术推荐场景
"""
import json
import re
import time
import random
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
import httpx
from loguru import logger
from utils.config_loader import config_loader
from utils.observability import add_degrade_flag, record_stage, get_request_context, get_trace_id
from utils import province_logger
from services.llm_stats import llm_stats


class _LLMHttpStatusError(Exception):
    """网关返回非 200：携带状态码，供重试分类判断（见 LLMService._is_retryable）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMService:
    """大模型服务类

    性能设计：
    - 使用持久化 httpx.AsyncClient（类级单例），所有请求共享连接池，
      避免并发推荐时每次重建 TCP 连接的开销。
    - 连接池参数：最大连接数 20，最大 keepalive 连接数 10。
    - 服务关闭时调用 close() 释放连接池资源。
    """

    # 类级共享连接池，所有 LLMService 实例复用同一个 AsyncClient
    _shared_client: Optional[httpx.AsyncClient] = None
    _client_lock: asyncio.Lock = asyncio.Lock()

    # ── 空槽位占位符保护（仅话术链路启用）──────────────────────────────
    # `**` 同时是三种东西：Markdown 加粗语法、本系统空槽位占位符
    # （prompt.script_generation.DEFAULT_SLOT_PLACEHOLDER），以及山东等省直接写在
    # 模板正文里表示「此处填数值」的记号（山东 online 模板中有 186 条含 `**`）。
    # 通用去加粗正则 \*{1,2}([^*]+)\*{1,2} 会把相邻两个 `**` 当成一对加粗标记、
    # 连同中间文字一起脱壳，数值凭空消失且随 `**` 个数奇偶变化（落单的反而留下）：
    #     "您现在的月均消费**元，月流量**GB，主叫时长**分钟"
    #  →  "您现在的月均消费元，月流量GB，主叫时长**分钟"
    # 生产表现即「话术缺数字、只剩 元/GB 单位」。故话术链路按「占位符优先」清洗：
    # 只剥离能安全判定为排版的加粗，其余星号串一律当占位符保留。
    #
    # 可安全判定为 Markdown 排版、且与占位符互斥的只有两种形态：
    #   1. 整行就是 `**小标题**`（行内无其它文字、无其它星号）；
    #   2. `**字段名**：` —— 闭合星号串紧跟中英文冒号。
    # 句中加粗（`这个套餐**很划算**`）与占位符完全同形、无法区分，一律保留：
    # 宁可多留一个星号（可见、可人工发现），也不能把事实数值静默洗掉。
    _MD_HEADING_LINE_RE = re.compile(r"(?m)^([ \t]*)\*{2,}([^*\n]+?)\*{2,}[ \t]*$")
    _MD_LABEL_RE        = re.compile(r"\*{2,}([^*\n]+?)\*{2,}(?=[：:])")
    _ASTERISK_RUN_RE    = re.compile(r"\*{2,}")

    # ── 超时与重试 ──────────────────────────────────────────────────
    # 配置里的 timeout 只作为**读**超时（大模型出字慢，山东单次均耗时 ~10.6s，需留足）。
    # 连接/写/连接池必须单独给短值：httpx 收到标量 timeout 会把 connect/read/write/pool
    # 四项全设成同一个数，于是网关不可达时要干等 200s 才判失败，连接池占满时更是静默排队
    # 200s（日志上只表现为"接口莫名很慢"）。这三项本该秒级判定，与读超时无关。
    _DEFAULT_CONNECT_TIMEOUT = 5.0
    _DEFAULT_WRITE_TIMEOUT   = 10.0
    _DEFAULT_POOL_TIMEOUT    = 5.0

    # 可重试的 HTTP 状态：网关/上游的瞬时故障或限流，重试有意义。
    # 其余 4xx（400 入参错、401/403 鉴权、404 地址错、422 校验失败）重试多少次结果都一样，
    # 只会把失败耗时和网关负载乘以重试次数，必须立即失败。
    _RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

    # 预算兜底：剩余时间不足这个值就不再发起新尝试（连接+首字节都来不及，纯属浪费）
    _MIN_ATTEMPT_SECONDS = 1.5

    def __init__(self, province_name: str = "default", config_override: Optional[Dict] = None):
        # LLM 配置统一从 agents_config.llm_gateway 读取（主数据源），分省可覆盖
        if config_override:
            self.llm_config = config_override
        else:
            self.llm_config = config_loader.get_llm_config(province_name)
        self.province_config = config_loader.get_province_full_config(province_name)
        self.province_name = province_name
        # ⚠️ 不在此处打 INFO — 避免 uvicorn --reload 双进程重复打印
        # 启动成功消息统一由 warmup() / lifespan 输出

    @classmethod
    async def _get_client(cls) -> httpx.AsyncClient:
        """获取（或懒创建）共享 AsyncClient"""
        if cls._shared_client is None or cls._shared_client.is_closed:
            async with cls._client_lock:
                if cls._shared_client is None or cls._shared_client.is_closed:
                    limits = httpx.Limits(
                        max_connections=200,
                        max_keepalive_connections=100,
                        keepalive_expiry=30,
                    )
                    cls._shared_client = httpx.AsyncClient(limits=limits)
                    logger.info("✅ 创建共享 httpx.AsyncClient（连接池 max=20）")
        return cls._shared_client

    def _build_timeout(self, read_timeout: float) -> httpx.Timeout:
        """分离四类超时（背景见类级注释）。连接/写/池可按网关情况在配置里覆盖。"""
        def _cfg(key: str, default: float) -> float:
            try:
                v = float(self.llm_config.get(key) or 0)
                return v if v > 0 else default
            except (TypeError, ValueError):
                return default

        return httpx.Timeout(
            connect=_cfg("connect_timeout", self._DEFAULT_CONNECT_TIMEOUT),
            read=read_timeout,
            write=_cfg("write_timeout", self._DEFAULT_WRITE_TIMEOUT),
            pool=_cfg("pool_timeout", self._DEFAULT_POOL_TIMEOUT),
        )

    @classmethod
    def _is_retryable(cls, exc: BaseException) -> bool:
        """区分「重试有意义」与「重试必然同样失败」。

        可重试：超时、连接类错误（含长连接被对端关掉后复用）、以及网关瞬时故障状态码。
        不可重试：入参/鉴权/地址类 4xx —— 重试只会让失败更慢、给网关更多压力。
        """
        if isinstance(exc, _LLMHttpStatusError):
            return exc.status_code in cls._RETRYABLE_STATUS
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError,
                            httpx.RemoteProtocolError)):
            return True
        # 其余异常（JSON 解析失败、返回体缺 choices 等响应级异常）沿用既有口径：
        # 视作上游瞬时问题，允许重试。
        return True

    @classmethod
    async def close(cls):
        """关闭共享连接池（服务下线时调用）"""
        if cls._shared_client and not cls._shared_client.is_closed:
            await cls._shared_client.aclose()
            cls._shared_client = None
            logger.info("✅ 共享 httpx.AsyncClient 已关闭")

    async def warmup(self) -> None:
        """预热连接池（应用启动时调用，建立 TCP 连接复用）"""
        try:
            await self._get_client()
            logger.info("✅ LLM 连接池预热完成")
        except Exception as e:
            logger.warning(f"LLM 预热失败（不影响服务启动）: {e}")

    def _generate_request_id(self) -> str:
        """生成请求ID"""
        prov_id = self.province_config.get("id", "000")
        ms = int(time.time() * 1000)
        rand_str = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
        return f"{prov_id}-{ms}{rand_str}"

    def _get_environment(self) -> str:
        """读取 config.json app.environment，默认 development"""
        env = str(config_loader.get("app.environment", "development"))
        # 与 utils.env_config 保持一致：清洗误带入的分隔符/引号（如全角 '；' 前缀）
        return env.strip().strip(";；'\"，, ").strip().lower()

    def _build_headers(self, province: str = "") -> Dict[str, str]:
        """构建请求头，按 app.environment 区分：
        - development：加 Authorization: Bearer <api_key>，不加 host
        - gray / production：host 路由 + 完整追踪/路由头
        """
        env = self._get_environment()
        headers = {"Content-Type": "application/json"}

        if env == "development":
            api_key = self.llm_config.get("api_key") or self.llm_config.get("appcode", "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            else:
                logger.warning("⚠️ development 环境缺少 api_key")
        else:
            # gray / production：host 路由
            host = self.llm_config.get("host", "")
            if host:
                headers["host"] = host
            else:
                logger.warning(f"⚠️ {env} 环境缺少 host 配置")

            # 追踪与路由头
            ms = int(time.time() * 1000)
            rand_str = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=8))
            request_id = f"{province}-{ms}{rand_str}" if province else f"000-{ms}{rand_str}"
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            headers["X-Prov"]          = province
            headers["X-Caller"]        = province
            headers["X-Request-Id"]    = request_id
            headers["X-Date-Time"]     = now_str
            headers["X-App-Id"]        = "znhs"
            headers["X-Service-Name"]  = "agent"
            headers["X-Model-Id"]      = "ds14b"
            headers["X-Resource-Pool"] = "yidongyun"
            headers["X-GPU-Card"]      = "910B2"
            headers["X-EXT1"]          = ""

        return headers

    async def generate(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
        max_tokens: Optional[int] = None,
        stage: str = "llm.generate",
        provider: str = "llm",
        province: str = "",
        preserve_slot_placeholder: bool = False,
        budget_s: Optional[float] = None,
    ) -> str:
        """调用大模型生成内容。

        Qwen3 系列模型内置思考模式，需通过 enable_thinking=false 关闭，
        否则会先输出大段推理再给答案，且 max_tokens 不足时被截断后永远输不出话术。

        preserve_slot_placeholder：话术生成链路传 True，避免输出里的 `**`
        空槽位占位符被去 Markdown 加粗逻辑成对吃掉（详见 _clean_llm_output）。

        budget_s：本次调用（含全部重试与退避）的总时长预算，秒。None = 不限（既有行为，
        供接口映射/自动填充等离线链路使用）。话术链路必须传：灵运侧 20s 就会断开，
        而每次尝试的读超时是 200s —— 不设预算时最坏要跑 max_retries×200s+退避，
        后面那些重试没有任何人在等结果，纯粹占着 worker、占着连接、给正在故障的网关加压。
        设了预算后，只有「剩余时间还够再打一次」才会重试：首次若 0.3s 就 502，
        剩下的时间足够重试；若已烧掉 10s 才失败，则直接返回空串走模板兜底。
        """
        if not prompt:
            logger.warning("提示词为空")
            return ""

        if temperature is None:
            temperature = self.llm_config.get("temperature", 0.7)
        if max_retries is None:
            max_retries = self.llm_config.get("max_retries", 3)
        if max_tokens is None:
            max_tokens = self.llm_config.get("max_tokens", 512)

        url = self.llm_config.get("url")
        timeout = self.llm_config.get("timeout", 200)

        env = self._get_environment()
        headers = self._build_headers(province=province)
        # DeepSeek / DashScope 支持标准 messages 格式，保留 /no_think 前缀以抑制思考
        # DeepSeek / DashScope 均支持标准 messages 格式；关闭思考统一通过下方
        # chat_template_kwargs / thinking_off 处理（详见下文），此处不再拼接 /no_think 前缀
        messages = [{"role": "user", "content": prompt}]
        logger.info(
            f"[LLM] 准备请求 env={env} stage={stage} prompt={prompt} "
            f"max_tokens={max_tokens} temperature={temperature}"
        )

        data: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if env == "development":
            # 开发环境：带 model 字段
            model_id = (
                self.llm_config.get("model")
                or self.llm_config.get("model_id")
                or "qwen-plus"
            )
            data["model"] = model_id
        # gray 环境：不带 model 字段，网关根据 host 路由到对应模型

        # 关闭思考模式：不同模型厂商参数不同，统一支持 config 覆盖（agents_config.dashscope.thinking_off）。
        # thinking_off 为 dict 时直接并入请求体，供网关差异时零改代码切换；缺省按模型类型给默认：
        # - DeepSeek(含 deepseek-v4-flash 等混合推理模型): chat_template_kwargs.thinking=false（V3.1+ 关闭思考链）
        # - Qwen3/DashScope: chat_template_kwargs.enable_thinking=false（+ /no_think 前缀双保险）
        url_str = str(self.llm_config.get("url", ""))
        model_str = str(self.llm_config.get("model", "") or self.llm_config.get("model_id", ""))
        is_deepseek = "deepseek" in url_str.lower() or "deepseek" in model_str.lower()
        thinking_off = self.llm_config.get("thinking_off")
        if isinstance(thinking_off, dict):
            # 运维显式指定关闭思考的请求参数（如 {"chat_template_kwargs":{"thinking":false}}
            # 或 {"reasoning_effort":"none"} / {"thinking":{"type":"disabled"}}），原样并入
            data.update(thinking_off)
        elif is_deepseek:
            # deepseek-v4-flash 混合推理模型（dev 直连）：同时下发两家关闭思考的模板参数，
            # 模板引擎(vLLM/SGLang)会忽略不认识的 kwargs，故 DeepSeek(thinking) 与
            # 底座若为 Qwen3(enable_thinking) 都能命中，最大化"关掉思考"成功率。
            data["chat_template_kwargs"] = {"thinking": False, "enable_thinking": False}
        else:
            # 生产/灰度内网代理（qwen-plus 等）：保持既有单参数，行为不变
            data["chat_template_kwargs"] = {"enable_thinking": False}

        # 采样参数补充位（agents_config.<provider>.sampling_extra）：缺省不下发、行为完全不变。
        # 背景：部分网关（vLLM/TGI/transformers 系）在未显式开启采样时按贪婪解码执行，
        # 只给 temperature 不起作用——表现为"温度调到 1.1 了话术还是每次一模一样"。
        # 此时运维配 {"do_sample": true, "top_p": 0.95} 之类即可，无需改代码发版。
        # 不硬编码 do_sample/top_p：部分严格网关会对未知参数直接报 400，故设计为按需开启。
        sampling_extra = self.llm_config.get("sampling_extra")
        if isinstance(sampling_extra, dict) and sampling_extra:
            data.update(sampling_extra)

        last_error = None
        start_time = time.perf_counter()
        client = await self._get_client()

        def _remaining() -> Optional[float]:
            """剩余预算秒数；无预算时返回 None。"""
            if budget_s is None:
                return None
            return budget_s - (time.perf_counter() - start_time)

        for attempt in range(max_retries):
            # 预算守卫：剩余时间连一次像样的尝试都不够就别发了（首次尝试始终放行，
            # 否则预算配得过小会导致一次都不调用）
            _left = _remaining()
            if attempt > 0 and _left is not None and _left < self._MIN_ATTEMPT_SECONDS:
                logger.warning(
                    f"⏱️ 预算耗尽，放弃剩余重试 stage={stage} "
                    f"budget={budget_s}s 剩余={_left:.2f}s 已尝试={attempt}/{max_retries}"
                )
                break
            # 读超时同时受预算约束：预算只剩 6s 时没必要还挂着 200s 的读超时等下去
            read_timeout = timeout if _left is None else max(self._MIN_ATTEMPT_SECONDS,
                                                             min(timeout, _left))
            try:
                logger.info(f"🤖 调用大模型: {stage} (尝试 {attempt + 1}/{max_retries})")
                response = await client.post(
                    url,
                    headers=headers,
                    data=json.dumps(data),
                    timeout=self._build_timeout(read_timeout),
                )

                if response.status_code != 200:
                    error_msg = f"HTTP {response.status_code}: {response.text[:500]}"
                    logger.error(f"❌ 大模型API返回错误状态码: {error_msg}")
                    raise _LLMHttpStatusError(response.status_code, error_msg)

                logger.debug(f"大模型响应状态码: {response.status_code}")
                logger.debug(f"大模型响应前500字符: {response.text[:500]}")

                try:
                    output = response.json()
                except json.JSONDecodeError as e:
                    logger.error(f"❌ 无法解析JSON响应: {e}")
                    raise Exception(f"JSON解析失败: {e}. 响应: {response.text[:200]}")

                logger.info(f"大模型原始输出: {output}")

                if "choices" in output and len(output["choices"]) > 0:
                    raw_content = output["choices"][0]["message"]["content"]
                    content = self._clean_llm_output(
                        raw_content,
                        preserve_slot_placeholder=preserve_slot_placeholder,
                    )
                    logger.info(f"✅ 大模型生成成功 - 输出长度: {len(content)}")
                    _llm_elapsed = (time.perf_counter() - start_time) * 1000
                    record_stage(
                        stage=stage,
                        elapsed_ms=_llm_elapsed,
                        cache_hit=False,
                        provider=provider,
                        degrade_flag=False,
                        prompt_len=len(prompt),
                        output_len=len(content),
                    )
                    # 分省日志：记录本次大模型调用的 prompt / 原始输出（细粒度排查）
                    self._log_province_llm(
                        province, stage, prompt, raw_content, _llm_elapsed,
                        success=True,
                        temperature=temperature, max_tokens=max_tokens,
                    )
                    # 分省调用量统计：补采 usage 的 token 用量（纯内存计数，零 I/O）
                    _usage = output.get("usage") if isinstance(output, dict) else None
                    _usage = _usage if isinstance(_usage, dict) else {}
                    self._record_llm_stats(
                        province, success=True, elapsed_ms=_llm_elapsed,
                        prompt_tokens=_usage.get("prompt_tokens", 0),
                        completion_tokens=_usage.get("completion_tokens", 0),
                    )
                    return content
                else:
                    logger.error(f"大模型返回格式异常: {output}")
                    last_error = "返回格式异常"

            except httpx.TimeoutException as e:
                logger.error(f"❌ 大模型调用超时 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_error = f"调用超时: {e}"
                if attempt < max_retries - 1:
                    await self._wait_before_retry(attempt, _remaining())

            except Exception as e:
                logger.error(f"❌ 大模型调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_error = f"调用失败: {e}"
                if not self._is_retryable(e):
                    logger.error(
                        f"⛔ 该错误重试无意义，立即失败 stage={stage}: {e}"
                    )
                    break
                if attempt < max_retries - 1:
                    await self._wait_before_retry(attempt, _remaining())

        # 所有重试都失败
        logger.error(f"❌ 大模型调用最终失败: {last_error}")
        add_degrade_flag(stage)
        _llm_elapsed = (time.perf_counter() - start_time) * 1000
        record_stage(
            stage=stage,
            elapsed_ms=_llm_elapsed,
            cache_hit=False,
            provider=provider,
            degrade_flag=True,
            error=last_error,
        )
        # 分省日志：记录失败调用（含错误信息），便于分省定位大模型问题
        self._log_province_llm(
            province, stage, prompt, "", _llm_elapsed,
            success=False, error=str(last_error),
            temperature=temperature, max_tokens=max_tokens,
        )
        # 分省调用量统计：最终失败记 1 次 fail（重试中间态不计数）
        self._record_llm_stats(province, success=False, elapsed_ms=_llm_elapsed)
        return ""

    def _log_province_llm(
        self,
        province: str,
        stage: str,
        prompt: str,
        output: str,
        elapsed_ms: float,
        *,
        success: bool,
        error: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        """写分省大模型日志：province 缺省时回退请求上下文；trace_id/intent 从上下文取。

        temperature/max_tokens 传本次实际下发网关的值，供生产核对采样参数是否真的生效。
        """
        try:
            rc = get_request_context()
            prov = province or rc.get("province") or self.province_name or "unknown"
            model = str(
                self.llm_config.get("model")
                or self.llm_config.get("model_id")
                or ""
            )
            province_logger.log_llm(
                prov, rc.get("intent", ""), get_trace_id(),
                stage=stage, prompt=prompt, output=output,
                elapsed_ms=elapsed_ms, model=model,
                success=success, error=error,
                temperature=temperature, max_tokens=max_tokens,
            )
        except Exception:
            pass

    def _record_llm_stats(
        self,
        province: str,
        *,
        success: bool,
        elapsed_ms: float,
        prompt_tokens: Any = 0,
        completion_tokens: Any = 0,
    ) -> None:
        """分省调用量统计：province 缺省时回退请求上下文；intent 从上下文取。

        record() 本身是纯内存操作且不抛异常，这里再用 try/except 包一层双保险，
        主链路绝不因统计受影响。空省份由 llm_stats 归入 ``unknown``，
        ``province=test`` 由其剔除（测试页双写不计入）。
        """
        try:
            rc = get_request_context()
            prov = province or rc.get("province") or ""
            model = str(
                self.llm_config.get("model")
                or self.llm_config.get("model_id")
                or "default"
            )
            llm_stats.record(
                prov, rc.get("intent", ""), model, success, elapsed_ms,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            )
        except Exception:
            pass

    @classmethod
    def _strip_markdown_keep_placeholders(cls, text: str) -> str:
        """只剥离能安全判定为排版的 Markdown 加粗，其余星号串留给占位符（判定依据见类注释）。"""
        text = cls._MD_HEADING_LINE_RE.sub(r"\1\2", text)
        return cls._MD_LABEL_RE.sub(r"\1", text)

    @classmethod
    def _clean_llm_output(
        cls,
        raw: str,
        *,
        preserve_slot_placeholder: bool = False,
    ) -> str:
        """清洗模型输出，只保留正式话术正文。

        由于已通过 /no_think 前缀 + enable_thinking=False 双重关闭 Qwen3 思考模式，
        正常情况下不会出现 <think> 块或推理段落，此处仅做轻量正文清洗：
        - 去除残留 Markdown 格式符号
        - 去除「话术：」前缀
        - 不含中文视为异常，返回空

        preserve_slot_placeholder：话术链路传 True，把 `**` 等星号串按空槽位占位符保护，
        不让通用去加粗正则成对吃掉（背景与判定规则见类级注释）。接口映射 / refine_mapping
        等非话术链路保持默认 False，清洗行为与改动前完全一致。
        """
        text = raw.strip()
        if not text:
            return ""

        hidden_runs: List[str] = []
        if preserve_slot_placeholder:
            # 先剥离排版式加粗，再把剩余星号串换成哨兵藏起来（哨兵用 \x00 包裹，模型输出不会出现）
            text = cls._strip_markdown_keep_placeholders(text)

            def _hide(m: "re.Match[str]") -> str:
                hidden_runs.append(m.group(0))
                return f"\x00PH{len(hidden_runs) - 1}\x00"

            text = cls._ASTERISK_RUN_RE.sub(_hide, text)

        # 去 Markdown 格式符号（加粗/标题）；占位符已藏起，此处只会命中单个 * 的斜体
        text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

        # 去「话术：」前缀
        text = re.sub(r"^话术[：:]\s*", "", text).strip()

        # 占位符还原：放在「无中文」判定之前，保证判定看到的是最终文本
        for i, run in enumerate(hidden_runs):
            text = text.replace(f"\x00PH{i}\x00", run)

        # 不含中文且不像 JSON/数组 → 异常响应，置空
        # 接口映射等场景 LLM 直接返回纯 JSON，不含中文属正常，不应置空
        if text and not re.search(r"[\u4e00-\u9fff]", text):
            if not re.search(r"[\{\[]", text):
                logger.warning(f"⚠️ LLM 输出不含中文且非JSON，疑似异常响应，置空: {text[:80]}")
                return ""

        return text.strip()

    async def _wait_before_retry(self, attempt: int, remaining_s: Optional[float] = None):
        """重试前等待：指数退避 + 全抖动（full jitter），并受剩余预算约束。

        抖动不可省：原先是固定的 1s、2s，网关一抖，当前在飞的所有请求
        （单 pod 多路并发 × 多副本）会在同一时刻整齐地重试，很容易把一次
        短暂 502 顶成持续故障。改成 [0, base) 区间随机后，重试被自然打散。
        """
        base = min(2 ** attempt, 10)
        wait_time = random.uniform(0, base)
        if remaining_s is not None:
            # 别把预算睡光——至少给下一次尝试留出最小时间
            wait_time = max(0.0, min(wait_time, remaining_s - self._MIN_ATTEMPT_SECONDS))
        if wait_time <= 0:
            return
        logger.info(f"⏳ 等待 {wait_time:.2f} 秒后重试...")
        await asyncio.sleep(wait_time)

    async def chat(
        self,
        system: str = "",
        user: str = "",
        stage: str = "llm.chat",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> str:
        """兼容旧代码的简单 chat 方法（refine_mapping 等使用）。
        将 system + user 合并后调用 generate。
        """
        if system and user:
            prompt = f"{system}\n\n{user}"
        else:
            prompt = system or user or ""
        return await self.generate(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stage=stage,
            **{k: v for k, v in kwargs.items() if k not in ("system", "user")}
        )


    async def chat_with_tools(  # used by management/interface_mapper/scripts/agent_runner.py
        self,
        system: str,
        messages: list,
        tools: list,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        stage: str = "llm.chat_with_tools",
    ):
        """带 Function Calling 的对话接口（供 Agent Loop 调用）。
        返回 {"content": str, "tool_calls": list} 或 None。
        """
        import time
        url = self.llm_config.get("url")
        headers = self._build_headers()
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        data = {
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
            "tool_choice": "auto",
            "chat_template_kwargs": {"enable_thinking": False},
        }
        client = await self._get_client()
        # 使用较短的超时（30s），避免 LLM 网关不支持 tools 时长时间阻塞
        fc_timeout = min(self.llm_config.get("timeout", 60), 30)
        try:
            resp = await client.post(
                url, headers=headers,
                data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                timeout=fc_timeout,
            )
            if resp.status_code != 200:
                logger.error(f"[chat_with_tools] HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            out = resp.json()
            msg = out.get("choices", [{}])[0].get("message", {})
            return {
                "content":    msg.get("content") or "",
                "tool_calls": msg.get("tool_calls") or [],
            }
        except Exception as e:
            logger.error(f"[chat_with_tools] 调用失败: {e}")
            return None


# 全局LLM服务实例
llm_service = LLMService()
