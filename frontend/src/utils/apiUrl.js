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
 * 安全解析响应体为 JSON。
 *
 * 背景：推荐链路（外部接口 + LLM 生成）耗时较长，一旦超过网关/Ingress 超时，
 * 网关会返回自己的 HTML 错误页（502/504 等）。此时直接 res.json() 会抛出
 * 「Unexpected token < in JSON at position 0」这种难以定位的异常。
 *
 * 本函数在解析前先判断 HTTP 状态与 Content-Type，非 JSON 时读取文本并抛出
 * 带上下文（HTTP 状态 + 超时/网关提示 + 片段）的可读错误，便于用户与排查。
 *
 * @param {Response} res
 * @returns {Promise<any>} 解析后的 JSON
 */
export async function readJsonOrThrow(res) {
  const ctype = (res.headers.get('content-type') || '').toLowerCase()
  const looksJson = ctype.includes('application/json') || ctype.includes('+json')

  if (looksJson) {
    return res.json()
  }

  // 非 JSON：读取文本用于诊断（HTML 错误页 / 空响应 / 网关文案）
  let text = ''
  try { text = await res.text() } catch { /* ignore */ }
  const snippet = (text || '').trim().replace(/\s+/g, ' ').slice(0, 160)
  const isHtml = /^\s*</.test(text || '')

  let hint
  if (res.status === 504 || res.status === 502 || res.status === 503) {
    hint = `网关超时或服务不可用（HTTP ${res.status}）：推荐生成耗时可能超过网关超时，请稍后重试或减少 topN`
  } else if (isHtml) {
    hint = `服务返回了非 JSON 内容（HTTP ${res.status}，疑似网关错误页/登录页），请检查服务状态或登录态`
  } else if (!res.ok) {
    hint = `请求失败（HTTP ${res.status}）`
  } else {
    hint = `响应不是 JSON（HTTP ${res.status}, content-type=${ctype || '未知'}）`
  }

  const err = new Error(snippet ? `${hint}｜响应片段：${snippet}` : hint)
  err.httpStatus = res.status
  err.nonJson = true
  throw err
}

/**
 * 营销推荐接口路径（实时对外服务，固定 /znhs 前缀，与环境 BASE_URL 无关）
 * 默认 dev/gray/prod 均为 /znhs/marketing/recommend
 * @param {{ debug?: boolean }} [opts] debug=true 时附带 resource_context / llm_prompts 等排障字段
 */
export function marketingRecommendUrl(opts) {
  const base = import.meta.env.VITE_MARKETING_RECOMMEND_PATH || '/znhs/marketing/recommend'
  if (opts && opts.debug) {
    const sep = base.includes('?') ? '&' : '?'
    return `${base}${sep}debug=1`
  }
  return base
}

/**
 * 营销推荐 fetch 封装（路径固定 /znhs/marketing/recommend，仍注入 satoken）
 * 运营测试页默认带 debug=1，拿到排障字段；对外下游不传则仅返回规范出参。
 * @param {RequestInit & { debug?: boolean }} [options]
 * @returns {Promise<Response>}
 */
export function marketingRecommendFetch(options) {
  const satoken = resolveSatoken()
  const { debug = true, ...fetchOpts } = options || {}
  return fetch(marketingRecommendUrl({ debug }), {
    credentials: 'include',
    ...fetchOpts,
    headers: {
      ...(fetchOpts?.headers || {}),
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
