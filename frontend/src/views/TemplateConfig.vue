<template>
  <div class="page-wrap">
    <!-- 顶部筛选栏 -->
    <div class="toolbar">
      <div class="toolbar-filters">
        <select v-model="filterProvince" @change="onProvinceChange" class="sel">
          <option value="">全部省份</option>
          <option v-for="p in provinceList" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
        <select v-model="filterIntent" @change="loadTemplates" class="sel">
          <option value="">全部意图</option>
          <option v-for="i in intentList" :key="i" :value="i">{{ i }}</option>
        </select>
        <select v-model="filterStatus" @change="loadTemplates" class="sel">
          <option value="">全部状态</option>
          <option value="online">🟢 上线</option>
          <option value="offline">🔴 下线</option>
        </select>
        <input v-model="filterName" @input="onSearchInput" class="search-input" placeholder="搜索话术名称...">
      </div>
      <div class="toolbar-actions">
        <button class="btn btn-primary" @click="openCreate">+ 新建话术</button>
      </div>
    </div>

    <!-- 列表 -->
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th style="width:120px">省份</th>
            <th style="width:100px">意图</th>
            <th>话术名称</th>
            <th style="width:90px">场景</th>
            <th style="width:90px">环节</th>
            <th style="width:80px">状态</th>
            <th style="width:140px">更新时间</th>
            <th style="width:120px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="8" class="empty">加载中...</td></tr>
          <tr v-else-if="!templates.length"><td colspan="8" class="empty">暂无数据</td></tr>
          <tr v-for="tpl in templates" :key="tpl.template_id" class="data-row">
            <td>{{ tpl.province_name || tpl.province }}</td>
            <td>{{ tpl.intent }}</td>
            <td>
              <div class="tpl-name">{{ tpl.template_name }}</div>
              <div class="tpl-preview">{{ tpl.template_content?.slice(0,40) }}{{ tpl.template_content?.length > 40 ? '...' : '' }}</div>
            </td>
            <td>{{ tpl.scene || '—' }}</td>
            <td>{{ tpl.stage || '—' }}</td>
            <td>
              <span class="status-badge" :class="tpl.status === 'online' ? 'badge-online' : 'badge-offline'">
                {{ tpl.status === 'online' ? '🟢 上线' : '🔴 下线' }}
              </span>
            </td>
            <td class="text-muted">{{ tpl.updated_at || tpl.created_at || '—' }}</td>
            <td>
              <button class="btn-link" @click="openEdit(tpl)">编辑</button>
              <button class="btn-link" :class="tpl.status === 'online' ? 'danger' : 'success'"
                @click="toggleStatus(tpl)">
                {{ tpl.status === 'online' ? '下线' : '上线' }}
              </button>
              <button class="btn-link danger" @click="confirmDelete(tpl)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 分页 -->
      <div class="pagination" v-if="total > pageSize">
        <button :disabled="page <= 1" @click="page--; loadTemplates()">‹ 上一页</button>
        <span>{{ page }} / {{ Math.ceil(total / pageSize) }}</span>
        <button :disabled="page >= Math.ceil(total / pageSize)" @click="page++; loadTemplates()">下一页 ›</button>
      </div>
    </div>

    <!-- 新建/编辑弹窗 -->
    <div v-if="showModal" class="modal-mask" @click.self="showModal=false">
      <div class="modal">
        <div class="modal-header">
          <span>{{ editingId ? '编辑话术模板' : '新建话术模板' }}</span>
          <button class="modal-close" @click="showModal=false">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-row">
            <div class="form-item">
              <label>省份 <em>*</em></label>
              <select v-model="form.province" @change="onFormProvinceChange" :disabled="!!editingId">
                <option value="">请选择</option>
                <option v-for="p in provinceList" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
            </div>
            <div class="form-item">
              <label>意图 <em>*</em></label>
              <select v-model="form.intent" :disabled="!!editingId">
                <option value="">请选择</option>
                <option v-for="i in formIntentList" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-item full">
              <label>话术名称 <em>*</em></label>
              <input v-model="form.template_name" placeholder="请输入话术名称">
            </div>
          </div>
          <div class="form-row">
            <div class="form-item">
              <label>场景</label>
              <input v-model="form.scene" placeholder="如：外呼/入呼">
            </div>
            <div class="form-item">
              <label>环节</label>
              <input v-model="form.stage" placeholder="如：切入环节">
            </div>
          </div>
          <div class="form-row">
            <div class="form-item full">
              <label>话术内容 <em>*</em></label>
              <textarea v-model="form.template_content" rows="6" placeholder="请输入话术内容，支持 {context.xxx} 变量"></textarea>
            </div>
          </div>
          <div class="form-row">
            <div class="form-item">
              <label>状态</label>
              <select v-model="form.status">
                <option value="online">上线</option>
                <option value="offline">下线</option>
              </select>
            </div>
            <div class="form-item">
              <label>产品ID</label>
              <input v-model="form.product_id" placeholder="可选，为空则为兜底话术">
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="showModal=false">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="saveTemplate">
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 删除确认 -->
    <div v-if="showDeleteConfirm" class="modal-mask" @click.self="showDeleteConfirm=false">
      <div class="modal confirm-modal">
        <div class="modal-header">
          <span>确认删除</span>
          <button class="modal-close" @click="showDeleteConfirm=false">✕</button>
        </div>
        <div class="modal-body">
          <p>确定删除话术「<b>{{ deletingTpl?.template_name }}</b>」？此操作不可撤销。</p>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="showDeleteConfirm=false">取消</button>
          <button class="btn btn-danger" :disabled="deleting" @click="doDelete">
            {{ deleting ? '删除中...' : '确认删除' }}
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

