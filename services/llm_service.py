"""
大模型调用服务
基于现有llm_dialogue.py代码改造，支持营销话术推荐场景
"""
import json
import time
import random
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
import httpx
from loguru import logger
from utils.config_loader import config_loader
from utils.observability import add_degrade_flag, record_stage, get_request_context, get_trace_id
from utils import province_logger


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
    ) -> str:
        """调用大模型生成内容。

        Qwen3 系列模型内置思考模式，需通过 enable_thinking=false 关闭，
        否则会先输出大段推理再给答案，且 max_tokens 不足时被截断后永远输不出话术。
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

        # 关闭思考模式：不同模型厂商参数不同
        # - Qwen3/DashScope: chat_template_kwargs.enable_thinking=false
        # - Qwen3/九天MOMA(vLLM): enable_thinking=false（顶层 OpenAI 兼容参数）
        # - DeepSeek: reasoning_effort="none" 或 thinking={"type":"disabled"}（部分版本支持）
        url_str = str(self.llm_config.get("url", ""))
        model_str = str(self.llm_config.get("model", "") or self.llm_config.get("model_id", ""))
        if "deepseek" in url_str.lower() or "deepseek" in model_str.lower():
            # DeepSeek 推理模型无法完全关闭思考，reasoning_effort 合法值仅 high/low/medium/max/xhigh，
            # 取 low 以最小化思考链 token 占用（none 会 400）
            data["reasoning_effort"] = "low"
        else:
            # Qwen3 / DashScope 系列：同时发顶层和 chat_template_kwargs 两层参数
            # - 九天MOMA / vLLM 等 OpenAI 兼容平台读顶层 enable_thinking
            # - DashScope 原生平台读 chat_template_kwargs.enable_thinking
            data["enable_thinking"] = False
            data["chat_template_kwargs"] = {"enable_thinking": False}

        last_error = None
        start_time = time.perf_counter()
        client = await self._get_client()
        for attempt in range(max_retries):
            try:
                logger.info(f"🤖 调用大模型: {stage} (尝试 {attempt + 1}/{max_retries})")
                response = await client.post(
                    url,
                    headers=headers,
                    data=json.dumps(data),
                    timeout=timeout,
                )

                if response.status_code != 200:
                    error_msg = f"HTTP {response.status_code}: {response.text[:500]}"
                    logger.error(f"❌ 大模型API返回错误状态码: {error_msg}")
                    raise Exception(error_msg)

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
                    content = self._clean_llm_output(raw_content)
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
                    )
                    return content
                else:
                    logger.error(f"大模型返回格式异常: {output}")
                    last_error = "返回格式异常"

            except httpx.TimeoutException as e:
                logger.error(f"❌ 大模型调用超时 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_error = f"调用超时: {e}"
                if attempt < max_retries - 1:
                    await self._wait_before_retry(attempt)

            except Exception as e:
                logger.error(f"❌ 大模型调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_error = f"调用失败: {e}"
                if attempt < max_retries - 1:
                    await self._wait_before_retry(attempt)

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
        )
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
    ) -> None:
        """写分省大模型日志：province 缺省时回退请求上下文；trace_id/intent 从上下文取。"""
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
            )
        except Exception:
            pass

    @staticmethod
    def _clean_llm_output(raw: str) -> str:
        """清洗模型输出，只保留正式话术正文。

        - 去除 <think>...</think> 思考块（开启思考模式时 Qwen3 会输出）
        - 去除残留 Markdown 格式符号
        - 去除「话术：」前缀
        - 不含中文视为异常，返回空
        """
        import re

        text = raw.strip()
        if not text:
            return ""

        # 去 <think>...</think> 思考块（开启思考模式时可能出现）
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # 去 Markdown 格式符号（加粗/标题）
        text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

        # 去「话术：」前缀
        text = re.sub(r"^话术[：:]\s*", "", text).strip()

        # 不含中文且不像 JSON/数组 → 异常响应，置空
        # 接口映射等场景 LLM 直接返回纯 JSON，不含中文属正常，不应置空
        if text and not re.search(r"[\u4e00-\u9fff]", text):
            if not re.search(r"[\{\[]", text):
                logger.warning(f"⚠️ LLM 输出不含中文且非JSON，疑似异常响应，置空: {text[:80]}")
                return ""

        return text.strip()

    async def _wait_before_retry(self, attempt: int):
        """重试前等待（指数退避）"""
        wait_time = min(2 ** attempt, 10)
        logger.info(f"⏳ 等待 {wait_time} 秒后重试...")
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
            "enable_thinking": False,
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
