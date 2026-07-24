/**
 * 前端操作日志（供日志侧边抽屉展示，规范 6）
 *
 * 纯前端环形缓冲（最多 500 条），记录页面关键操作与接口异常，
 * 不依赖后端日志接口（后端服务与数据结构零改动）。
 *
 * uiLog.info / warn / error(module, message, detail?)
 * uiLog.entries  响应式日志列表（新日志在前）
 */
import { reactive } from 'vue'

const MAX_ENTRIES = 500

const state = reactive({
  entries: [],   // { ts, level, module, message, detail }
  paused: false, // 暂停滚动时新日志仍记录，只是 UI 不自动滚动
})

let _seq = 0

function push(level, module, message, detail) {
  const e = {
    id: ++_seq,
    ts: new Date(),
    level,
    module,
    message: String(message ?? ''),
    detail: detail === undefined ? '' :
      (typeof detail === 'string' ? detail : safeJson(detail)),
  }
  state.entries.unshift(e)
  if (state.entries.length > MAX_ENTRIES) state.entries.length = MAX_ENTRIES
}

function safeJson(v) {
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

export const uiLog = {
  state,
  info(module, message, detail)  { push('INFO',  module, message, detail) },
  warn(module, message, detail)  { push('WARN',  module, message, detail) },
  error(module, message, detail) { push('ERROR', module, message, detail) },
  clear() { state.entries.length = 0 },
  /** 全部日志导出为文本（供复制） */
  toText(levels = null) {
    return state.entries
      .filter(e => !levels || levels.includes(e.level))
      .map(e => {
        const t = e.ts.toLocaleTimeString('zh-CN', { hour12: false })
          + '.' + String(e.ts.getMilliseconds()).padStart(3, '0')
        return `[${t}] [${e.level}] [${e.module}] ${e.message}${e.detail ? '\n' + e.detail : ''}`
      })
      .reverse()
      .join('\n')
  },
}
