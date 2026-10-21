/** 运行真实模板弹窗 setup/watch：草稿无需省份/发布，且过期响应不能覆盖新配置。 */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { SourceTextModule, SyntheticModule } from 'node:vm'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const vue = require('vue')
const { parse, compileScript } = require('@vue/compiler-sfc')
const source = await readFile(new URL('../frontend/src/components/TemplateEditDialog.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'template-draft-vars-test' })
const pending = []
const fetcher = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }))
const vueModule = new SyntheticModule(Object.keys(vue), function () {
  for (const key of Object.keys(vue)) this.setExport(key, vue[key])
})
const messages = new SyntheticModule(['ElMessage'], function () {
  this.setExport('ElMessage', { error() {}, warning() {}, success() {} })
})
const api = new SyntheticModule(['apiFetch'], function () { this.setExport('apiFetch', fetcher) })
const componentModule = new SourceTextModule(compiled.content)
await componentModule.link(id => {
  if (id === 'vue') return vueModule
  if (id === 'element-plus') return messages
  if (id === '@/utils/apiUrl') return api
  throw new Error(`Unexpected dependency: ${id}`)
})
await componentModule.evaluate()

const draft = key => ({ main_api: { source_type: 'direct', direct_mode: 'passthrough', mock_response: { [key]: { name: '' } } } })
const props = vue.reactive({
  modelValue: false, template: {}, isNew: true, mode: 'skill',
  editableProvinceOptions: [], editIntents: [], multiProduct: true,
  provinceMap: {}, productIdCandidates: [], draftApiNodes: draft('alpha'),
  availableApis: [{ api_name: 'main_api', enabled: true, source_type: 'direct', passthrough: true }],
})
const scope = vue.effectScope()
const state = scope.run(() => componentModule.namespace.default.setup(props, { emit() {}, expose() {} }))
const flush = async () => { await Promise.resolve(); await vue.nextTick(); await Promise.resolve() }
const respond = (request, key) => request.resolve({ ok: true, json: async () => ({ code: 200, data: [
  { key, source: 'passthrough', api_names: ['main_api'], subfields: [{ token: `${key}[name]`, label: 'name', path: 'name', sample: '' }] },
] }) })

assert.equal(pending.length, 0)
props.modelValue = true
await flush()
assert.equal(pending.length, 1, '创建页省份/意图尚空时，也必须根据草稿加载字段')
assert.equal(pending[0].url, '/api/skills/context_vars/preview')
assert.equal(pending[0].options.method, 'POST')
assert.deepEqual(JSON.parse(pending[0].options.body).api_nodes, draft('alpha'))
respond(pending[0], 'alpha')
await flush()
assert.equal(state.insertableVars.value[0].subfields[0].token, 'alpha[name]')

props.draftApiNodes = draft('beta')
await flush()
assert.equal(pending.length, 2)
props.draftApiNodes = draft('alpha')
await flush()
assert.equal(pending.length, 3, '切回旧配置时不可把之前的缓存误认为当前请求已完成')
respond(pending[2], 'alpha')
await flush()
respond(pending[1], 'beta')
await flush()
assert.equal(state.dynamicVarList.value[0].key, 'alpha', '旧请求不得覆盖最新接口配置')

props.modelValue = false
await flush()
props.draftApiNodes = null
props.template = { province: 'fujian', intent: '！@#￥%' }
props.modelValue = true
await flush()
const published = pending.at(-1)
assert.equal(published.url, '/api/skills/fujian/%EF%BC%81%40%23%EF%BF%A5%25/context_vars')
assert.equal(published.options, undefined)
respond(published, 'published')
await flush()
assert.equal(state.dynamicVarList.value[0].key, 'published')
scope.stop()
console.log('Template dialog passed: draft parameters, subfields, draft changes, stale responses and published-mode compatibility')
