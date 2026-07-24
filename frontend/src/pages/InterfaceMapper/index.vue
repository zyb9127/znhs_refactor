<template>
  <div class="breadcrumb">
    <div class="breadcrumb-item">智能体运营</div>
    <div class="breadcrumb-item active"><span class="crumb-dot"></span>Skill 生成向导</div>
  </div>
  <div class="page">
    <div class="wizard-layout">
      <!-- 左侧步骤 -->
      <div class="steps-panel">
        <div v-for="(s, i) in steps" :key="i" class="step-item" :class="{active: currentStep===i, done: currentStep>i}" @click="currentStep>i&&(currentStep=i)">
          <div class="step-circle">{{ currentStep>i ? '✓' : i+1 }}</div>
          <div class="step-label">{{ s.label }}</div>
        </div>
      </div>

      <!-- 右侧内容 -->
      <div class="step-content">
        <!-- Step 0: 选择省份/意图 -->
        <div v-if="currentStep===0">
          <div class="step-title">选择省份与意图</div>
          <div class="form-row-2col">
            <div class="form-row"><label class="required">省份</label>
              <select class="form-control" v-model="wizard.province" @change="onProvinceChange">
                <option value="">请选择</option>
                <option v-for="s in skillsList" :key="s.province" :value="s.province">{{ s.meta?.province_name||s.province }}</option>
              </select>
            </div>
            <div class="form-row"><label class="required">意图</label>
              <select class="form-control" v-model="wizard.intent">
                <option value="">请先选择省份</option>
                <option v-for="i in intentOptions" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
          </div>
          <div class="step-footer">
            <button class="btn btn-primary" :disabled="!wizard.province||!wizard.intent" @click="currentStep=1">下一步 →</button>
          </div>
        </div>

        <!-- Step 1: 粘贴接口文档 -->
        <div v-if="currentStep===1">
          <div class="step-title">粘贴接口文档</div>
          <p class="step-desc">将接口文档（Markdown / 纯文本 / JSON 样例）粘贴到下方，LLM 将自动解析并生成接口配置。</p>
          <textarea class="form-control doc-area" v-model="wizard.docText" placeholder="粘贴接口文档内容..."></textarea>
          <div class="step-footer">
            <button class="btn btn-default" @click="currentStep=0">← 上一步</button>
            <button class="btn btn-primary" :disabled="!wizard.docText.trim()||parsing" @click="parseDoc">
              {{ parsing ? '🔍 解析中...' : '🔍 智能解析' }}
            </button>
          </div>
        </div>

        <!-- Step 2: 确认解析结果 -->
        <div v-if="currentStep===2">
          <div class="step-title">确认接口配置</div>
          <p class="step-desc">以下是 LLM 解析生成的接口配置，请检查并按需修改。</p>
          <div v-for="(api, idx) in wizard.parsedApis" :key="idx" class="api-card">
            <div class="api-card-header">
              <span class="api-name">{{ api.api_name }}</span>
              <span class="api-desc">{{ api._comment||api.description }}</span>
              <button class="btn-icon" @click="wizard.parsedApis.splice(idx,1)" title="删除">🗑</button>
            </div>
            <div class="api-card-body">
              <div class="form-row-2col">
                <div class="form-row"><label>接口名称</label><input class="form-control" v-model="api.api_name"></div>
                <div class="form-row"><label>URL</label><input class="form-control" v-model="api.url"></div>
              </div>
              <div class="form-row-2col">
                <div class="form-row"><label>方法</label>
                  <select class="form-control" v-model="api.method"><option>POST</option><option>GET</option><option>PUT</option></select>
                </div>
                <div class="form-row"><label>描述</label><input class="form-control" v-model="api._comment"></div>
              </div>
              <div class="form-row"><label>request_template</label>
                <textarea class="form-control code-area" v-model="api._req_str" rows="4"></textarea>
              </div>
              <div class="form-row-2col">
                <div class="form-row"><label>response_extract</label>
                  <textarea class="form-control code-area" v-model="api._ext_str" rows="5"></textarea>
                </div>
                <div class="form-row"><label>field_transform</label>
                  <textarea class="form-control code-area" v-model="api._tr_str" rows="5"></textarea>
                </div>
              </div>
            </div>
          </div>
          <button class="btn btn-default" style="margin-top:8px" @click="addEmptyApi">＋ 手动添加接口</button>
          <div class="step-footer">
            <button class="btn btn-default" @click="currentStep=1">← 上一步</button>
            <button class="btn btn-primary" :disabled="!wizard.parsedApis.length||saving" @click="saveAll">
              {{ saving ? '💾 保存中...' : '💾 保存全部接口' }}
            </button>
          </div>
        </div>

        <!-- Step 3: 完成 -->
        <div v-if="currentStep===3" class="done-panel">
          <div class="done-icon">🎉</div>
          <div class="done-title">接口配置已保存！</div>
          <p class="done-desc">共保存 {{ savedCount }} 个接口到 <strong>{{ wizard.province }} / {{ wizard.intent }}</strong></p>
          <div class="done-actions">
            <router-link to="/mapping" class="btn btn-primary">前往接口管理</router-link>
            <button class="btn btn-default" @click="resetWizard">重新生成</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="toast-wrap">
    <div v-for="(t,i) in toasts" :key="i" class="toast" :class="t.ok?'toast-ok':'toast-err'">{{ t.msg }}</div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { apiUrl, apiFetch } from '@/utils/apiUrl'

