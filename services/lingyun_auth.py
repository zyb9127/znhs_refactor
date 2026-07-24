"""
灵运平台后端鉴权接入模块

鉴权流程：
1. 灵运主应用登录后，将 satoken 放入 Cookie 传递给子应用前端
2. 子应用前端请求子应用后端时，将 satoken 传递到后端
3. 子应用后端携带 satoken 请求灵运平台鉴权接口
4. 鉴权成功返回用户信息，缓存指定时长（默认 2 小时）；失败抛出异常

退出登录流程：
- 消费 Kafka 消息，根据 satoken 清除本地缓存

FastAPI 集成：
    # 推荐：直接传 client_env，中间件内部自动创建 LingyunAuthClient
    app.add_middleware(
        LingyunAuthMiddleware,
        client_env="gray",
        skip_prefixes=["/marketing/", "/health"],
    )

    # 或传入已有 client
    client = LingyunAuthClient(env="prod")
    app.add_middleware(LingyunAuthMiddleware, auth_client=client,
                       exclude_paths={"/health"}, exclude_prefixes={"/static"})

    # 按路由声明（Depends）
    get_current_user = make_lingyun_depends(client)

    @app.get("/api/me")
    def me(user: LingyunUser = Depends(get_current_user)):
        return {"username": user.user_info.username}
"""

from __future__ import annotations

import asyncio
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

import requests

__all__ = [
    # 核心客户端
    "LingyunAuthClient",
    # 数据结构
    "LingyunUser",
    "UserInfo",
    "RoleInfo",
    # 异常
    "LingyunAuthError",
    "AuthenticationError",
    "LingyunAPIError",
    # 工具函数
    "get_satoken_from_request_header",
    "handle_logout_kafka_message",
    # FastAPI 中间件（需安装 fastapi）
    "LingyunAuthMiddleware",
    "GrayApiAliasMiddleware",
    "GraySpaIngressRestoreMiddleware",
]

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 内部默认配置
# ──────────────────────────────────────────────

_BASE_URL_GRAY = "http://bigmodel.zhiduo.cmos:8080/gray"
_BASE_URL_PROD = "http://bigmodel.zhiduo.cmos:8080"
_AUTH_PATH = "/cslm-manager-Oauth/children/user/getUserInfo"
_DEFAULT_CACHE_TTL = 2 * 60 * 60  # 2 小时
_SUCCESS_CODE = "0000"


# ──────────────────────────────────────────────
# 自定义异常（优先定义，供全文引用）
# ──────────────────────────────────────────────

class LingyunAuthError(Exception):
    """灵运鉴权基础异常"""


class AuthenticationError(LingyunAuthError):
    """satoken 无效或未登录"""


class LingyunAPIError(LingyunAuthError):
    """灵运平台接口调用异常"""


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class UserInfo:
    id: str
    username: str
    phone: str
    dept_id: str
    dept_name: str
    png_url: str


@dataclass
class RoleInfo:
    role_id: int
    role_name: str


@dataclass
class LingyunUser:
    user_info: UserInfo
    role_list: List[RoleInfo] = field(default_factory=list)

    def has_role(self, role_name: str) -> bool:
        """判断用户是否拥有指定角色名（大小写敏感）。"""
        return any(r.role_name == role_name for r in self.role_list)

    def has_role_id(self, role_id: int) -> bool:
        """判断用户是否拥有指定角色 ID。"""
        return any(r.role_id == role_id for r in self.role_list)

    @property
    def role_names(self) -> List[str]:
        """返回所有角色名称列表。"""
        return [r.role_name for r in self.role_list]


# ──────────────────────────────────────────────
# 内部：线程安全本地缓存
# ──────────────────────────────────────────────

@dataclass
class _CacheEntry:
    user: LingyunUser
    expire_at: float  # time.monotonic() 时间戳


