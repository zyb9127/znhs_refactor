/**
 * 统一消息反馈工具 $msg —— 全局替换零散的 ElMessage 调用
 *
 * 三类反馈：
 *   $msg.ok(text)    成功（绿色，2s）
 *   $msg.warn(text)  警告（橙色，3s）
 *   $msg.err(text)   错误（红色，4s，可手动关闭）
 *
 * 高危操作二次确认：
 *   await $msg.confirm(text, { title, type, confirmText })
 *   取消时返回 false（不抛异常，调用方无需 try/catch 'cancel'）
 *
 * 接口错误规范化：
 *   $msg.errOf(e, fallback) 自动提取 axios/fetch 错误中的 detail/message，
 *   401/503 给出统一话术（鉴权过期 / 网关不可用）。
 */
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

function normalizeError(e, fallback = '操作失败') {
  const status = e?.response?.status || e?.status
  if (status === 401) return '登录已过期或无权限（401），请重新登录后重试'
  if (status === 503) return '服务暂不可用（503），可能是网关未就绪，请稍后重试'
  return (
    e?.response?.data?.detail
    || e?.response?.data?.message
    || e?.detail
    || e?.message
    || (typeof e === 'string' ? e : fallback)
  )
}

export const $msg = {
  ok(text)   { ElMessage({ type: 'success', message: text, duration: 2000 }) },
  warn(text) { ElMessage({ type: 'warning', message: text, duration: 3000 }) },
  err(text)  { ElMessage({ type: 'error',   message: text, duration: 4000, showClose: true }) },
  info(text) { ElMessage({ type: 'info',    message: text, duration: 2000 }) },

  /** 接口异常统一提示（含 401/503 兜底话术），并返回规范化文本供日志用 */
  errOf(e, fallback = '操作失败') {
    const text = normalizeError(e, fallback)
    this.err(text)
    return text
  },

  /**
   * 高危操作二次确认。resolve true=确认，false=取消。
   * type: 'warning'（默认，下线/覆盖类） | 'error'（删除类）
   */
  async confirm(text, { title = '操作确认', type = 'warning', confirmText = '确认' } = {}) {
    try {
      await ElMessageBox.confirm(text, title, {
        confirmButtonText: confirmText,
        cancelButtonText: '取消',
        type: type === 'error' ? 'warning' : type,
        confirmButtonClass: type === 'error' ? 'el-button--danger' : '',
        closeOnClickModal: false,   // 规范 9：禁止遮罩误关高危弹窗
        closeOnPressEscape: true,
      })
      return true
    } catch {
      return false
    }
  },
}

/**
 * 按钮防重复点击锁：包装 async 函数，执行期间再次调用直接忽略。
 * 返回 [wrappedFn, lockingRef]，lockingRef 可直接绑到按钮 :loading。
 */
export function useLock(fn) {
  const locking = ref(false)
  async function wrapped(...args) {
    if (locking.value) return
    locking.value = true
    try {
      return await fn(...args)
    } finally {
      locking.value = false
    }
  }
  return [wrapped, locking]
}

/** 防抖（默认 300ms，规范 1） */
export function debounce(fn, wait = 300) {
  let timer = null
  return function (...args) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { timer = null; fn.apply(this, args) }, wait)
  }
}