const skillsList = ref([])
const intentOptions = ref([])
const toasts = ref([])
const currentStep = ref(0)
const parsing = ref(false)
const saving = ref(false)
const savedCount = ref(0)

const steps = [
  { label: '选择省份/意图' },
  { label: '粘贴接口文档' },
  { label: '确认配置' },
  { label: '完成' }
]

const wizard = reactive({
  province: '', intent: '', docText: '', parsedApis: []
})

function onProvinceChange() {
  intentOptions.value = skillsList.value.filter(s => s.province === wizard.province).map(s => s.intent)
  wizard.intent = ''
}

async function parseDoc() {
  parsing.value = true
  try {
    const res = await apiFetch('/api/interfaces/parse_doc', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_text: wizard.docText, province: wizard.province, intent: wizard.intent })
    })
    const json = await res.json()
    if (json.code === 200) {
      wizard.parsedApis = (json.data || []).map(api => ({
        ...api,
        _req_str: JSON.stringify(api.request_template || {}, null, 2),
        _ext_str: JSON.stringify(api.response_extract || {}, null, 2),
        _tr_str: JSON.stringify(api.field_transform || {}, null, 2)
      }))
      currentStep.value = 2
    } else {
      showToast('❌ ' + (json.detail || json.message || '解析失败'), false)
    }
  } catch(e) { showToast('❌ ' + e.message, false) }
  finally { parsing.value = false }
}

function addEmptyApi() {
  wizard.parsedApis.push({
    api_name: 'new_api_' + Date.now(),
    _comment: '', url: '', method: 'POST',
    _req_str: '{}', _ext_str: '{}', _tr_str: '{}'
  })
}

