/**
 * 灵运文档：WujieRouter(systemEnvironment, import.meta.env)。
 * 文档示例 `from "../src/utils/constant"` 路径有误，子应用内应使用 `@/utils/constant`。
 */
import { WujieRouter } from "ling-yun-methods";
import { systemEnvironment } from "@/utils/constant";

export const useWujieRouter = () =>
  WujieRouter(systemEnvironment, import.meta.env);
