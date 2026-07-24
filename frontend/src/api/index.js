import axios from 'axios'
import { getAxiosBaseURL } from '@/utils/apiUrl'

const http = axios.create({
  baseURL: getAxiosBaseURL(),
  timeout: 60000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' }
})

/**
 * 从 Cookie 中读取 satoken。
 * 灵运平台鉴权流程（文档 7.1）：主应用登录后将 satoken 放入 Cookie，子应用从 Cookie 读取放入 header。
 */
function resolveSatoken() {
  return document.cookie
    .split('; ')
    .find(row => row.startsWith('satoken='))
    ?.split('=')[1] || ''
}

// 请求拦截器：从 Cookie 读取 satoken，注入到请求头
http.interceptors.request.use(config => {
  const satoken = resolveSatoken()
  if (satoken) {
    config.headers['satoken'] = satoken
  }
  return config
})

// 响应拦截器：401 通知主应用重新登录，401/503 统一兜底记录（交互规范 7）
http.interceptors.response.use(
  res => res.data,
  async err => {
    const status = err?.response?.status
    const url = err?.config?.url || ''
    if (status === 401) {
      window.$wujie?.bus?.$emit('lingyun:auth:expired')
    }
    try {
      const { uiLog } = await import('@/utils/uiLog')
      if (status === 401) uiLog.error('HTTP', `401 鉴权失败：${url}`, '登录已过期或无权限，请重新登录')
      else if (status === 503) uiLog.error('HTTP', `503 服务不可用：${url}`, '网关或后端服务暂不可用，请稍后重试')
      else if (status >= 500) uiLog.error('HTTP', `${status} 服务端错误：${url}`, err?.response?.data)
    } catch { /* 日志记录失败不影响业务 */ }
    return Promise.reject(err)
  }
)

export default http
