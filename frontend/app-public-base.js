import { name as appName } from './package.json'

/** 与 vite.config.js 中 base 保持一致；末尾必须有 `/`，与 `import.meta.env.BASE_URL` 一致 */
export function resolveAppPublicBase(env) {
  let base = appName
  if (env.VITE_SUB_APP_ENVIRONMENT === 'true') {
    if (env.VITE_APP_PREFIX) {
      base = `/${appName}-${env.VITE_APP_PREFIX}`
    } else {
      base = '/znhs/'
    }
  } else {
    base = `/${appName}/`
  }
  if (!base.endsWith('/')) {
    base += '/'
  }
  return base
}
