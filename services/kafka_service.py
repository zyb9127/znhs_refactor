"""
灵运平台 Kafka 集成服务

功能：
1. 消费退出登录/角色变更消息 → 清除本地鉴权缓存
2. 生产操作日志消息 → 发送到灵运平台操作日志 Topic

环境变量：
    LINGYUN_ENV            gray | prod（默认 gray）
    KAFKA_BOOTSTRAP        Kafka 地址（默认 kafka-ly-zxfz-svc.zjjpt-kafka.svc.ly.armdual.hpc:9092）
    KAFKA_SERVICE_NAME     本服务名称，用于 GroupId 后缀（默认 marketing-agent）
    KAFKA_ENABLED          是否启用 Kafka（gray/prod 默认 true；development 默认 false；可显式覆盖）


Topic 规则（灰度/生产自动切换）：
    退出登录消费：topic_lingyun_logout_gray / topic_lingyun_logout
    操作日志生产：topic_lingyun_operationLog_gray / topic_lingyun_operationLog
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from loguru import logger

# ── 环境配置 ──────────────────────────────────────────────────

# 与 utils.env_config 保持一致：清洗误带入的分隔符/引号（如全角 '；' 前缀）
_ENV              = os.getenv("LINGYUN_ENV", "gray").strip().strip(";；'\"，, ").strip().lower()  # gray | prod
_KAFKA_BOOTSTRAP  = os.getenv(
    "KAFKA_BOOTSTRAP",
    "kafka-ly-zxfz-svc.zjjpt-kafka.svc.ly.armdual.hpc:9092",
)
_SERVICE_NAME     = os.getenv("KAFKA_SERVICE_NAME", "marketing-agent")
# 生产/灰度环境默认启用 Kafka；development 环境默认关闭
# 可通过 KAFKA_ENABLED=true/false 显式覆盖
_KAFKA_ENABLED_DEFAULT = "false" if _ENV == "development" else "true"
_KAFKA_ENABLED    = os.getenv("KAFKA_ENABLED", _KAFKA_ENABLED_DEFAULT).lower() == "true"


# Topic 名称
_IS_GRAY = _ENV == "gray"
TOPIC_LOGOUT      = "topic_lingyun_logout_gray"      if _IS_GRAY else "topic_lingyun_logout"
TOPIC_OP_LOG      = "topic_lingyun_operationLog_gray" if _IS_GRAY else "topic_lingyun_operationLog"
GROUP_LOGOUT      = f"group_lingyun_logout_{_SERVICE_NAME}"
GROUP_OP_LOG      = "group_lingyun_operationLog"


# ── 操作日志生产者 ─────────────────────────────────────────────

class KafkaOperationLogProducer:
    """
    向灵运平台发送操作日志的 Kafka 生产者。

    消息格式：
        {
            "satoken": "...",
            "operation_type": "add|update|delete|select|export|import",
            "operation_name": "操作概述",
            "operation_content": "操作详情",
            "operation_status": 0,   # 0=成功 1=失败
            "interface_url": "接口地址"
        }
    """

    def __init__(self):
        self._producer = None
        self._enabled = _KAFKA_ENABLED
        if self._enabled:
            self._init_producer()

    def _init_producer(self):
        try:
            from kafka import KafkaProducer
            self._producer = KafkaProducer(
                bootstrap_servers=_KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                retries=3,
                request_timeout_ms=5000,
            )
            logger.info(f"[Kafka] 操作日志生产者已连接: {_KAFKA_BOOTSTRAP}")
        except ImportError:
            logger.warning("[Kafka] kafka-python 未安装，操作日志功能不可用")
            self._enabled = False
        except Exception as e:
            logger.warning(f"[Kafka] 操作日志生产者初始化失败（不影响主服务）: {e}")
            self._enabled = False

    def send_operation_log(
        self,
        satoken: str,
        operation_type: str,
        operation_name: str,
        operation_content: str,
        interface_url: str,
        operation_status: int = 0,
    ) -> None:
        """
        异步发送操作日志（fire-and-forget，失败只记录警告不抛出异常）。

        Args:
            satoken:           用户 satoken
            operation_type:    操作类型：select/export/import/add/update/delete
            operation_name:    操作概述，如"话术模板列表查询"
            operation_content: 操作详情，如"admin 对话术模板进行查询操作"
            interface_url:     接口地址
            operation_status:  0=成功 1=失败
        """
        if not self._enabled or self._producer is None:
            return

        message = {
            "satoken":           satoken,
            "operation_type":    operation_type,
            "operation_name":    operation_name,
            "operation_content": operation_content,
            "operation_status":  operation_status,
            "interface_url":     interface_url,
        }
        try:
            self._producer.send(TOPIC_OP_LOG, value=message)
            logger.debug(f"[Kafka] 操作日志已发送: {operation_name}")
        except Exception as e:
            logger.warning(f"[Kafka] 操作日志发送失败（不影响主服务）: {e}")

    def close(self):
        if self._producer:
            try:
                self._producer.flush(timeout=3)
                self._producer.close()
            except Exception:
                pass


# ── 退出登录消费者 ─────────────────────────────────────────────

class KafkaLogoutConsumer:
    """
    消费灵运平台退出登录/角色变更消息，清除本地鉴权缓存。

    在后台线程中运行，不阻塞主服务。
    """

    def __init__(self, auth_client):
        """
        Args:
            auth_client: LingyunAuthClient 实例，用于清除缓存
        """
        self._auth_client = auth_client
        self._consumer = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._enabled = _KAFKA_ENABLED

    def start(self):
        """启动后台消费线程。"""
        if not self._enabled:
            logger.info("[Kafka] KAFKA_ENABLED=false，退出登录消费者未启动")
            return
        if not self._init_consumer():
            return
        self._thread = threading.Thread(
            target=self._consume_loop,
            name="kafka-logout-consumer",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[Kafka] 退出登录消费者已启动，Topic={TOPIC_LOGOUT}, Group={GROUP_LOGOUT}")

    def stop(self):
        """停止消费线程。"""
        self._stop_event.set()
        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                pass

    def _init_consumer(self) -> bool:
        try:
            from kafka import KafkaConsumer
            self._consumer = KafkaConsumer(
                TOPIC_LOGOUT,
                bootstrap_servers=_KAFKA_BOOTSTRAP,
                group_id=GROUP_LOGOUT,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                auto_offset_reset="latest",
                enable_auto_commit=True,
                consumer_timeout_ms=1000,  # 每秒检查一次 stop_event
            )
            return True
        except ImportError:
            logger.warning("[Kafka] kafka-python 未安装，退出登录消费者不可用")
            self._enabled = False
            return False
        except Exception as e:
            logger.warning(f"[Kafka] 退出登录消费者初始化失败（不影响主服务）: {e}")
            self._enabled = False
            return False

    def _consume_loop(self):
        from services.lingyun_auth import handle_logout_kafka_message
        logger.info("[Kafka] 退出登录消费循环已启动")
        while not self._stop_event.is_set():
            try:
                for message in self._consumer:
                    if self._stop_event.is_set():
                        break
                    try:
                        handle_logout_kafka_message(message.value, self._auth_client)
                    except Exception as e:
                        logger.warning(f"[Kafka] 处理退出登录消息异常: {e}")
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"[Kafka] 消费循环异常，5秒后重试: {e}")
                    time.sleep(5)
        logger.info("[Kafka] 退出登录消费循环已停止")


# ── 单例 ──────────────────────────────────────────────────────

# 全局操作日志生产者（在 main.py lifespan 中初始化）
op_log_producer: Optional[KafkaOperationLogProducer] = None
logout_consumer: Optional[KafkaLogoutConsumer] = None


def init_kafka(auth_client) -> None:
    """初始化 Kafka 服务（在 lifespan startup 中调用）。"""
    global op_log_producer, logout_consumer

    op_log_producer = KafkaOperationLogProducer()

    logout_consumer = KafkaLogoutConsumer(auth_client)
    logout_consumer.start()


def shutdown_kafka() -> None:
    """关闭 Kafka 服务（在 lifespan shutdown 中调用）。"""
    global op_log_producer, logout_consumer

    if logout_consumer:
        logout_consumer.stop()
    if op_log_producer:
        op_log_producer.close()


def send_op_log(
    request,
    operation_type: str,
    operation_name: str,
    operation_content: str,
    operation_status: int = 0,
) -> None:
    """
    便捷函数：从 FastAPI Request 中提取 satoken，发送操作日志。

    在路由处理函数中调用，失败不影响主流程。
    """
    if op_log_producer is None:
        return
    satoken = request.headers.get("satoken", "")
    if not satoken:
        return
    op_log_producer.send_operation_log(
        satoken=satoken,
        operation_type=operation_type,
        operation_name=operation_name,
        operation_content=operation_content,
        interface_url=str(request.url.path),
        operation_status=operation_status,
    )
