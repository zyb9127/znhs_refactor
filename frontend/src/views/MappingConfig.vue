<template>
  <div class="page-wrap">
    <!-- 顶部筛选栏 -->
    <div class="toolbar">
      <div class="toolbar-filters">
        <select v-model="filterProvince" @change="onProvinceChange" class="sel">
          <option value="">全部省份</option>
          <option v-for="p in provinceList" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
        <select v-model="filterIntent" @change="loadInterfaces" class="sel">
          <option value="">全部意图</option>
          <option v-for="i in intentList" :key="i" :value="i">{{ i }}</option>
        </select>
        <input v-model="filterSearch" @input="onSearchInput" class="search-input" placeholder="搜索接口名称/URL...">
      </div>
      <div class="toolbar-actions">
        <button class="btn btn-primary" @click="openCreate">+ 新建接口</button>
      </div>
    </div>

    <!-- 列表 -->
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th style="width:110px">省份</th>
            <th style="width:90px">意图</th>
            <th style="width:160px">接口名称</th>
            <th>接口URL</th>
            <th style="width:70px">方法</th>
            <th style="width:80px">启用状态</th>
            <th style="width:70px">Mock</th>
            <th style="width:110px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="8" class="empty">加载中...</td></tr>
          <tr v-else-if="!filtered.length"><td colspan="8" class="empty">暂无数据</td></tr>
          <tr v-for="item in filtered" :key="item.province+item.intent+item.api_name" class="data-row">
            <td>{{ item.province_name || item.province }}</td>
            <td>{{ item.intent }}</td>
            <td>
              <div class="api-name">{{ item.api_name }}</div>
              <div class="api-desc">{{ item.description }}</div>
            </td>
            <td class="url-cell">{{ item.url || '—' }}</td>
            <td><span class="method-badge">{{ item.method }}</span></td>
            <td>
              <span class="status-badge" :class="item.enabled ? 'badge-online' : 'badge-offline'">
                {{ item.enabled ? '🟢 启用' : '🔴 停用' }}
              </span>
            </td>
            <td>
              <span class="mock-badge" :class="item.mock_mode ? 'mock-on' : 'mock-off'">
                {{ item.mock_mode ? 'Mock' : '—' }}
              </span>
            </td>
            <td>
              <button class="btn-link" @click="openEdit(item)">编辑</button>
              <button class="btn-link" :class="item.enabled ? 'danger' : 'success'"
                @click="toggleStatus(item)">
                {{ item.enabled ? '停用' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 新建/编辑弹窗 -->
    <div v-if="showModal" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <div class="modal-header">
          <span>{{ editMode === 'create' ? '新建接口' : '编辑接口' }}</span>
          <button class="modal-close" @click="closeModal">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-row">
            <div class="form-item">
              <label>省份 <em>*</em></label>
              <select v-model="form.province" @change="onFormProvinceChange" :disabled="editMode==='edit'">
                <option value="">请选择</option>
                <option v-for="p in provinceList" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
            </div>
            <div class="form-item">
              <label>意图 <em>*</em></label>
              <select v-model="form.intent" :disabled="editMode==='edit'">
                <option value="">请选择</option>
                <option v-for="i in formIntentList" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-item full">
              <label>接口标识名 <em>*</em></label>
              <input v-model="form.api_name" :disabled="editMode==='edit'" placeholder="如 query_package，唯一标识">
            </div>
          </div>
          <div class="form-row">
            <div class="form-item full">
              <label>接口URL <em>*</em></label>
              <input v-model="form.url" placeholder="http://...">
            </div>
          </div>
          <div class="form-row">
            <div class="form-item">
              <label>请求方法</label>
              <select v-model="form.method">
                <option>POST</option><option>GET</option><option>PUT</option>
              </select>
            </div>
            <div class="form-item">
              <label>超时(ms)</label>
              <input v-model.number="form.timeout" type="number" placeholder="5000">
            </div>
          </div>
          <div class="form-row">
            <div class="form-item">
              <label>启用状态</label>
              <select v-model="form.enabled">
                <option :value="true">启用</option>
                <option :value="false">停用</option>
              </select>
            </div>
            <div class="form-item">
              <label>Mock 模式</label>
              <select v-model="form.mock_mode">
                <option :value="false">关闭</option>
                <option :value="true">开启</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-item full">
              <label>描述</label>
              <input v-model="form.description" placeholder="接口用途说明">
            </div>
          </div>
          <div class="form-row">
            <div class="form-item full">
              <label>请求参数 (JSON)</label>
              <textarea v-model="form.params_raw" rows="4" placeholder='{"phone": "{phone}", ...}'></textarea>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="closeModal">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="saveInterface">
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Toast -->
    <div v-if="toast.show" class="toast" :class="'toast-'+toast.type">{{ toast.msg }}</div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { apiFetch } from '@/utils/apiUrl'

const skillsList = ref([])
const interfaces = ref([])
const loading = ref(false)
const saving = ref(false)

const filterProvince = ref('')
const filterIntent = ref('')
const filterSearch = ref('')
let searchTimer = null

const showModal = ref(false)
const editMode = ref('create')
const toast = reactive({ show: false, msg: '', type: 'success' })

const form = reactive({
  province: '', intent: '', api_name: '', url: '', method: 'POST',
  timeout: 5000, enabled: true, mock_mode: false, description: '', params_raw: ''
})

// ── 计算 ─────────────────────────────────────────────────────
const provinceList = computed(() => {
  const map = {}
  skillsList.value.forEach(s => { if (!map[s.province]) map[s.province] = s.meta?.province_name || s.province })
  return Object.entries(map).map(([value, label]) => ({ value, label }))
})

const intentList = computed(() => {
  if (!filterProvince.value) return [...new Set(skillsList.value.map(s => s.intent))]
  return skillsList.value.filter(s => s.province === filterProvince.value).map(s => s.intent)
})

const formIntentList = computed(() => {
  if (!form.province) return [...new Set(skillsList.value.map(s => s.intent))]
  return skillsList.value.filter(s => s.province === form.province).map(s => s.intent)
})

const filtered = computed(() => {
  let list = interfaces.value
  if (filterProvince.value) list = list.filter(i => i.province === filterProvince.value)
  if (filterIntent.value) list = list.filter(i => i.intent === filterIntent.value)
  if (filterSearch.value) {
    const q = filterSearch.value.toLowerCase()
    list = list.filter(i => i.api_name.toLowerCase().includes(q) || (i.url || '').toLowerCase().includes(q) || (i.description || '').toLowerCase().includes(q))
  }
  return list
})

// ── 加载 ─────────────────────────────────────────────────────
async function loadSkills() {
  try {
    const res = await apiFetch('/api/skills')
    const json = await res.json()
    skillsList.value = json.data || []
  } catch {}
}

async function loadInterfaces() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (filterProvince.value) params.set('province', filterProvince.value)
    const res = await apiFetch(`/api/interfaces?${params}`)
    const json = await res.json()
    interfaces.value = json.data || []
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

function onProvinceChange() { filterIntent.value = ''; loadInterfaces() }
function onSearchInput() { clearTimeout(searchTimer); searchTimer = setTimeout(() => {}, 300) }

// ── 新建/编辑 ─────────────────────────────────────────────────
function openCreate() {
  editMode.value = 'create'
  Object.assign(form, {
    province: filterProvince.value || '',
    intent: filterIntent.value || '',
    api_name: '', url: '', method: 'POST',
    timeout: 5000, enabled: true, mock_mode: false, description: '', params_raw: ''
  })
  showModal.value = true
}

function openEdit(item) {
  editMode.value = 'edit'
  Object.assign(form, {
    province: item.province, intent: item.intent,
    api_name: item.api_name, url: item.url || '',
    method: item.method || 'POST', timeout: item.timeout || 5000,
    enabled: item.enabled !== false, mock_mode: !!item.mock_mode,
    description: item.description || '', params_raw: ''
  })
  // 加载完整配置
  apiFetch(`/api/interfaces/${item.province}/${item.intent}/${item.api_name}`)
    .then(r => r.json()).then(j => {
      if (j.code === 200) {
        const d = j.data
        form.params_raw = d.params ? JSON.stringify(d.params, null, 2) : ''
      }
    }).catch(() => {})
  showModal.value = true
}

function onFormProvinceChange() { form.intent = '' }
function closeModal() { showModal.value = false }

async function saveInterface() {
  if (!form.province || !form.intent || !form.api_name || !form.url) {
    showToast('省份、意图、接口名、URL 为必填项', 'error'); return
  }
  let params = undefined
  if (form.params_raw?.trim()) {
    try { params = JSON.parse(form.params_raw) } catch { showToast('请求参数 JSON 格式错误', 'error'); return }
  }
  saving.value = true
  try {
    const body = {
      url: form.url, method: form.method, timeout: form.timeout,
      enabled: form.enabled, mock_mode: form.mock_mode,
      description: form.description,
      ...(params ? { params } : {})
    }
    const res = await apiFetch(`/api/interfaces/${form.province}/${form.intent}/${form.api_name}`, {
      method: 'PUT', body: JSON.stringify(body)
    })
    const json = await res.json()
    if (json.code === 200) {
      showToast('保存成功，配置已实时生效', 'success')
      closeModal()
      loadInterfaces()
    } else {
      showToast(json.message || '保存失败', 'error')
    }
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error')
  } finally {
    saving.value = false
  }
}

// ── 状态切换 ─────────────────────────────────────────────────
async function toggleStatus(item) {
  try {
    const res = await apiFetch(`/api/interfaces/${item.province}/${item.intent}/${item.api_name}/status`, {
      method: 'PATCH', body: JSON.stringify({ enabled: !item.enabled })
    })
    const json = await res.json()
    if (json.code === 200) {
      item.enabled = !item.enabled
      showToast(`接口已${item.enabled ? '启用' : '停用'}，配置已实时生效`, 'success')
    } else {
      showToast(json.message || '操作失败', 'error')
    }
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error')
  }
}

function showToast(msg, type = 'success') {
  toast.msg = msg; toast.type = type; toast.show = true
  setTimeout(() => { toast.show = false }, 3000)
}

onMounted(async () => { await loadSkills(); loadInterfaces() })
</script>

<style scoped>
.page-wrap { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; gap: 12px; flex-wrap: wrap; }
.toolbar-filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.toolbar-actions { display: flex; gap: 10px; }
.sel { padding: 7px 12px; border: 1px solid var(--border, #e2e8f0); border-radius: 7px; font-size: 14px; background: #fff; min-width: 120px; }
.search-input { padding: 7px 12px; border: 1px solid var(--border, #e2e8f0); border-radius: 7px; font-size: 14px; width: 220px; }
.btn { padding: 8px 18px; border: none; border-radius: 7px; font-size: 14px; font-weight: 500; cursor: pointer; transition: .15s; }
.btn-primary { background: var(--primary, #2563eb); color: #fff; }
.btn-primary:hover { background: #1d4ed8; }
.btn-secondary { background: #f1f5f9; color: #334155; }
.btn-secondary:hover { background: #e2e8f0; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.table-wrap { background: #fff; border: 1px solid var(--border, #e2e8f0); border-radius: 10px; overflow: hidden; }
.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th { background: #f8fafc; padding: 10px 14px; text-align: left; font-weight: 600; color: #64748b; border-bottom: 1px solid var(--border, #e2e8f0); }
.data-table td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
.data-row:hover td { background: #f8fafc; }
.empty { text-align: center; color: #94a3b8; padding: 40px 0; }
.api-name { font-weight: 600; color: #1e293b; font-size: 13px; font-family: monospace; }
.api-desc { font-size: 12px; color: #94a3b8; margin-top: 2px; }
.url-cell { font-size: 12px; color: #64748b; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.method-badge { background: #eff6ff; color: #2563eb; font-size: 12px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.status-badge { font-size: 12px; padding: 3px 10px; border-radius: 20px; font-weight: 500; }
.badge-online { background: #dcfce7; color: #15803d; }
.badge-offline { background: #fee2e2; color: #b91c1c; }
.mock-badge { font-size: 12px; padding: 2px 8px; border-radius: 4px; }
.mock-on { background: #fef9c3; color: #854d0e; font-weight: 600; }
.mock-off { color: #cbd5e1; }
.btn-link { background: none; border: none; cursor: pointer; font-size: 13px; padding: 2px 6px; border-radius: 4px; color: var(--primary, #2563eb); }
.btn-link:hover { background: #eff6ff; }
.btn-link.danger { color: #ef4444; }
.btn-link.danger:hover { background: #fef2f2; }
.btn-link.success { color: #16a34a; }
.btn-link.success:hover { background: #f0fdf4; }
/* Modal */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 1000; display: flex; align-items: center; justify-content: center; }
.modal { background: #fff; border-radius: 12px; width: 640px; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,.2); }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px; border-bottom: 1px solid var(--border, #e2e8f0); font-weight: 600; font-size: 15px; }
.modal-close { background: none; border: none; font-size: 18px; cursor: pointer; color: #94a3b8; padding: 2px 6px; }
.modal-body { padding: 20px; }
.modal-footer { padding: 16px 20px; border-top: 1px solid var(--border, #e2e8f0); display: flex; justify-content: flex-end; gap: 10px; }
.form-row { display: flex; gap: 16px; margin-bottom: 14px; }
.form-item { flex: 1; display: flex; flex-direction: column; gap: 5px; }
.form-item.full { flex: none; width: 100%; }
.form-item label { font-size: 13px; color: #64748b; font-weight: 500; }
.form-item label em { color: #ef4444; margin-left: 2px; font-style: normal; }
.form-item input, .form-item select, .form-item textarea { padding: 8px 12px; border: 1px solid var(--border, #e2e8f0); border-radius: 7px; font-size: 14px; font-family: inherit; }
.form-item input:focus, .form-item select:focus, .form-item textarea:focus { outline: none; border-color: var(--primary, #2563eb); }
.form-item input:disabled, .form-item select:disabled { background: #f8fafc; color: #94a3b8; }
/* Toast */
.toast { position: fixed; bottom: 30px; right: 30px; z-index: 2000; padding: 12px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; box-shadow: 0 4px 16px rgba(0,0,0,.15); }
.toast-success { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
.toast-error { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
</style>