// ── 状态 ──────────────────────────────────────────────────────
const skillsList = ref([])
const templates = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)

const filterProvince = ref('')
const filterIntent = ref('')
const filterStatus = ref('')
const filterName = ref('')
let searchTimer = null

const showModal = ref(false)
const editingId = ref('')
const showDeleteConfirm = ref(false)
const deletingTpl = ref(null)

const toast = reactive({ show: false, msg: '', type: 'success' })

const form = reactive({
  province: '', intent: '', template_name: '', template_content: '',
  scene: '', stage: '', status: 'online', product_id: ''
})

// ── 计算省份/意图列表 ─────────────────────────────────────────
const provinceList = computed(() => {
  const map = {}
  skillsList.value.forEach(s => {
    if (!map[s.province]) {
      map[s.province] = s.meta?.province_name || s.province
    }
  })
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

// ── 加载 ─────────────────────────────────────────────────────
async function loadSkills() {
  try {
    const res = await apiFetch('/api/skills')
    const json = await res.json()
    skillsList.value = json.data || []
  } catch {}
}

async function loadTemplates() {
  loading.value = true
  try {
    const params = new URLSearchParams({ page: page.value, page_size: pageSize.value })
    if (filterProvince.value) params.set('province', filterProvince.value)
    if (filterIntent.value) params.set('intent', filterIntent.value)
    if (filterStatus.value) params.set('status', filterStatus.value)
    if (filterName.value) params.set('name', filterName.value)
    const res = await apiFetch(`/api/templates?${params}`)
    const json = await res.json()
    const items = json.data?.items || []
    // 注入省份名称
    const provinceNameMap = {}
    skillsList.value.forEach(s => { provinceNameMap[s.province] = s.meta?.province_name || s.province })
    items.forEach(t => { t.province_name = provinceNameMap[t.province] || t.province })
    templates.value = items
    total.value = json.data?.total || 0
  } catch (e) {
    showToast('加载失败: ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

function onProvinceChange() {
  filterIntent.value = ''
  page.value = 1
  loadTemplates()
}

function onSearchInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { page.value = 1; loadTemplates() }, 400)
}

// ── 新建/编辑 ─────────────────────────────────────────────────
function openCreate() {
  editingId.value = ''
  Object.assign(form, {
    province: filterProvince.value || '',
    intent: filterIntent.value || '',
    template_name: '', template_content: '',
    scene: '', stage: '', status: 'online', product_id: ''
  })
  showModal.value = true
}

function openEdit(tpl) {
  editingId.value = tpl.template_id
  Object.assign(form, {
    province: tpl.province,
    intent: tpl.intent,
    template_name: tpl.template_name || '',
    template_content: tpl.template_content || '',
    scene: tpl.scene || '',
    stage: tpl.stage || '',
    status: tpl.status || 'online',
    product_id: tpl.product_id || ''
  })
  showModal.value = true
}

function onFormProvinceChange() {
  form.intent = ''
}

async function saveTemplate() {
  if (!form.province || !form.intent || !form.template_name || !form.template_content) {
    showToast('省份、意图、名称、内容为必填项', 'error'); return
  }
  saving.value = true
  try {
    let res, json
    if (editingId.value) {
      res = await apiFetch(`/api/templates/${editingId.value}`, {
        method: 'PUT', body: JSON.stringify({
          template_name: form.template_name,
          template_content: form.template_content,
          scene: form.scene, stage: form.stage,
          status: form.status, product_id: form.product_id
        })
      })
    } else {
      res = await apiFetch('/api/templates', {
        method: 'POST', body: JSON.stringify({ ...form })
      })
    }
    json = await res.json()
    if (json.code === 200) {
      showToast('保存成功，配置已实时生效', 'success')
      showModal.value = false
      loadTemplates()
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
async function toggleStatus(tpl) {
  const newStatus = tpl.status === 'online' ? 'offline' : 'online'
  try {
    const res = await apiFetch(`/api/templates/${tpl.template_id}/status`, {
      method: 'PATCH', body: JSON.stringify({ status: newStatus })
    })
    const json = await res.json()
    if (json.code === 200) {
      tpl.status = newStatus
      showToast(`已${newStatus === 'online' ? '上线' : '下线'}，配置已实时生效`, 'success')
    } else {
      showToast(json.message || '操作失败', 'error')
    }
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error')
  }
}

// ── 删除 ─────────────────────────────────────────────────────
function confirmDelete(tpl) {
  deletingTpl.value = tpl
  showDeleteConfirm.value = true
}

async function doDelete() {
  deleting.value = true
  try {
    const res = await apiFetch(`/api/templates/${deletingTpl.value.template_id}`, { method: 'DELETE' })
    const json = await res.json()
    if (json.code === 200) {
      showToast('删除成功', 'success')
      showDeleteConfirm.value = false
      loadTemplates()
    } else {
      showToast(json.message || '删除失败', 'error')
    }
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error')
  } finally {
    deleting.value = false
  }
}

// ── Toast ─────────────────────────────────────────────────────
function showToast(msg, type = 'success') {
  toast.msg = msg; toast.type = type; toast.show = true
  setTimeout(() => { toast.show = false }, 3000)
}

onMounted(async () => { await loadSkills(); loadTemplates() })
</script>

<style scoped>
.page-wrap { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; gap: 12px; flex-wrap: wrap; }
.toolbar-filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.toolbar-actions { display: flex; gap: 10px; }
.sel { padding: 7px 12px; border: 1px solid var(--border); border-radius: 7px; font-size: 14px; background: #fff; min-width: 120px; }
.search-input { padding: 7px 12px; border: 1px solid var(--border); border-radius: 7px; font-size: 14px; width: 200px; }
.btn { padding: 8px 18px; border: none; border-radius: 7px; font-size: 14px; font-weight: 500; cursor: pointer; transition: .15s; }
.btn-primary { background: var(--primary, #2563eb); color: #fff; }
.btn-primary:hover { background: #1d4ed8; }
.btn-secondary { background: #f1f5f9; color: #334155; }
.btn-secondary:hover { background: #e2e8f0; }
.btn-danger { background: #ef4444; color: #fff; }
.btn-danger:hover { background: #dc2626; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.table-wrap { background: #fff; border: 1px solid var(--border, #e2e8f0); border-radius: 10px; overflow: hidden; }
.data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.data-table th { background: #f8fafc; padding: 10px 14px; text-align: left; font-weight: 600; color: #64748b; border-bottom: 1px solid var(--border, #e2e8f0); }
.data-table td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
.data-row:hover td { background: #f8fafc; }
.empty { text-align: center; color: #94a3b8; padding: 40px 0; }
.tpl-name { font-weight: 500; color: #1e293b; }
.tpl-preview { font-size: 12px; color: #94a3b8; margin-top: 2px; }
.text-muted { color: #94a3b8; font-size: 13px; }
.status-badge { font-size: 12px; padding: 3px 10px; border-radius: 20px; font-weight: 500; }
.badge-online { background: #dcfce7; color: #15803d; }
.badge-offline { background: #fee2e2; color: #b91c1c; }
.btn-link { background: none; border: none; cursor: pointer; font-size: 13px; padding: 2px 6px; border-radius: 4px; color: var(--primary, #2563eb); transition: .15s; }
.btn-link:hover { background: #eff6ff; }
.btn-link.danger { color: #ef4444; }
.btn-link.danger:hover { background: #fef2f2; }
.btn-link.success { color: #16a34a; }
.btn-link.success:hover { background: #f0fdf4; }
.pagination { display: flex; align-items: center; justify-content: center; gap: 16px; padding: 16px; font-size: 14px; color: #64748b; }
.pagination button { padding: 5px 14px; border: 1px solid var(--border, #e2e8f0); border-radius: 6px; background: #fff; cursor: pointer; }
.pagination button:disabled { opacity: .4; cursor: not-allowed; }
/* Modal */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 1000; display: flex; align-items: center; justify-content: center; }
.modal { background: #fff; border-radius: 12px; width: 640px; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,.2); }
.confirm-modal { width: 400px; }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px; border-bottom: 1px solid var(--border, #e2e8f0); font-weight: 600; font-size: 15px; }
.modal-close { background: none; border: none; font-size: 18px; cursor: pointer; color: #94a3b8; padding: 2px 6px; border-radius: 4px; }
.modal-close:hover { background: #f1f5f9; }
.modal-body { padding: 20px; }
.modal-footer { padding: 16px 20px; border-top: 1px solid var(--border, #e2e8f0); display: flex; justify-content: flex-end; gap: 10px; }
.form-row { display: flex; gap: 16px; margin-bottom: 14px; }
.form-item { flex: 1; display: flex; flex-direction: column; gap: 5px; }
.form-item.full { flex: none; width: 100%; }
.form-item label { font-size: 13px; color: #64748b; font-weight: 500; }
.form-item label em { color: #ef4444; margin-left: 2px; font-style: normal; }
.form-item input, .form-item select, .form-item textarea { padding: 8px 12px; border: 1px solid var(--border, #e2e8f0); border-radius: 7px; font-size: 14px; font-family: inherit; }
.form-item input:focus, .form-item select:focus, .form-item textarea:focus { outline: none; border-color: var(--primary, #2563eb); box-shadow: 0 0 0 3px rgba(37,99,235,.1); }
/* Toast */
.toast { position: fixed; bottom: 30px; right: 30px; z-index: 2000; padding: 12px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; box-shadow: 0 4px 16px rgba(0,0,0,.15); animation: slideUp .2s ease; }
.toast-success { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
.toast-error { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
@keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
</style>
