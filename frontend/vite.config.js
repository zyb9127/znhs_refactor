import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import { name as appName } from './package.json'
import { resolveAppPublicBase } from './app-public-base.js'
import { workspaceConfig } from './workspace-config'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())

  const base = resolveAppPublicBase(env)

  // 与 base 一致、去掉末尾 /，用于 dev proxy：避免把 SPA 自身地址误转发到后端
  const spaPathPrefix = base.replace(/\/$/, '')
  const proxyBypassSpa =
    env.VITE_SUB_APP_ENVIRONMENT === 'true' && !!env.VITE_APP_PREFIX

  const excludePackages = []
  if (mode === 'development') {
    if (workspaceConfig.useLocalMethods) {
      excludePackages.push('ling-yun-methods')
    }
    if (workspaceConfig.useLocalCustomMethods) {
      excludePackages.push('ling-yun-custom-components')
    }
  }

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    optimizeDeps: {
      exclude: excludePackages,
    },
    base,
    server: {
      host: true,
      strictPort: true,
      port: Number(env.VITE_APP_PORT),
      proxy: {
        // 开发/灰度：BASE_URL=/znhs-gray/，API 请求路径为 /znhs-gray/api/...
        // 后端 SERVICE_PREFIX=/znhs-gray，直接透传，无需 rewrite
        '/znhs-gray': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
          bypass(req) {
            const path = (req.url || '').split('?')[0]
            // API / internal / marketing 请求转发到后端
            const API_PREFIXES = ['/api/', '/internal/', '/marketing/']
            const relPath = path.slice(spaPathPrefix.length) // 去掉 /znhs-gray
            if (API_PREFIXES.some(p => relPath.startsWith(p))) {
              return null // 走 proxy，转发到后端
            }
            // 其余（SPA 页面路由）不转发，由 Vite 返回 index.html
            return false
          },
        },
        // 生产：BASE_URL=/znhs/，API 请求路径为 /znhs/api/...
        '/znhs': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        '/internal': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      // 兼容 Chrome 79+/Firefox 78+/Safari 13.1+，覆盖大多数企业内网浏览器版本
      // 若遇到 "Unexpected token '='" 等语法报错，说明浏览器不支持 esnext，此配置可解决
      target: ['es2020', 'chrome79', 'firefox78', 'safari13.1'],
    },
  }
})