class _TokenCache:
    """satoken -> LingyunUser 的线程安全内存缓存。"""

    def __init__(self, ttl: int = _DEFAULT_CACHE_TTL):
        self._ttl = ttl
        self._store: Dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, satoken: str) -> Optional[LingyunUser]:
        with self._lock:
            entry = self._store.get(satoken)
            if entry is None:
                return None
            if time.monotonic() > entry.expire_at:
                del self._store[satoken]
                return None
            return entry.user

    def set(self, satoken: str, user: LingyunUser) -> None:
        with self._lock:
            self._store[satoken] = _CacheEntry(
                user=user,
                expire_at=time.monotonic() + self._ttl,
            )

    def delete(self, satoken: str) -> bool:
        with self._lock:
            return self._store.pop(satoken, None) is not None

    def clear(self) -> int:
        """清空所有缓存，返回被清除的条目数。"""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count


# ──────────────────────────────────────────────
# 内部：响应解析
# ──────────────────────────────────────────────

def _parse_user(data: dict) -> LingyunUser:
    ui = data.get("userInfo", {})
    user_info = UserInfo(
        id=ui.get("id", ""),
        username=ui.get("username", ""),
        phone=ui.get("phone", ""),
        dept_id=ui.get("deptId", ""),
        dept_name=ui.get("deptName", ""),
        png_url=ui.get("pngUrl", ""),
    )
    role_list = [
        RoleInfo(role_id=r.get("roleId", 0), role_name=r.get("roleName", ""))
        for r in data.get("roleList", [])
    ]
    return LingyunUser(user_info=user_info, role_list=role_list)


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def get_satoken_from_request_header(headers: dict) -> str:
    """
    从请求头中提取 satoken（对键名大小写不敏感）。

    Raises:
        AuthenticationError: 请求头中不包含 satoken
    """
    lower = {k.lower(): v for k, v in headers.items()}
    satoken = lower.get("satoken")
    if not satoken:
        raise AuthenticationError("请求头中缺少 satoken")
    return satoken


# ──────────────────────────────────────────────
# 同步鉴权客户端
# ──────────────────────────────────────────────

