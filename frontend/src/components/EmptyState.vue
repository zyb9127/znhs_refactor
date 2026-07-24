<template>
  <div class="empty-state">
    <div class="empty-state-icon">{{ icon }}</div>
    <div class="empty-state-title">{{ title }}</div>
    <div v-if="description" class="empty-state-desc">{{ description }}</div>
    <el-button v-if="retryable" size="small" type="primary" plain
      style="margin-top:12px;" @click="$emit('retry')">
      <el-icon><Refresh /></el-icon>&nbsp;刷新重试
    </el-button>
  </div>
</template>

<script setup>
/**
 * 全局统一空状态组件（规范 8）
 * variant: 'empty'（无数据） | 'es-missing'（ES 索引缺失/无线上配置） | 'error'（加载失败）
 */
import { computed } from 'vue'

const props = defineProps({
  variant:     { type: String, default: 'empty' },
  title:       { type: String, default: '' },
  description: { type: String, default: '' },
  retryable:   { type: Boolean, default: false },
})
defineEmits(['retry'])

const icon = computed(() =>
  ({ empty: '📭', 'es-missing': '🗄️', error: '⚠️' })[props.variant] || '📭')
</script>

<style scoped>
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 48px 20px; color: var(--muted, #6b7280);
}
.empty-state-icon { font-size: 36px; margin-bottom: 10px; }
.empty-state-title { font-size: 14px; font-weight: 600; color: var(--text, #1f2937); }
.empty-state-desc { font-size: 12px; margin-top: 6px; max-width: 420px; text-align: center; line-height: 1.6; }
</style>