async function saveAll() {
  saving.value = true
  let ok = 0, fail = 0
  for (const api of wizard.parsedApis) {
    let req = {}, ext = {}, tr = {}
    try { req = JSON.parse(api._req_str || '{}') } catch { showToast(`${api.api_name}: request_template JSON 错误`, false); saving.value = false; return }
    try { ext = JSON.parse(api._ext_str || '{}') } catch { showToast(`${api.api_name}: response_extract JSON 错误`, false); saving.value = false; return }
    try { tr = JSON.parse(api._tr_str || '{}') } catch { showToast(`${api.api_name}: field_transform JSON 错误`, false); saving.value = false; return }
    const body = { _comment: api._comment, url: api.url, method: api.method, enabled: true, request_template: req, response_extract: ext, field_transform: tr }
    try {
      const res = await apiFetch(`/api/interfaces/${wizard.province}/${wizard.intent}/${api.api_name}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const json = await res.json()
      if (json.code === 200) {
        ok++
      } else {
        fail++
      }
    } catch { fail++ }
  }
  saving.value = false
  savedCount.value = ok
  if (fail) showToast(`⚠️ ${ok} 个成功，${fail} 个失败`, false)
  else currentStep.value = 3
}

function resetWizard() {
  Object.assign(wizard, { province: '', intent: '', docText: '', parsedApis: [] })
  currentStep.value = 0
}

function showToast(msg, ok) {
  const t = { msg, ok }; toasts.value.push(t)
  setTimeout(() => { toasts.value = toasts.value.filter(x => x !== t) }, 2000)
}

onMounted(async () => {
  const res = await apiFetch('/api/skills')
  const json = await res.json()
  skillsList.value = json.data || []
})
</script>

<style scoped>
.breadcrumb{background:#fff;border-bottom:1px solid var(--border);padding:0 24px;display:flex;align-items:center;height:42px}
.breadcrumb-item{color:var(--muted);font-size:13px;display:flex;align-items:center;gap:6px}
.breadcrumb-item.active{color:var(--primary);font-weight:600}
.breadcrumb-item::after{content:'>';margin:0 8px;color:#ced4da;font-size:11px}
.breadcrumb-item:last-child::after{display:none}
.crumb-dot{width:6px;height:6px;border-radius:50%;background:var(--primary)}
.page{padding:20px 24px}
.wizard-layout{display:grid;grid-template-columns:200px 1fr;gap:24px;max-width:1100px;margin:0 auto}
.steps-panel{background:var(--card);border-radius:var(--radius);padding:20px 16px;box-shadow:var(--shadow);height:fit-content}
.step-item{display:flex;align-items:center;gap:10px;padding:10px 8px;border-radius:6px;cursor:default;margin-bottom:4px;transition:.2s}
.step-item.done{cursor:pointer}.step-item.done:hover{background:#f1f3f5}
.step-item.active .step-circle{background:var(--primary);color:#fff}
.step-item.done .step-circle{background:var(--success);color:#fff}
.step-circle{width:26px;height:26px;border-radius:50%;background:#e9ecef;color:var(--muted);font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.step-label{font-size:13px;color:var(--text)}
.step-item.active .step-label{font-weight:600;color:var(--primary)}
.step-content{background:var(--card);border-radius:var(--radius);padding:28px;box-shadow:var(--shadow)}
.step-title{font-size:16px;font-weight:700;margin-bottom:8px}
.step-desc{font-size:13px;color:var(--muted);margin-bottom:16px;line-height:1.6}
.step-footer{display:flex;justify-content:flex-end;gap:10px;margin-top:20px;padding-top:16px;border-top:1px solid var(--border)}
.form-row{margin-bottom:14px}
.form-row label{display:block;font-size:13px;font-weight:500;margin-bottom:5px}
.form-row .required::after{content:' *';color:var(--danger)}
.form-control{width:100%;height:36px;padding:0 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;outline:none;transition:.2s;font-family:inherit}
.form-control:focus{border-color:var(--primary);box-shadow:0 0 0 2px rgba(59,91,219,.15)}
textarea.form-control{height:auto;padding:8px 10px;resize:vertical;line-height:1.6}
.doc-area{min-height:280px;font-family:monospace;font-size:13px}
.code-area{font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;min-height:100px}
.form-row-2col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:34px;padding:0 16px;border:none;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;transition:.2s;white-space:nowrap;text-decoration:none}
.btn-default{background:#f1f3f5;color:var(--text)}.btn-default:hover{background:#e9ecef}
.btn-primary{background:var(--primary);color:#fff}.btn-primary:hover{background:var(--primary-hover)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.api-card{border:1px solid var(--border);border-radius:8px;margin-bottom:16px;overflow:hidden}
.api-card-header{background:#f8f9fa;padding:10px 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--border)}
.api-name{font-family:monospace;font-size:13px;font-weight:600;color:var(--primary)}
.api-desc{font-size:12px;color:var(--muted);flex:1}
.btn-icon{background:none;border:none;cursor:pointer;font-size:14px;padding:2px 6px;border-radius:4px}
.btn-icon:hover{background:#fee2e2}
.api-card-body{padding:16px}
.done-panel{text-align:center;padding:40px 20px}
.done-icon{font-size:56px;margin-bottom:12px}
.done-title{font-size:20px;font-weight:700;margin-bottom:8px}
.done-desc{font-size:14px;color:var(--muted);margin-bottom:24px}
.done-actions{display:flex;justify-content:center;gap:12px}
.toast-wrap{position:fixed;top:60px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px}
.toast{padding:10px 18px;border-radius:7px;font-size:13px;font-weight:500;box-shadow:0 4px 16px rgba(0,0,0,.15)}
.toast-ok{background:#2f9e44;color:#fff}.toast-err{background:#c92a2a;color:#fff}
</style>
