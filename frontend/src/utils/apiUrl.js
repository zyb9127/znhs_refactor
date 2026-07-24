/**
 * 根据 BASE_URL 拼接 API 请求路径，三个环境统一使用同一套逻辑。
 *
 * BASE_URL 由 vite.config.js 的 base（resolveAppPublicBase）决定：
 *
 *   dev   (VITE_APP_PREFIX=gray)  → BASE_URL=/znhs-gray/
 *           apiUrl('/api/skills') → '/znhs-gray/api/skills'
 *           Vite proxy 将 /znhs-gray/... 直接透传到 127.0.0.1:8000
 *           后端 SERVICE_PREFIX=/znhs-gray，路径完全匹配，无需 rewrite
 *
 *   gray  (VITE_APP_PREFIX=gray)  → BASE_URL=/znhs-gray/
 *           apiUrl('/api/skills') → '/znhs-gray/api/skills'
 *           部署后网关路由到后端，路径与 dev 完全一致
 *
 *   prod  (VITE_APP_PREFIX='')    → BASE_URL=/znhs/
 *           apiUrl('/api/skills') → '/znhs/api/skills'
 *           后端 SERVICE_PREFIX=/znhs
 *
 * 开发与灰度唯一区别：鉴权（灰度开启，开发关闭），API 路径完全一致。
 */

const _base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')

/**
 * @param {string} path - 以 / 开头的 API 路径，如 '/api/skills'
 * @returns {string} 带环境前缀的完整路径
 *
 * dev/gray:  apiUrl('/api/skills') → '/znhs-gray/api/skills'
 * prod:      apiUrl('/api/skills') → '/znhs/api/skills'
 */
export function apiUrl(path) {
  return `${_base}${path}`
}

/**
 * 从 Cookie 中读取 satoken。
 *
 * 灵运平台鉴权流程（文档 7.1）：
 *   1. 主应用登录后将 satoken 放入 Cookie
 *   2. 子应用前端从 Cookie 读取 satoken，放入请求 header 传递给后端
 *   3. 后端携带 satoken 请求灵运平台鉴权接口
 */
function resolveSatoken() {
  return document.cookie
    .split('; ')
    .find(row => row.startsWith('satoken='))
    ?.split('=')[1] || ''
}

/**
 * 带环境前缀的 fetch 封装，自动从 Cookie 读取 satoken 注入请求头。
 *
 * @param {string} path - 以 / 开头的 API 路径，如 '/api/user/me'
 * @param {RequestInit} [options]
 * @returns {Promise<Response>}
 */
export function apiFetch(path, options) {
  const satoken = resolveSatoken()
  const merged = {
    credentials: 'include',
    ...options,
    headers: {
      ...(options?.headers || {}),
      ...(satoken ? { satoken } : {}),
    },
  }
  return fetch(apiUrl(path), merged)
}

/**
 * 营销推荐接口路径（实时对外服务，固定 /znhs 前缀，与环境 BASE_URL 无关）
 * 默认 dev/gray/prod 均为 /znhs/marketing/recommend
 */
export function marketingRecommendUrl() {
  return import.meta.env.VITE_MARKETING_RECOMMEND_PATH || '/znhs/marketing/recommend'
}

/**
 * 营销推荐 fetch 封装（路径固定 /znhs/marketing/recommend，仍注入 satoken）
 * @param {RequestInit} [options]
 * @returns {Promise<Response>}
 */
export function marketingRecommendFetch(options) {
  const satoken = resolveSatoken()
  return fetch(marketingRecommendUrl(), {
    credentials: 'include',
    ...options,
    headers: {
      ...(options?.headers || {}),
      ...(satoken ? { satoken } : {}),
    },
  })
}

/**
 * axios baseURL（去掉末尾 /），供 src/api/index.js 使用
 * dev/gray: /znhs-gray
 * prod:     /znhs
 */
export function getAxiosBaseURL() {
  return _base
}