class LingyunAuthClient:
    """
    灵运平台鉴权客户端。

    每个实例拥有独立的缓存，不同实例之间不会相互干扰。

    Args:
        env:       运行环境，"gray"（灰度）或 "prod"（生产）。
        timeout:   HTTP 请求超时秒数，默认 5。
        cache_ttl: 用户信息本地缓存时长（秒），默认 7200（2 小时）。
        base_url:  自定义完整基础 URL（优先级高于 env）。

    示例：
        with LingyunAuthClient(env="prod") as client:
            user = client.verify(satoken)
            if user.has_role("管理员"):
                ...
    """

    def __init__(
        self,
        env: str = "prod",
        timeout: int = 5,
        cache_ttl: int = _DEFAULT_CACHE_TTL,
        base_url: Optional[str] = None,
    ):
        if base_url:
            self._url = base_url.rstrip("/") + _AUTH_PATH
        else:
            _base = _BASE_URL_GRAY if env == "gray" else _BASE_URL_PROD
            self._url = _base + _AUTH_PATH

        self._timeout = timeout
        self._session = requests.Session()
        self._cache = _TokenCache(ttl=cache_ttl)

    # ── 公开接口 ──────────────────────────────

    def verify(self, satoken: str) -> LingyunUser:
        """
        验证 satoken 并返回用户信息。

        优先从本地缓存读取；缓存未命中时请求灵运平台，
        鉴权成功后将结果缓存 cache_ttl 秒。

        Raises:
            AuthenticationError: satoken 为空、无效或已过期
            LingyunAPIError:     灵运平台接口网络 / 超时异常
        """
        if not satoken:
            raise AuthenticationError("satoken 不能为空")

        cached = self._cache.get(satoken)
        if cached is not None:
            logger.debug("命中缓存: satoken=%.8s...", satoken)
            return cached

        user = self._call_api(satoken)
        self._cache.set(satoken, user)
        logger.info("鉴权成功，用户 %s 已缓存", user.user_info.username)
        return user

    def revoke_cache(self, satoken: str) -> bool:
        """
        手动清除指定 satoken 的本地缓存（退出登录 / 角色变更场景）。

        Returns:
            True 表示确实删除了缓存条目，False 表示本就不存在。
        """
        removed = self._cache.delete(satoken)
        if removed:
            logger.info("已清除缓存: satoken=%.8s...", satoken)
        return removed

    def clear_all_cache(self) -> int:
        """清空所有已缓存的用户信息，返回清除条目数。"""
        count = self._cache.clear()
        logger.info("已清空全部缓存，共 %d 条", count)
        return count

    def close(self) -> None:
        """释放底层 HTTP 连接池。"""
        self._session.close()

    def __enter__(self) -> "LingyunAuthClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── 内部方法 ──────────────────────────────

    def _call_api(self, satoken: str) -> LingyunUser:
        try:
            resp = self._session.post(
                self._url,
                headers={"satoken": satoken},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.Timeout:
            raise LingyunAPIError(f"灵运鉴权接口请求超时（>{self._timeout}s）")
        except requests.RequestException as exc:
            raise LingyunAPIError(f"灵运鉴权接口请求失败: {exc}") from exc

        body = resp.json()
        code = body.get("code")
        if code != _SUCCESS_CODE:
            msg = body.get("msg", "未知错误")
            raise AuthenticationError(f"鉴权失败（code={code}）: {msg}")

        return _parse_user(body["data"])


# ──────────────────────────────────────────────
# Kafka 消费者（退出登录 / 角色变更）
# ──────────────────────────────────────────────

def handle_logout_kafka_message(message: dict, client: LingyunAuthClient) -> None:
    """
    处理灵运平台推送的退出登录 / 角色变更 Kafka 消息。

    消息格式：
        {"satoken": "...", "operation_type": 1}
        operation_type: 1=退出登录  2=修改角色

    用法：
        for msg in kafka_consumer:
            handle_logout_kafka_message(json.loads(msg.value), client)
    """
    satoken = message.get("satoken", "")
    op_type = message.get("operation_type")

    if not satoken:
        logger.warning("[Kafka] 消息缺少 satoken，跳过")
        return

    if op_type in (1, 2):
        removed = client.revoke_cache(satoken)
        action = "退出登录" if op_type == 1 else "修改角色"
        logger.info("[Kafka] %s 消息处理完成，缓存%s清除",
                    action, "已" if removed else "未（不存在）")
    else:
        logger.warning("[Kafka] 未知 operation_type=%s，跳过", op_type)


# ──────────────────────────────────────────────
# FastAPI 集成（可选，需安装 fastapi / starlette）
# ──────────────────────────────────────────────

try:
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware

    class LingyunAuthMiddleware(BaseHTTPMiddleware):
        """
        FastAPI/Starlette 中间件：对所有请求自动进行灵运鉴权。

        鉴权调用在线程池中执行，不会阻塞 asyncio 事件循环。

        支持按请求路径前缀选择不同的鉴权客户端，用于同一进程同时服务
        灰度（/znhs-gray/）和生产（/znhs/）时使用不同鉴权地址的场景。

        Args:
            auth_client:      LingyunAuthClient 实例（与 client_env 二选一）。
            client_env:       "gray" 或 "prod"，自动创建 LingyunAuthClient（与 auth_client 二选一）。
            exclude_paths:    精确路径白名单，如 {"/health", "/docs", "/openapi.json"}。
            exclude_prefixes: 前缀路径白名单，如 {"/static", "/public"}。
            skip_prefixes:    同 exclude_prefixes（兼容别名）。
            prefix_clients:   按路径前缀选择不同鉴权客户端的映射，格式：
                              {"/znhs/": LingyunAuthClient(env="prod"),
                               "/znhs-gray/": LingyunAuthClient(env="gray")}
                              匹配时按前缀从长到短优先，未匹配则回退到 auth_client / client_env。

        用法（推荐，单环境）：
            app.add_middleware(
                LingyunAuthMiddleware,
                client_env="gray",
                skip_prefixes=["/marketing/", "/health"],
            )

        用法（多环境，同进程服务灰度+生产）：
            app.add_middleware(
                LingyunAuthMiddleware,
                client_env="gray",          # 默认客户端（兜底）
                skip_prefixes=["/znhs/marketing/", "/health"],
                prefix_clients={
                    "/znhs/": LingyunAuthClient(env="prod"),
                    "/znhs-gray/": LingyunAuthClient(env="gray"),
                },
            )
        """

        def __init__(
            self,
            app,
            auth_client: Optional[LingyunAuthClient] = None,
            client_env: str = "prod",
            exclude_paths: Optional[Set[str]] = None,
            exclude_prefixes: Optional[Set[str]] = None,
            skip_prefixes: Optional[List[str]] = None,
            prefix_clients: Optional[Dict[str, "LingyunAuthClient"]] = None,
        ):
            super().__init__(app)
            self._client = auth_client or LingyunAuthClient(env=client_env)
            self._exclude_paths: Set[str] = set(exclude_paths or [])
            # skip_prefixes 是 exclude_prefixes 的别名，合并处理
            _prefixes: Set[str] = set(exclude_prefixes or [])
            _prefixes.update(skip_prefixes or [])
            self._exclude_prefixes = _prefixes
            # 按路径前缀选择客户端：按前缀长度降序排列，优先匹配最长前缀
            self._prefix_clients: List[tuple] = sorted(
                (prefix_clients or {}).items(),
                key=lambda x: len(x[0]),
                reverse=True,
            )

        def _is_excluded(self, path: str) -> bool:
            if path in self._exclude_paths:
                return True
            return any(path.startswith(p) for p in self._exclude_prefixes)

        def _get_client(self, path: str) -> "LingyunAuthClient":
            """根据请求路径选择对应的鉴权客户端。"""
            for prefix, client in self._prefix_clients:
                if path.startswith(prefix):
                    return client
            return self._client

        async def dispatch(self, request: Request, call_next):
            if self._is_excluded(request.url.path):
                return await call_next(request)

            # 优先从请求头取 satoken，其次从 Cookie 取（兼容灵运平台 Cookie 传递方式）
            satoken = (
                request.headers.get("satoken", "")
                or request.cookies.get("satoken", "")
            )
            if not satoken:
                logger.debug("鉴权失败：请求头和 Cookie 中均未找到 satoken，path=%s", request.url.path)

            # 按路径选择对应环境的鉴权客户端
            client = self._get_client(request.url.path)
            try:
                # 在线程池中运行同步的 verify，避免阻塞事件循环
                user = await asyncio.to_thread(client.verify, satoken)
                request.state.lingyun_user = user
            except AuthenticationError as exc:
                return JSONResponse(
                    status_code=401,
                    content={"code": "4010", "msg": str(exc)},
                )
            except LingyunAPIError as exc:
                logger.error("灵运平台鉴权接口异常: %s", exc)
                return JSONResponse(
                    status_code=502,
                    content={"code": "5020", "msg": "鉴权服务暂时不可用，请稍后重试"},
                )
            return await call_next(request)

    class GrayApiAliasMiddleware:
        """将灰度 API 别名前缀（如 /gray）重写为主服务前缀（如 /znhs-gray）。

        纯 ASGI 中间件实现，比 BaseHTTPMiddleware 更可靠地完成 scope["path"] 重写。
        用于灰度前端通过 /gray/api/... 访问后端 API 时，本地无 Ingress 剥前缀的场景。

        Args:
            gray_prefix:   灰度 API 前缀，如 /gray
            target_prefix: 目标服务前缀，如 /znhs-gray
        """

        def __init__(self, app, gray_prefix: str, target_prefix: str):
            self.app = app
            self._gray = gray_prefix.rstrip("/")
            self._target = target_prefix.rstrip("/")

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                path = scope.get("path", "")
                if path.startswith(self._gray + "/") or path == self._gray:
                    new_path = self._target + path[len(self._gray):]
                    # 浅拷贝 scope 以避免影响上层中间件持有的引用
                    scope = dict(scope)
                    scope["path"] = new_path
                    scope["raw_path"] = new_path.encode("utf-8")
            await self.app(scope, receive, send)

    class GraySpaIngressRestoreMiddleware(BaseHTTPMiddleware):
        """灰度 SPA Ingress 路径补偿中间件。

        当 Ingress 剥除了灰度前缀（如 /znhs-gray）后，
        前端 SPA 的 HTML 请求会以 /znhs/... 到达后端，
        导致返回主站 index.html 而非灰度 index.html。

        此中间件检测 Referer 头，若来自灰度 UI 前缀，
        则将请求路径重写回灰度前缀，确保返回灰度 SPA。

        Args:
            gray_ui_prefix:  灰度 UI 前缀，如 /znhs-gray
            service_prefix:  主服务前缀，如 /znhs
        """

        def __init__(self, app, gray_ui_prefix: str, service_prefix: str):
            super().__init__(app)
            self._gray = gray_ui_prefix.rstrip("/")
            self._svc = service_prefix.rstrip("/")

        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            referer = request.headers.get("referer", "")
            # 仅对 SPA 页面请求（非 API / 静态资源）做补偿
            is_api = any(path.startswith(p) for p in ("/api/", "/assets/", "/static/"))
            if (
                not is_api
                and self._gray
                and self._svc
                and path.startswith(self._svc + "/")
                and self._gray in referer
            ):
                new_path = self._gray + path[len(self._svc):]
                request.scope["path"] = new_path
                request.scope["raw_path"] = new_path.encode()
            return await call_next(request)

    def make_lingyun_depends(client: LingyunAuthClient) -> Callable:
        """
        生成 FastAPI Depends 依赖函数，直接在路由签名中注入已鉴权用户。

        相比中间件，Depends 模式更精细：可只在需要鉴权的路由上声明，
        且可在 OpenAPI 文档中体现认证信息。

        用法：
            client = LingyunAuthClient(env="prod")
            get_current_user = make_lingyun_depends(client)

            @app.get("/api/me")
            def me(user: LingyunUser = Depends(get_current_user)):
                return {"username": user.user_info.username}
        """
        def get_current_user(request: Request) -> LingyunUser:
            satoken = request.headers.get("satoken", "")
            try:
                return client.verify(satoken)
            except AuthenticationError as exc:
                raise HTTPException(status_code=401, detail=str(exc))
            except LingyunAPIError as exc:
                logger.error("灵运平台鉴权接口异常: %s", exc)
                raise HTTPException(status_code=502, detail="鉴权服务暂时不可用，请稍后重试")

        return get_current_user

except ImportError:
    pass  # FastAPI 未安装时跳过，不影响其他功能


# ──────────────────────────────────────────────
# 快速测试入口
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(message)s")

    with LingyunAuthClient(env="gray", timeout=5) as auth_client:
        test_satoken = "your-satoken-here"  # 替换为真实 satoken

        try:
            user = auth_client.verify(test_satoken)
            print("鉴权成功：")
            print(json.dumps({
                "id": user.user_info.id,
                "username": user.user_info.username,
                "phone": user.user_info.phone,
                "deptId": user.user_info.dept_id,
                "deptName": user.user_info.dept_name,
                "roles": [{"roleId": r.role_id, "roleName": r.role_name}
                          for r in user.role_list],
            }, ensure_ascii=False, indent=2))
            print("角色列表:", user.role_names)
        except AuthenticationError as e:
            print(f"鉴权失败: {e}")
        except LingyunAPIError as e:
            print(f"接口异常: {e}")

        kafka_msg = {"satoken": test_satoken, "operation_type": 1}
        handle_logout_kafka_message(kafka_msg, auth_client)
