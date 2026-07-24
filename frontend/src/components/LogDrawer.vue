<template>
  <el-drawer
    :model-value="modelValue"
    title="操作日志"
    size="480px"
    direction="rtl"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div style="display:flex;align-items:center;gap:10px;flex:1;">
        <span style="font-weight:600;">操作日志</span>
        <el-tag size="small" type="info" effect="plain">{{ filtered.length }} 条</el-tag>
      </div>
    </template>

    <!-- 工具栏：分层过滤 + 复制 + 暂停 + 清空 -->
    <div class="log-toolbar">
      <el-checkbox-group v-model="levels" size="small">
        <el-checkbox-button value="ERROR">ERROR</el-checkbox-button>
        <el-checkbox-button value="WARN">WARN</el-checkbox-button>
        <el-checkbox-button value="INFO">INFO</el-checkbox-button>
      </el-checkbox-group>
      <div style="display:flex;gap:6px;margin-left:auto;">
        <el-button size="small" plain @click="copyLogs">复制</el-button>
        <el-button size="small" plain :type="uiLog.state.paused ? 'warning' : ''"
          @click="uiLog.state.paused = !uiLog.state.paused">
          {{ uiLog.state.paused ? '恢复滚动' : '暂停滚动' }}
        </el-button>
        <el-button size="small" plain @click="uiLog.clear()">清空</el-button>
      </div>
    </div>

    <!-- 日志列表 -->
    <div ref="listEl" class="log-list">
      <div v-if="!filtered.length" class="log-empty">暂无日志</div>
      <div v-for="e in filtered" :key="e.id" class="log-entry" :class="'lv-' + e.level.toLowerCase()">
        <div class="log-entry-head">
          <span class="log-lv">{{ e.level }}</span>
          <span class="log-module">{{ e.module }}</span>
          <span class="log-ts">{{ fmtTs(e.ts) }}</span>
        </div>
        <div class="log-msg">{{ e.message }}</div>
        <pre v-if="e.detail" class="log-detail">{{ e.detail }}</pre>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
/**
 * 日志侧边抽屉（规范 6）：分层展示 ERROR/WARN/INFO，
 * 支持复制、暂停滚动、清空。数据源为前端操作日志 uiLog（不依赖后端）。
 */
import { ref, computed, watch, nextTick } from 'vue'
import { uiLog } from '@/utils/uiLog'
import { $msg } from '@/utils/msg'

const props = defineProps({ modelValue: Boolean })
defineEmits(['update:modelValue'])

const levels = ref(['ERROR', 'WARN', 'INFO'])
const listEl = ref(null)

const filtered = computed(() =>
  uiLog.state.entries.filter(e => levels.value.includes(e.level)))

// 新日志到达时自动滚到顶部（新日志在前），暂停时不动
watch(() => uiLog.state.entries.length, async () => {
  if (uiLog.state.paused || !props.modelValue) return
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = 0
})

function fmtTs(ts) {
  return ts.toLocaleTimeString('zh-CN', { hour12: false })
    + '.' + String(ts.getMilliseconds()).padStart(3, '0')
}

async function copyLogs() {
  const text = uiLog.toText(levels.value)
  if (!text) { $msg.warn('没有可复制的日志'); return }
  try {
    await navigator.clipboard.writeText(text)
    $msg.ok('日志已复制到剪贴板')
  } catch {
    $msg.err('复制失败，请手动选择文本复制')
  }
}
</script>

<style scoped>
.log-toolbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding-bottom: 10px; border-bottom: 1px solid var(--border, #e5e7eb);
  margin-bottom: 10px;
}
.log-list { overflow-y: auto; max-height: calc(100vh - 180px); }
.log-empty { text-align: center; color: var(--muted, #6b7280); padding: 40px 0; font-size: 13px; }
.log-entry {
  border-left: 3px solid #cbd5e1; background: #f8fafc;
  border-radius: 4px; padding: 6px 10px; margin-bottom: 6px; font-size: 12px;
}
.log-entry.lv-error { border-left-color: #dc2626; background: #fef2f2; }
.log-entry.lv-warn  { border-left-color: #d97706; background: #fffbeb; }
.log-entry-head { display: flex; gap: 8px; align-items: center; }
.log-lv { font-weight: 700; font-size: 11px; }
.lv-error .log-lv { color: #dc2626; }
.lv-warn  .log-lv { color: #d97706; }
.lv-info  .log-lv { color: #2563eb; }
.log-module { color: var(--muted, #6b7280); font-size: 11px; }
.log-ts { margin-left: auto; color: var(--muted, #9ca3af); font-size: 11px; font-family: monospace; }
.log-msg { margin-top: 2px; line-height: 1.5; word-break: break-all; }
.log-detail {
  margin: 4px 0 0; padding: 6px 8px; background: #0f172a; color: #e2e8f0;
  border-radius: 4px; font-size: 11px; max-height: 160px; overflow: auto; line-height: 1.5;
}
</style>
