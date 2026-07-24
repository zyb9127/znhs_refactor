<template>
  <div v-if="env.loaded && env.environment" class="env-banner" :class="'env-' + envClass">
    <span class="env-banner-dot"></span>
    <span>{{ envLabel }}</span>
    <span v-if="!env.authEnabled" class="env-banner-noauth">· 免鉴权模式（仅受控测试）</span>
    <span class="env-banner-prefix">前缀 {{ env.servicePrefix }}</span>
  </div>
</template>

<script setup>
/**
 * 顶部环境提示条（规范 10）：灰度/生产交互完全一致，仅提示当前环境。
 * 数据来自共享 useEnv()（底层调后端 /env，全应用只请求一次），加载失败时静默隐藏。
 * PRODUCTION_NOAUTH（免鉴权）模式给出显式提醒（规范 7）。
 */
import { computed } from 'vue'
import { useEnv } from '@/composables/useEnv'

const { env, environment } = useEnv()

const envClass = computed(() => {
  const e = environment.value || ''
  if (e === 'production') return 'prod'
  if (e === 'production_noauth') return 'noauth'
  if (e === 'gray') return 'gray'
  return 'dev'
})

const envLabel = computed(() => ({
  production: '生产环境',
  production_noauth: '生产行为（免鉴权）',
  gray: '灰度环境',
  development: '开发环境',
}[environment.value] || environment.value || ''))
</script>

<style scoped>
.env-banner {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; padding: 5px 14px; border-radius: 6px; margin-bottom: 10px;
  border: 1px solid transparent;
}
.env-banner-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.env-dev    { background: #eff6ff; color: #1d4ed8; border-color: #bfdbfe; }
.env-gray   { background: #fefce8; color: #a16207; border-color: #fde68a; }
.env-prod   { background: #f0fdf4; color: #15803d; border-color: #bbf7d0; }
.env-noauth { background: #fff7ed; color: #c2410c; border-color: #fed7aa; }
.env-banner-noauth { font-weight: 600; }
.env-banner-prefix { margin-left: auto; color: var(--muted, #9ca3af); font-family: monospace; }
</style>
