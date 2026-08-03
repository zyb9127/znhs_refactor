/**
 * 全局运行环境（development / gray / production / production_noauth）共享状态。
 *
 * 数据来自后端 /env（只读）。使用模块级 reactive + 单次 Promise 缓存，
 * 全应用只请求一次，多个组件（EnvBanner / SkillManager / SkillConfigEditor 等）
 * 复用同一份结果，用于按环境显隐测试类 UI（响应提取、标准数据关联、导出/日志按钮等）。
 *
 * 加载失败或未加载完成时 environment 为空串，所有「仅测试环境显示」的开关默认关闭，
 * 保证生产环境不会误露测试功能。
 */
import { reactive, computed } from 'vue'
import { apiFetch } from '@/utils/apiUrl'

const state = reactive({
  environment: '',     // '' | development | gray | production | production_noauth
  authEnabled: true,
  servicePrefix: '',
  loaded: false,
})

let _promise = null

export function loadEnv() {
  if (_promise) return _promise
  _promise = (async () => {
    try {
      const res = await apiFetch('/env')
      if (res.ok) {
        const data = await res.json()
        state.environment = data.environment || ''
        state.authEnabled = data.auth_enabled !== false
        state.servicePrefix = data.service_prefix || ''
        state.loaded = true
        return data
      }
    } catch { /* 静默：环境加载失败不影响主流程 */ }
    state.loaded = true
    return null
  })()
  return _promise
}

export function useEnv() {
  loadEnv()

  const environment  = computed(() => state.environment)
  const isDev        = computed(() => state.environment === 'development')
  const isGray       = computed(() => state.environment === 'gray')
  const isProd       = computed(() => state.environment === 'production')
  const isProdNoauth = computed(() => state.environment === 'production_noauth')

  // 仅开发环境显示（响应提取、标准数据关联，方便测试）
  const showDevOnly = isDev

  // 操作日志：development + production_noauth 显示，gray / production 隐藏
  const showExportLog = computed(
    () => state.environment === 'development' || state.environment === 'production_noauth'
  )

  // 一键导出/导入配置：全部环境（含 production）可见。
  // 后端已按账号分省权限收敛——省份账号只导出/导入本省，导入逐技能包校验写权限，
  // 不会影响其它省配置，故生产环境展示也是安全的。仅在环境未加载完成时隐藏。
  const showExportImport = computed(
    () => ['development', 'gray', 'production', 'production_noauth'].includes(state.environment)
  )

  return {
    env: state,
    environment, isDev, isGray, isProd, isProdNoauth,
    showDevOnly, showExportLog, showExportImport,
    loadEnv,
  }
}
