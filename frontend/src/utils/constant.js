import { getSystemEnvironment } from "ling-yun-methods";
import { name as appName } from "../../package.json";

/**
 * 灵运 / ling-yun-methods：WujieRouter、KeepAlive 等使用的环境段，形如 `znhs-gray`（无前导 /）。
 * 与官方文档「getSystemEnvironment + VITE_APP_PREFIX」一致。
 */
export const systemEnvironment = getSystemEnvironment(
  appName,
  import.meta.env.VITE_SUB_APP_ENVIRONMENT
    ? import.meta.env.VITE_APP_PREFIX
    : ""
);

/**
 * Vue Router `createWebHistory` 的 base，必须与 Vite `base`（即 import.meta.env.BASE_URL）一致。
 * 灵运文档写 `createWebHistory(systemEnvironment)`：在无 `VITE_PLATFORM_PATH_PREFIX` 时 BASE_URL 规范化后等价于 `/znhs-gray`；
 * 若主应用挂载为 `/lingyun-platform-gray/znhs-gray/`，则须用完整 BASE_URL，不能仅用 systemEnvironment。
 */
export const routerHistoryBase = import.meta.env.BASE_URL;
