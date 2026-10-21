/** 执行真实 API 包装函数，验证旧名称不会被当作 fragment/query 或重复解码。 */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { SourceTextModule, SyntheticModule } from 'node:vm'
import { encodePathSegments } from '../frontend/src/utils/apiPath.js'
import { skillNameError } from '../frontend/src/utils/skillName.js'

const calls = []
const client = Object.fromEntries(['get', 'post', 'put', 'patch', 'delete'].map(method => [
  method, (...args) => { calls.push({ method, args }); return Promise.resolve({}) }
]))
const http = new SyntheticModule(['default'], function () { this.setExport('default', client) })
const apiUrl = new SyntheticModule(['apiUrl'], function () { this.setExport('apiUrl', p => p) })
const paths = new SyntheticModule(['encodePathSegments'], function () { this.setExport('encodePathSegments', encodePathSegments) })
const source = await readFile(new URL('../frontend/src/api/autoConfig.js', import.meta.url), 'utf8')
const apiModule = new SourceTextModule(source)
await apiModule.link(specifier => {
  if (specifier === '@/api/index.js') return http
  if (specifier === '@/utils/apiUrl') return apiUrl
  if (specifier === '@/utils/apiPath') return paths
  throw new Error(`Unexpected dependency: ${specifier}`)
})
await apiModule.evaluate()
const api = apiModule.namespace
const operations = [
  ['acGetSkillConfig', 'get', '/config'],
  ['acContextAudit', 'get', '/context-audit'],
  ['acSkillAsTemplate', 'get', '/as-template'],
  ['acUpdateSkillConfig', 'put', '/config/biz_config', 'biz_config', {}],
  ['acReloadSkill', 'post', '/reload'],
  ['acUpdateSkillStatus', 'patch', '/status', 'offline'],
  ['acDeleteSkill', 'delete', ''],
]
for (const intent of ['！@#￥%', '旧活动?渠道=A&B=1', '旧活动%20+1', '旧活动 (A),B、C', '旧活动%2F1']) {
  for (const [operation, method, suffix, ...args] of operations) {
    await api[operation]('fujian', intent, ...args)
    const call = calls.pop()
    assert.equal(call.method, method)
    const url = new URL(call.args[0], 'https://example.test')
    assert.equal(url.hash, '')
    assert.equal(url.search, '')
    assert.equal(url.pathname, `/api/auto-config/skills/${encodePathSegments('fujian', intent)}${suffix}`)
    assert.equal(decodeURIComponent(url.pathname.split('/')[5]), intent)
  }
}
assert.equal(encodePathSegments('fujian', '旧#配置', '接口?A%'), 'fujian/%E6%97%A7%23%E9%85%8D%E7%BD%AE/%E6%8E%A5%E5%8F%A3%3FA%25')
assert.ok(skillNameError('！@#￥%'), '旧配置兼容不能放宽新增名称规则')
console.log('Legacy URLs passed: 5 historical names across 7 API operations; new-name validation remains enforced')
