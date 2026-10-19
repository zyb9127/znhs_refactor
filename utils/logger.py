"""
日志管理器
使用 loguru 实现统一的日志管理

【设计说明】
_setup_logger() 静默执行（不打 INFO 日志）。
"✅ 日志系统初始化完成" 统一由 main.py lifespan 输出，
避免 uvicorn --reload 模式下 reloader 进程与 worker 进程
各自导入模块时重复打印同一条初始化消息。
"""
from loguru import logger
from pathlib import Path
import sys


class LoggerManager:
    """日志管理器（进程内单例）"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._setup_logger()
            LoggerManager._initialized = True

    def _setup_logger(self):
        """配置日志 handler（静默执行，不打 INFO 日志）

        初始化成功日志由 main.py lifespan 统一输出，
        避免 uvicorn --reload 的 reloader/worker 双进程重复打印。
        """
        logger.remove()

        # 控制台输出（始终保留：容器云通过 stdout 采集日志，与本地文件留存开关无关）
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level="INFO",
            colorize=True,
        )

        # 本地日志文件：受 logging.local_file_enabled / 环境变量 ZNHS_LOCAL_LOG 控制。
        # 关闭后完全不落本地文件（仅保留上面的 stdout），降低敏感信息本地留存风险。
        if self._local_file_enabled():
            log_dir = Path(__file__).parent.parent / "logs"
            log_dir.mkdir(exist_ok=True)

            # 普通日志文件
            logger.add(
                log_dir / "app_{time:YYYY-MM-DD}.log",
                rotation="00:00",
                retention="30 days",
                level="INFO",
                encoding="utf-8",
                format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            )

            # 错误日志文件
            logger.add(
                log_dir / "error_{time:YYYY-MM-DD}.log",
                rotation="00:00",
                retention="30 days",
                level="ERROR",
                encoding="utf-8",
                format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            )
        # ⚠️ 不在此处打 INFO — lifespan 统一打印

    @staticmethod
    def _local_file_enabled() -> bool:
        """本地日志文件留存开关；读取失败一律回退 True（不因配置异常丢日志）。"""
        try:
            from utils.config_loader import local_log_enabled
            return local_log_enabled()
        except Exception:
            return True

    def get_logger(self):
        return logger


# 全局日志管理器实例（模块导入时静默完成 handler 注册）
log_manager = LoggerManager()
app_logger = log_manager.get_logger()


def setup_logger() -> None:
    """模块级快捷初始化函数（供 main.py 调用）
    LoggerManager 是单例，重复调用无副作用。
    不打印任何日志，由调用方（lifespan）决定是否输出初始化成功消息。
    """
    LoggerManager()
