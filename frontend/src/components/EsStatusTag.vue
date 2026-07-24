<template>
  <el-tooltip :content="tooltip" placement="top" :show-after="200">
    <el-tag size="small" :type="tagType" effect="plain">{{ label }}</el-tag>
  </el-tooltip>
</template>

<script setup>
/**
 * 统一 ES 配置来源状态标签（规范 5）
 * source: 'es' | 'redis' | 'local'
 * loadedAt: 配置加载时间（可选）
 * esError: ES 异常详情文本（可选，hover 展示）
 */
import { computed } from 'vue'

const props = defineProps({
  source:   { type: String, default: 'local' },
  loadedAt: { type: String, default: '' },
  esError:  { type: String, default: '' },
})

const label = computed(() =>
  ({ es: 'ES', redis: 'Redis', local: '本地' })[props.source] || props.source)

const tagType = computed(() =>
  ({ es: 'success', redis: 'warning', local: 'info' })[props.source] || 'info')

const tooltip = computed(() => {
  if (props.esError) return `ES 异常：${props.esError}`
  if (props.source === 'es')
    return `来自 ES 已发布配置${props.loadedAt ? '，加载于 ' + props.loadedAt : ''}`
  if (props.source === 'redis')
    return `来自 Redis 缓存${props.loadedAt ? '，加载于 ' + props.loadedAt : ''}`
  return '来自本地文件（ES 中尚无已发布配置或 ES 不可用）'
})
</script>
