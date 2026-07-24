<template>
  <div class="breadcrumb">
    <div class="breadcrumb-item">智能体运营</div>
    <div class="breadcrumb-item">话术配置</div>
    <div class="breadcrumb-item active"><span class="crumb-dot"></span> 模板管理</div>
  </div>
  <div class="page">
    <!-- 筛选栏 -->
    <div class="filter-bar">
      <div class="filter-group"><label>意图</label><input class="filter-input" v-model="filter.name" placeholder="请输入意图" @input="debouncedLoad"></div>
      <div class="filter-group"><label>应用环节</label>
        <select class="filter-select" v-model="filter.stage" @change="loadTemplates">
          <option value="">全部</option><option>场景切入</option><option>套餐介绍</option><option>异议处理</option><option>促成成交</option>
        </select>
      </div>
      <div class="filter-group"><label>应用场景</label>
        <select class="filter-select" v-model="filter.scene" @change="loadTemplates">
          <option value="">全部</option><option>流量超套</option><option>套餐升级</option><option>套餐降档</option><option>新业务推荐</option>
        </select>
      </div>
      <div class="filter-group"><label>模板状态</label>
        <select class="filter-select" v-model="filter.status" @change="loadTemplates">
          <option value="">全部</option><option value="online">已上线</option><option value="offline">已下线</option>
        </select>
      </div>
      <div class="filter-group"><label>所属省份</label>
        <select class="filter-select" v-model="filter.province" @change="loadTemplates">
          <option value="">全部</option>
          <option v-for="o in provinceOptions" :key="o.province" :value="o.province">{{ o.label }}</option>
        </select>
      </div>
      <div class="filter-actions">
        <button class="btn btn-default" @click="resetFilters">重置</button>
        <button class="btn btn-default" @click="openImportModal">📥 导入话术模板</button>
        <button class="btn btn-primary" @click="openCreateModal">＋ 新建</button>
      </div>
    </div>

    <!-- 表格 -->
    <div class="table-card">
      <div class="table-wrap">
        <table>
          <thead><tr><th>省份</th><th>意图</th><th>产品</th><th>环节</th><th>应用场景</th><th>模板状态</th><th>创建时间</th><th>创建人</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-if="!tableItems.length" class="empty-row"><td colspan="9"><div class="empty-icon">📭</div><div>暂无话术模板</div></td></tr>
            <tr v-for="t in tableItems" :key="t.template_id">
              <td>{{ provMap[t.province] || t.province }}</td>
              <td class="td-name">{{ t.template_name }}</td>
              <td>
                <span v-if="!t._product_ids || !t._product_ids.length">—</span>
                <span v-else>
                  {{ t._product_ids.slice(0,3).join(', ') }}<span v-if="t._product_ids.length > 3" style="color:var(--muted)"> 等{{ t._product_ids.length }}个</span>
                </span>
              </td>
              <td>{{ t.stage || '—' }}</td>
              <td>{{ t.scene || '—' }}</td>
              <td>
                <label class="row-status-toggle">
                  <input type="checkbox" :checked="t.status==='online'" @change="toggleStatus(t.template_id, $event.target.checked)">
                  <span class="row-slider"></span>
                  <span class="row-status-label" :class="t.status==='online'?'status-online':'status-offline'">{{ t.status==='online'?'已上线':'已下线' }}</span>
                </label>
              </td>
              <td>{{ t.created_at || '—' }}</td>
              <td>{{ t.created_by || '—' }}</td>
              <td><div class="ops">
                <button class="btn-link" @click="viewTemplate(t)">查看</button>
                <template v-if="authStore.canWrite(t.province)">
                  <span style="color:#dee2e6">|</span>
                  <button class="btn-link" @click="openGroupEditModal(t)">编辑</button>
                  <span style="color:#dee2e6">|</span>
                  <button class="btn-link danger" @click="openDelModal(t)">删除</button>
                </template>
              </div></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination-bar">
        <div class="page-info">共 {{ totalItems }} 条</div>
        <div style="display:flex;align-items:center;gap:12px">
          <select class="page-size-select" v-model.number="pageSize" @change="fetchPage(1)">
            <option :value="10">10条/页</option><option :value="20">20条/页</option><option :value="50">50条/页</option>
          </select>
          <div class="page-btns">
            <button class="page-btn" :disabled="currentPage<=1" @click="fetchPage(currentPage-1)">‹</button>
            <button v-for="p in pageNums" :key="p" class="page-btn" :class="{active:p===currentPage}" :disabled="p==='...'" @click="p!=='...'&&fetchPage(p)">{{ p }}</button>
            <button class="page-btn" :disabled="currentPage>=totalPages" @click="fetchPage(currentPage+1)">›</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Toast -->
  <div class="toast-wrap">
    <div v-for="(t,i) in toasts" :key="i" class="toast" :class="t.ok?'toast-ok':'toast-err'">{{ t.msg }}</div>
  </div>

  <!-- 无权限弹窗 -->
  <div class="modal-mask" :class="{show:showPermDenied}" @click.self="showPermDenied=false">
    <div class="modal-box modal-sm">
      <div class="modal-header">
        <span class="modal-title" style="color:var(--danger)">🚫 操作受限</span>
        <button class="modal-close" @click="showPermDenied=false">×</button>
      </div>
      <div class="modal-body" style="padding:24px 20px;text-align:center">
        <div style="font-size:36px;margin-bottom:12px">🔒</div>
        <p style="font-size:14px;line-height:1.7;color:var(--text)">{{ permDeniedMsg }}</p>
      </div>
      <div class="modal-footer"><button class="btn btn-default" @click="showPermDenied=false">知道了</button></div>
    </div>
  </div>

  <!-- 编辑弹窗 -->
  <div class="modal-mask" :class="{show:showEdit}" @click.self="showEdit=false">
    <div class="modal-box modal-lg">
      <div class="modal-header"><span class="modal-title">{{ editingId?'编辑模板':'新建模板' }}</span><button class="modal-close" @click="showEdit=false">×</button></div>
      <div class="modal-body">
        <div class="form-row-2col">
          <div class="form-row"><label class="required">应用省份</label>
            <select class="form-control" v-model="editForm.province" @change="onProvinceChange">
              <option value="">请选择</option>
              <option v-for="o in editableProvinceOptions" :key="o.province" :value="o.province">{{ o.label }}</option>
            </select>
          </div>
          <div class="form-row"><label class="required">意图</label>
            <select class="form-control" v-model="editForm.intent">
              <option value="">请选择省份后加载</option>
              <option v-for="i in editIntents" :key="i" :value="i">{{ i }}</option>
            </select>
          </div>
        </div>
        <div class="form-row-2col">
          <div class="form-row"><label>产品 ID（留空=兜底）</label><input class="form-control" v-model="editForm.product_id" placeholder="留空则作为兜底模板"></div>
          <div class="form-row"><label>应用环节</label><input class="form-control" v-model="editForm.stage" list="stageList" placeholder="请选择或输入">
            <datalist id="stageList"><option>切入环节</option><option>推荐环节</option><option>异议处理</option><option>促成成交</option></datalist>
          </div>
        </div>
        <div class="form-row"><label>应用场景</label><input class="form-control" v-model="editForm.scene" list="sceneList" placeholder="留空不限">
          <datalist id="sceneList"><option>流量超套</option><option>套餐升级</option><option>套餐降档</option><option>新业务推荐</option></datalist>
        </div>
        <div class="form-row"><label class="required">模板内容</label>
          <p class="form-hint">话术模板文字，运行时关联变量、话术要求组合构成 Prompt</p>
          <textarea class="form-control tall" v-model="editForm.template_content" placeholder="请输入话术模板文字"></textarea>
        </div>

        <!-- 关联变量 -->
        <div class="form-row">
          <label>关联变量
            <span class="label-sub">（与 FlowContext 数据域一致，运行时加入 Prompt，差异表格仅在话术中展示，不打入 LLM）</span>
            <span v-if="suggestedEditVarKeys.size" class="label-suggest">💡 已根据模板占位符自动推荐勾选</span>
          </label>
          <div class="var-grid">
            <label v-for="v in dynamicVarList" :key="v.key" class="var-item"
              :class="{checked: editForm.linked_vars.includes(v.key), suggested: suggestedEditVarKeys.has(v.key) && !editForm.linked_vars.includes(v.key)}">
              <input type="checkbox" :value="v.key" v-model="editForm.linked_vars">
              <span class="var-name">{{ v.label }}</span>
              <span v-if="v.source === 'response_extract' || v.source === 'field_transform'" class="var-tag var-tag-api">接口</span>
              <span v-else-if="v.source === 'script_step'" class="var-tag var-tag-gen">生成</span>
              <span v-if="v.desc" class="var-desc">{{ v.desc }}</span>
            </label>
          </div>
          <div v-if="suggestedEditVarKeys.size" class="var-suggest-bar">
            <span>检测到占位符：{{ [...suggestedEditVarKeys].map(k => '{' + k + '}').join('、') }}</span>
            <button class="btn-text-link" style="margin-left:8px" @click="applyEditSuggest">一键勾选</button>
          </div>
        </div>

        <div class="form-row"><label>话术要求</label>
          <p class="form-hint">结合上下文中的当前套餐、历史用量与用户标签，先点出痛点再用推荐套餐真实字段值说明如何解决；只讲有数据支撑的卖点，口语化、150字以内，结尾引导办理。</p>
          <button class="btn-text-link" @click="resetScriptReq">恢复默认</button>
          <textarea class="form-control" v-model="editForm.script_requirement" rows="3"></textarea>
        </div>

        <!-- Prompt 预览 -->
        <div class="form-row">
          <div class="prompt-preview-header">
            <span @click="showPromptPreview=!showPromptPreview" style="cursor:pointer;flex:1">
              <span class="prompt-preview-icon">{{ showPromptPreview?'▲':'▼' }}</span>
              展开完整 Prompt（运行时真正发送给大模型，示例数据填充）
            </span>
            <button v-if="showPromptPreview" class="btn-text-link" :disabled="editPreviewLoading" @click="refreshEditPreview">刷新</button>
          </div>
          <div v-if="showPromptPreview" class="prompt-preview-body">
            <div v-if="editPreviewErr" class="prompt-preview-err">预览失败：{{ editPreviewErr }}（以下为本地近似）</div>
            <div class="prompt-preview-text">{{ promptPreviewText }}</div>
          </div>
        </div>

        <div class="form-row"><label>创建人</label>
          <input class="form-control" :value="authStore.username || 'admin'" disabled style="background:#f8f9fa;color:var(--muted)">
        </div>
        <div class="form-row"><label>模板状态</label>
          <div class="radio-group">
            <label class="radio-label"><input type="radio" v-model="editForm.status" value="online"> 上线</label>
            <label class="radio-label"><input type="radio" v-model="editForm.status" value="offline"> 下线</label>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-default" @click="showEdit=false">取消</button>
        <button class="btn btn-primary" @click="saveTemplate">保存</button>
      </div>
    </div>
  </div>

  <!-- 分组编辑弹窗（多产品） -->
  <div class="modal-mask" :class="{show:showGroupEdit}" @click.self="showGroupEdit=false">
    <div class="modal-box modal-lg">
      <div class="modal-header"><span class="modal-title">编辑模板（多产品）</span><button class="modal-close" @click="showGroupEdit=false">×</button></div>
      <div class="modal-body">
        <div class="form-row-2col">
          <div class="form-row"><label>应用省份</label><input class="form-control" :value="provMap[groupEditForm.province]||groupEditForm.province" disabled style="background:#f8f9fa"></div>
          <div class="form-row"><label>意图</label><input class="form-control" :value="groupEditForm.intent" disabled style="background:#f8f9fa"></div>
        </div>
        <div class="form-row">
          <label>产品 ID</label>
          <p class="form-hint">多个产品 ID 用逗号或换行分隔，留空代表兜底模板；匹配时仍按单个 ID 精确匹配</p>
          <textarea class="form-control" v-model="groupEditForm.product_ids_text" rows="3" placeholder="例：prod001, prod002, prod003"></textarea>
        </div>
        <div class="form-row-2col">
          <div class="form-row"><label>应用环节</label><input class="form-control" v-model="groupEditForm.stage" list="stageList2" placeholder="请选择或输入">
            <datalist id="stageList2"><option>切入环节</option><option>推荐环节</option><option>异议处理</option><option>促成成交</option></datalist>
          </div>
          <div class="form-row"><label>应用场景</label><input class="form-control" v-model="groupEditForm.scene" list="sceneList2" placeholder="留空不限">
            <datalist id="sceneList2"><option>流量超套</option><option>套餐升级</option><option>套餐降档</option><option>新业务推荐</option></datalist>
          </div>
        </div>
        <div class="form-row"><label class="required">模板内容</label>
          <textarea class="form-control tall" v-model="groupEditForm.template_content" placeholder="请输入话术模板文字"></textarea>
        </div>
        <!-- 关联变量 -->
        <div class="form-row">
          <label>关联变量
            <span class="label-sub">（与 FlowContext 数据域一致，运行时加入 Prompt，差异表格仅在话术中展示，不打入 LLM）</span>
            <span v-if="suggestedGroupVarKeys.size" class="label-suggest">💡 已根据模板占位符自动推荐勾选</span>
          </label>
          <div class="var-grid">
            <label v-for="v in dynamicVarList" :key="v.key" class="var-item"
              :class="{checked: groupEditForm.linked_vars.includes(v.key), suggested: suggestedGroupVarKeys.has(v.key) && !groupEditForm.linked_vars.includes(v.key)}">
              <input type="checkbox" :value="v.key" v-model="groupEditForm.linked_vars">
              <span class="var-name">{{ v.label }}</span>
              <span v-if="v.source === 'response_extract' || v.source === 'field_transform'" class="var-tag var-tag-api">接口</span>
              <span v-else-if="v.source === 'script_step'" class="var-tag var-tag-gen">生成</span>
              <span v-if="v.desc" class="var-desc">{{ v.desc }}</span>
            </label>
          </div>
          <div v-if="suggestedGroupVarKeys.size" class="var-suggest-bar">
            <span>检测到占位符：{{ [...suggestedGroupVarKeys].map(k => '{' + k + '}').join('、') }}</span>
            <button class="btn-text-link" style="margin-left:8px" @click="applyGroupSuggest">一键勾选</button>
          </div>
        </div>
        <div class="form-row"><label>话术要求</label>
          <textarea class="form-control" v-model="groupEditForm.script_requirement" rows="3"></textarea>
        </div>
        <!-- Prompt 预览 -->
        <div class="form-row">
          <div class="prompt-preview-header">
            <span @click="showGroupPromptPreview=!showGroupPromptPreview" style="cursor:pointer;flex:1">
              <span class="prompt-preview-icon">{{ showGroupPromptPreview?'▲':'▼' }}</span>
              展开完整 Prompt（运行时真正发送给大模型，示例数据填充）
            </span>
            <button v-if="showGroupPromptPreview" class="btn-text-link" :disabled="groupPreviewLoading" @click="refreshGroupPreview">刷新</button>
          </div>
          <div v-if="showGroupPromptPreview" class="prompt-preview-body">
            <div v-if="groupPreviewErr" class="prompt-preview-err">预览失败：{{ groupPreviewErr }}（以下为本地近似）</div>
            <div class="prompt-preview-text">{{ groupPromptPreviewText }}</div>
          </div>
        </div>

        <div class="form-row"><label>创建人</label>
          <input class="form-control" :value="authStore.username || 'admin'" disabled style="background:#f8f9fa;color:var(--muted)">
        </div>
        
        <div class="form-row"><label>模板状态</label>
          <div class="radio-group">
            <label class="radio-label"><input type="radio" v-model="groupEditForm.status" value="online"> 上线</label>
            <label class="radio-label"><input type="radio" v-model="groupEditForm.status" value="offline"> 下线</label>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-default" @click="showGroupEdit=false">取消</button>
        <button class="btn btn-primary" @click="saveGroupTemplate">保存</button>
      </div>
    </div>
  </div>

  <!-- 查看弹窗 -->
  <div class="modal-mask" :class="{show:showView}" @click.self="showView=false">
    <div class="modal-box">
      <div class="modal-header"><span class="modal-title">模板详情</span><button class="modal-close" @click="showView=false">×</button></div>
      <div class="modal-body" v-html="viewHtml"></div>
      <div class="modal-footer"><button class="btn btn-primary" @click="()=>{showView=false;openEditModal(viewItem)}">编辑</button></div>
    </div>
  </div>

  <!-- 删除弹窗 -->
  <div class="modal-mask" :class="{show:showDel}" @click.self="showDel=false">
    <div class="modal-box modal-sm">
      <div class="modal-header"><span class="modal-title">确认删除</span><button class="modal-close" @click="showDel=false">×</button></div>
      <div class="modal-body" style="padding:24px 20px"><p style="font-size:14px;line-height:1.7">确认删除模板 <strong>{{ delName }}</strong>？<br><span style="color:var(--danger);font-size:13px">此操作不可恢复。</span></p></div>
      <div class="modal-footer"><button class="btn btn-default" @click="showDel=false">取消</button><button class="btn btn-primary" style="background:var(--danger)" @click="confirmDelete">确认删除</button></div>
    </div>
  </div>

  <!-- 导入弹窗 -->
  <div class="modal-mask" :class="{show:showImport}" @click.self="showImport=false">
    <div class="modal-box" style="width:560px">
      <div class="modal-header"><span class="modal-title">📥 导入话术模板</span><button class="modal-close" @click="showImport=false">×</button></div>
      <div class="modal-body">
        <p style="font-size:13px;color:var(--muted);margin-bottom:8px">
          支持 CSV 和 Excel（.xlsx/.xls），列顺序：<strong>省份、意图、产品、环节、应用场景、话术内容</strong>
        </p>
        <p style="font-size:12px;color:var(--muted);margin-bottom:12px;line-height:1.6">
          · 产品列支持多个产品 ID，用换行或英文逗号分隔，每个产品单独创建一条模板<br>
          · 省份/意图/产品/环节/场景为空时自动沿用上一行的值（跨行合并）
        </p>
        <input type="file" accept=".csv,.xlsx,.xls" @change="onCsvFile" style="font-size:13px;width:100%">
        <div v-if="csvFile" style="margin-top:8px;font-size:12px;color:var(--muted)">已选择：{{ csvFile.name }}</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-default" @click="showImport=false">关闭</button>
        <button class="btn btn-primary" :disabled="!csvFile||importing||importDone" @click="doImport">{{ importDone?'✅ 已完成':importing?'导入中...':'确认导入' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { apiUrl, apiFetch } from '@/utils/apiUrl'
import { useAuthStore } from '@/stores/authStore'

const authStore = useAuthStore()

// 兜底静态变量列表（无接口配置时使用）
const FALLBACK_VAR_LIST = [
  { key: 'current_package', label: '当前套餐信息', desc: '{context.current_package}', source: 'fixed' },
  { key: 'usage',           label: '历史用量',     desc: '{context.usage}',           source: 'fixed' },
  { key: 'tags',            label: '用户标签',     desc: '{context.tags}',            source: 'fixed' },
  { key: 'user_info',       label: '用户基础信息', desc: '{context.user_info}',       source: 'fixed' },
  { key: 'user_profile',    label: '用户画像',     desc: '{context.user_profile}',    source: 'fixed' },
  { key: 'domain_ext',      label: '扩展信息',     desc: '{context.domain_ext}',      source: 'fixed' },
  { key: 'extra_info',      label: '主服务补充信息', desc: '{context.extra_info}',    source: 'fixed' },
  { key: 'extra_context',   label: '模板匹配上下文', desc: '{context.extra_context}', source: 'fixed' },
  { key: 'pkg_brief',       label: '推荐产品信息', desc: '{context.pkg_brief}',       source: 'script_step' },
  { key: 'diff_str',        label: '套餐差异',     desc: '{context.diff_str}',        source: 'script_step' },
  { key: 'table',           label: '差异表格',     desc: '（仅话术展示，不打入LLM）', source: 'script_step' },
]

// 动态变量列表（由省份+意图决定，从后端 context_vars 接口加载）
const dynamicVarList = ref([...FALLBACK_VAR_LIST])
// 当前加载了哪个意图的变量（避免重复请求）
const loadedVarKey = ref('')

// 从模板内容提取的占位符（推荐勾选提示）
function extractPlaceholders(content) {
  if (!content) return new Set()
  const matches = content.match(/\{(\w+)\}/g) || []
  return new Set(matches.map(m => m.slice(1, -1)))
}

// 加载指定省份+意图的可用变量列表
async function loadContextVars(province, intent) {
  const key = `${province}:${intent}`
  if (!province || !intent || loadedVarKey.value === key) return
  try {
    const res = await apiFetch(`/api/skills/${encodeURIComponent(province)}/${encodeURIComponent(intent)}/context_vars`)
    const json = await res.json()
    if (json.code === 200 && Array.isArray(json.data) && json.data.length) {
      dynamicVarList.value = json.data
      loadedVarKey.value = key
    }
  } catch (e) {
    // 加载失败静默降级到兜底列表
  }
}

const skillsList = ref([])
const rawItems = ref([])        // 后端返回的原始单条数据（全量）
const groupedItems = ref([])    // 分组合并后的全量组
const tableItems = ref([])      // 当前页展示的组
const totalItems = ref(0)       // 组数（非原始条数）
const totalPages = ref(1)
const currentPage = ref(1)
const pageSize = ref(20)
const toasts = ref([])
const provMap = reactive({})

const filter = reactive({ name:'', stage:'', scene:'', status:'', province:'' })

// 分组编辑（多产品）
const showGroupEdit = ref(false)
const groupEditGroup = ref(null)  // 合并行对象
const groupEditForm = reactive({ province:'', intent:'', product_ids_text:'', stage:'', scene:'', template_content:'', script_requirement: '', linked_vars: [], status:'online' })

// 编辑
const showEdit = ref(false)
const editingId = ref(null)
const editIntents = ref([])
const showPromptPreview = ref(false)
const showGroupPromptPreview = ref(false)
const DEFAULT_SCRIPT_REQ = '结合【上下文数据】中的当前套餐、历史用量与用户标签，先点出最突出的用户痛点，再用推荐套餐对应字段的真实值说明如何解决；只讲有数据支撑的卖点，口语化、可直接对客播报，150字以内，结尾自然引导办理。'
const editForm = reactive({ province:'', intent:'', product_id:'', stage:'', scene:'', template_content:'', script_requirement: DEFAULT_SCRIPT_REQ, created_by:'admin', status:'online', linked_vars: [] })

// ── 调后端 infer_vars 接口推断推荐变量 ─────────────────────────
const suggestedEditVarKeys = ref(new Set())
const suggestedGroupVarKeys = ref(new Set())

let inferEditTimer = null
let inferGroupTimer = null

async function inferVars(content) {
  if (!content || !content.trim()) return []
  try {
    const res = await apiFetch('/api/templates/infer_vars', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_content: content }),
    })
    const json = await res.json()
    return (json.code === 200 && json.data?.linked_vars) ? json.data.linked_vars : []
  } catch { return [] }
}

// 编辑弹窗：模板内容变化时防抖调用推断
watch(() => editForm.template_content, (val) => {
  clearTimeout(inferEditTimer)
  inferEditTimer = setTimeout(async () => {
    const vars = await inferVars(val)
    suggestedEditVarKeys.value = new Set(vars)
  }, 800)
})

// 分组编辑弹窗：模板内容变化时防抖调用推断
watch(() => groupEditForm.template_content, (val) => {
  clearTimeout(inferGroupTimer)
  inferGroupTimer = setTimeout(async () => {
    const vars = await inferVars(val)
    suggestedGroupVarKeys.value = new Set(vars)
  }, 800)
})

function applyEditSuggest() {
  for (const k of suggestedEditVarKeys.value) {
    if (!editForm.linked_vars.includes(k)) editForm.linked_vars.push(k)
  }
}
function applyGroupSuggest() {
  for (const k of suggestedGroupVarKeys.value) {
    if (!groupEditForm.linked_vars.includes(k)) groupEditForm.linked_vars.push(k)
  }
}

// ── 完整 Prompt 预览（调后端 /api/templates/preview_prompt，与运行态 build_prompt 同一路径）──
// 本地近似（后端不可用时兜底）
function _localPreview(f) {
  const selected = dynamicVarList.value.filter(v => f.linked_vars.includes(v.key))
  const parts = []
  if (selected.length) {
    parts.push('【关联变量上下文】\n' + selected.map(v => `  [${v.label}] → ${v.desc}`).join('\n'))
  }
  parts.push('【话术模板】\n' + (f.template_content || '（请填写模板内容）'))
  if (f.script_requirement) parts.push('【输出要求】\n' + f.script_requirement)
  return parts.join('\n\n')
}

async function fetchPreview(f) {
  const res = await apiFetch('/api/templates/preview_prompt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      template: {
        template_name:      f.template_name || f.intent || '',
        template_content:   f.template_content || '',
        prompt_template:    f.prompt_template || '',
        linked_vars:        [...f.linked_vars],
        script_requirement: f.script_requirement || '',
        scene:              f.scene || '',
        stage:              f.stage || '',
        product_id:         f.product_id || '',
        intent:             f.intent || f.template_name || '',
        province:           f.province || '',
      },
      province: f.province || '',
      intent: f.intent || f.template_name || '',
    }),
  })
  const json = await res.json()
  if (json.code === 200 && json.data && typeof json.data.prompt === 'string') return json.data.prompt
  throw new Error(json.detail || json.message || '未知错误')
}

// 编辑弹窗预览
const editPreviewText = ref('')
const editPreviewLoading = ref(false)
const editPreviewErr = ref('')
let _editPreviewTimer = null
const promptPreviewText = computed(() =>
  editPreviewText.value || (editPreviewLoading.value ? '生成中…' : _localPreview(editForm)))
async function refreshEditPreview() {
  if (!showPromptPreview.value) return
  editPreviewLoading.value = true; editPreviewErr.value = ''
  try { editPreviewText.value = await fetchPreview(editForm) }
  catch (e) { editPreviewErr.value = e?.message || String(e); editPreviewText.value = '' }
  finally { editPreviewLoading.value = false }
}
function scheduleEditPreview() { clearTimeout(_editPreviewTimer); _editPreviewTimer = setTimeout(refreshEditPreview, 500) }

// 分组编辑弹窗预览
const groupPreviewText = ref('')
const groupPreviewLoading = ref(false)
const groupPreviewErr = ref('')
let _groupPreviewTimer = null
const groupPromptPreviewText = computed(() =>
  groupPreviewText.value || (groupPreviewLoading.value ? '生成中…' : _localPreview(groupEditForm)))
async function refreshGroupPreview() {
  if (!showGroupPromptPreview.value) return
  groupPreviewLoading.value = true; groupPreviewErr.value = ''
  try { groupPreviewText.value = await fetchPreview(groupEditForm) }
  catch (e) { groupPreviewErr.value = e?.message || String(e); groupPreviewText.value = '' }
  finally { groupPreviewLoading.value = false }
}
function scheduleGroupPreview() { clearTimeout(_groupPreviewTimer); _groupPreviewTimer = setTimeout(refreshGroupPreview, 500) }

watch(
  () => [showEdit.value, showPromptPreview.value, editForm.template_content, editForm.script_requirement, editForm.linked_vars.join('|'), editForm.province, editForm.intent],
  () => { if (showEdit.value && showPromptPreview.value) scheduleEditPreview() },
)
watch(
  () => [showGroupEdit.value, showGroupPromptPreview.value, groupEditForm.template_content, groupEditForm.script_requirement, groupEditForm.linked_vars.join('|'), groupEditForm.province, groupEditForm.intent],
  () => { if (showGroupEdit.value && showGroupPromptPreview.value) scheduleGroupPreview() },
)

function resetScriptReq() {
  editForm.script_requirement = DEFAULT_SCRIPT_REQ
}

// watch：编辑弹窗选完意图后自动加载可用变量
watch(() => [editForm.province, editForm.intent], ([p, i]) => {
  if (p && i) loadContextVars(p, i)
})
// watch：分组编辑弹窗打开时加载可用变量
watch(() => [groupEditForm.province, groupEditForm.intent], ([p, i]) => {
  if (p && i) loadContextVars(p, i)
})

// 查看
const showView = ref(false)
const viewHtml = ref('')
const viewItem = ref(null)

// 删除
const showDel = ref(false)
const delName = ref('')
const deletingId = ref(null)

// 导入
const showImport = ref(false)
const csvFile = ref(null)
const importing = ref(false)
const importDone = ref(false)
const importResult = ref(null)

// 权限拦截弹窗
const showPermDenied = ref(false)
const permDeniedMsg = ref('')
function showPermDeniedDialog(msg) {
  permDeniedMsg.value = msg || '您没有权限对其他省份的数据进行此操作。'
  showPermDenied.value = true
}

const pageNums = computed(() => {
  const pages = [], tp = totalPages.value, cp = currentPage.value
  for (let i = 1; i <= tp; i++) {
    if (tp <= 7 || i === 1 || i === tp || Math.abs(i - cp) <= 1) pages.push(i)
    else if (Math.abs(i - cp) === 2 && pages[pages.length-1] !== '...') pages.push('...')
  }
  return pages
})

/** /api/skills 每条为 province+intent，省份下拉需去重（查询用，全部省份） */
const provinceOptions = computed(() => {
  const seen = new Set()
  const out = []
  for (const s of skillsList.value) {
    const p = s.province
    if (!p || seen.has(p)) continue
    seen.add(p)
    out.push({ province: p, label: s.meta?.province_name || p })
  }
  return out
})

/** 新建/编辑弹窗中的省份下拉：非本部用户只能选自己省份 */
const editableProvinceOptions = computed(() => {
  if (authStore.isHQ) return provinceOptions.value
  return provinceOptions.value.filter(o => o.province === authStore.province)
})

let debounceTimer = null
function debouncedLoad() { clearTimeout(debounceTimer); debounceTimer = setTimeout(loadTemplates, 400) }

async function loadSkills() {
    const res = await apiFetch('/api/skills')
  const json = await res.json()
  skillsList.value = json.data || []
  const BUILTIN = {beijing:'北京',shanghai:'上海',guangdong:'广东',zhejiang:'浙江',jiangsu:'江苏'}
  skillsList.value.forEach(s => {
    provMap[s.province] = s.meta?.province_name || BUILTIN[s.province] || s.province
  })
  Object.assign(provMap, BUILTIN)
}

async function loadTemplates() { currentPage.value = 1; await fetchPage(1) }

async function fetchPage(page) {
  currentPage.value = page
  // 分页基于分组后的条数，一次拉全量再前端分页
  // 用足够大的 page_size 拉取所有原始记录
  const params = new URLSearchParams({ page: 1, page_size: 2000 })
  if (filter.name) params.set('name', filter.name)
  if (filter.stage) params.set('stage', filter.stage)
  if (filter.scene) params.set('scene', filter.scene)
  if (filter.status) params.set('status', filter.status)
  if (filter.province) params.set('province', filter.province)
  try {
    const res = await apiFetch('/api/templates?' + params)
    const json = await res.json()
    const data = json.data || { total: 0, items: [] }
    rawItems.value = data.items
    groupedItems.value = mergeByGroup(data.items)
    totalItems.value = groupedItems.value.length
    totalPages.value = Math.ceil(totalItems.value / pageSize.value) || 1
    // 确保 page 不超出范围
    if (currentPage.value > totalPages.value) currentPage.value = totalPages.value || 1
    const start = (currentPage.value - 1) * pageSize.value
    tableItems.value = groupedItems.value.slice(start, start + pageSize.value)
  } catch(e) { showToast('加载失败: ' + e.message, false) }
}

/** 按 province+intent+stage+scene+template_content 分组，合并 product_id 为逗号列表 */
function mergeByGroup(items) {
  const map = new Map()
  for (const t of items) {
    const key = [t.province, t.intent||t.template_name, t.stage, t.scene, t.template_content].join('\x00')
    if (!map.has(key)) {
      map.set(key, { ...t, _product_ids: t.product_id ? [t.product_id] : [], _template_ids: [t.template_id] })
    } else {
      const g = map.get(key)
      if (t.product_id && !g._product_ids.includes(t.product_id)) g._product_ids.push(t.product_id)
      g._template_ids.push(t.template_id)
    }
  }
  return [...map.values()]
}

function resetFilters() {
  Object.assign(filter, { name:'', stage:'', scene:'', status:'', province:'' })
  loadTemplates()
}

function onProvinceChange() {
  editIntents.value = [...new Set(skillsList.value.filter(s => s.province === editForm.province).map(s => s.intent))]
  editForm.intent = ''
}

function openCreateModal() {
  editingId.value = null
  // 非本部用户自动锁定省份
  const defaultProvince = authStore.isHQ ? '' : (authStore.province || '')
  Object.assign(editForm, { province: defaultProvince, intent:'', product_id:'', stage:'', scene:'', template_content:'', script_requirement: DEFAULT_SCRIPT_REQ, created_by: authStore.username || 'admin', status:'online', linked_vars: [] })
  if (defaultProvince) {
    editIntents.value = [...new Set(skillsList.value.filter(s => s.province === defaultProvince).map(s => s.intent))]
  } else {
    editIntents.value = []
  }
  showEdit.value = true
}

function openEditModal(tpl) {
  editingId.value = tpl.template_id
  Object.assign(editForm, { province: tpl.province||'', intent: tpl.intent||tpl.template_name||'', product_id: tpl.product_id||'', stage: tpl.stage||'', scene: tpl.scene||'', template_content: tpl.template_content||'', script_requirement: tpl.script_requirement||'', created_by: authStore.username || 'admin', status: tpl.status||'online', linked_vars: Array.isArray(tpl.linked_vars) ? [...tpl.linked_vars] : [] })
  editIntents.value = [...new Set(skillsList.value.filter(s => s.province === tpl.province).map(s => s.intent))]
  showEdit.value = true
}

async function saveTemplate() {
  if (!editForm.template_content.trim()) { showToast('请填写模板内容', false); return }
  const body = { template_name: editForm.intent, scene: editForm.scene, stage: editForm.stage, product_id: editForm.product_id, template_content: editForm.template_content, script_requirement: editForm.script_requirement, linked_vars: editForm.linked_vars, linked_apis: [], status: editForm.status, created_by: authStore.username || 'admin' }
  try {
    let res
    if (editingId.value) {
      res = await apiFetch(`/api/templates/${encodeURIComponent(editingId.value)}`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) })
    } else {
      if (!editForm.province) { showToast('请选择应用省份', false); return }
      if (!editForm.intent) { showToast('请选择意图', false); return }
      res = await apiFetch('/api/templates', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ province: editForm.province, intent: editForm.intent, ...body }) })
    }
    if (res.status === 403) {
      const json = await res.json().catch(() => ({}))
      showEdit.value = false
      showPermDeniedDialog(json.detail || '您没有权限对其他省份的数据进行此操作。')
      return
    }
    const json = await res.json()
    if (json.code === 200) { showEdit.value = false; showToast(editingId.value?'✅ 更新成功':'✅ 创建成功', true); await fetchPage(currentPage.value) }
    else showToast('❌ ' + (json.detail||json.message||'操作失败'), false)
  } catch(e) { showToast('❌ 网络错误: ' + e.message, false) }
}

const LINKED_VAR_LABELS = {
  current_package: '当前套餐信息', usage: '历史用量', tags: '用户标签', user_info: '用户基础信息',
  user_profile: '用户画像', domain_ext: '扩展信息', extra_info: '主服务补充信息(extra_info)',
  extra_context: '模板匹配上下文(extra_context)', pkg_brief: '推荐产品信息', diff_str: '套餐差异', table: '差异表格',
}

function viewTemplate(tpl) {
  viewItem.value = tpl
  const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  const statusText = tpl.status==='online' ? '<span class="status-badge status-online"><span class="dot dot-online"></span>已上线</span>' : '<span class="status-badge status-offline"><span class="dot dot-offline"></span>已下线</span>'
  const apis = (tpl.linked_apis || []).join('、') || '—'
  const linkedVars = (tpl.linked_vars || []).map(v => LINKED_VAR_LABELS[v] || v).join('、') || '—'
  viewHtml.value = `
    <div class="detail-row"><span class="detail-label">意图</span><span class="detail-value">${esc(tpl.intent_label || tpl.intent || tpl.template_name || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">应用省份</span><span class="detail-value">${esc(provMap[tpl.province] || tpl.province || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">应用环节</span><span class="detail-value">${esc(tpl.stage || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">应用场景</span><span class="detail-value">${esc(tpl.scene || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">产品 ID</span><span class="detail-value">${esc((tpl._product_ids && tpl._product_ids.length) ? tpl._product_ids.join('\n') : (tpl.product_id || '（兜底模板）'))}</span></div>
    <div class="detail-row"><span class="detail-label">模板内容</span><span class="detail-value code">${esc(tpl.template_content || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">关联变量</span><span class="detail-value">${esc(linkedVars)}</span></div>
    <div class="detail-row"><span class="detail-label">话术要求</span><span class="detail-value code">${esc(tpl.script_requirement || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">关联接口</span><span class="detail-value">${esc(apis)}</span></div>
    <div class="detail-row"><span class="detail-label">模板状态</span><span class="detail-value">${statusText}</span></div>
    <div class="detail-row"><span class="detail-label">创建时间</span><span class="detail-value">${esc(tpl.created_at || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">创建人</span><span class="detail-value">${esc(tpl.created_by || '—')}</span></div>`
  showView.value = true
}

/** 打开分组编辑弹窗（多产品合并行） */
function openGroupEditModal(group) {
  groupEditGroup.value = group
  const pids = (group._product_ids || []).filter(p => p)
  groupEditForm.province = group.province || ''
  groupEditForm.intent = group.intent || group.template_name || ''
  groupEditForm.product_ids_text = pids.join(', ')
  groupEditForm.stage = group.stage || ''
  groupEditForm.scene = group.scene || ''
  groupEditForm.template_content = group.template_content || ''
  groupEditForm.script_requirement = group.script_requirement || ''
  groupEditForm.linked_vars = Array.isArray(group.linked_vars) ? [...group.linked_vars] : []
  groupEditForm.status = group.status || 'online'
  showGroupEdit.value = true
}

/** 保存分组编辑：对比旧产品ID列表，增/删/改对应的单条模板 */
async function saveGroupTemplate() {
  if (!groupEditForm.template_content.trim()) { showToast('请填写模板内容', false); return }
  const group = groupEditGroup.value
  const province = group.province
  const intent = group.intent || group.template_name

  // 新产品 ID 列表（空字符串代表兜底）
  const newPids = groupEditForm.product_ids_text
    .split(/[,\n]+/)
    .map(p => p.trim())
    .filter((p, i, arr) => arr.indexOf(p) === i)
  if (!newPids.length) newPids.push('')

  const oldPids = group._product_ids || []
  const oldTplIds = group._template_ids || []

  // 找到每个 oldPid 对应的 template_id
  const pidToTplId = {}
  for (const raw of rawItems.value) {
    const key = raw.province + '|' + (raw.intent||raw.template_name) + '|' + (raw.stage||'') + '|' + (raw.scene||'') + '|' + (raw.template_content||'')
    const gkey = province + '|' + intent + '|' + (group.stage||'') + '|' + (group.scene||'') + '|' + (group.template_content||'')
    if (key === gkey) pidToTplId[raw.product_id || ''] = raw.template_id
  }

  const baseBody = {
    template_name: intent, stage: groupEditForm.stage, scene: groupEditForm.scene,
    template_content: groupEditForm.template_content,
    script_requirement: groupEditForm.script_requirement,
    linked_vars: groupEditForm.linked_vars,
    linked_apis: [], status: groupEditForm.status,
    created_by: authStore.username || 'admin',
  }

  try {
    // 更新或新建
    for (const pid of newPids) {
      const existTplId = pidToTplId[pid]
      if (existTplId) {
        await apiFetch(`/api/templates/${encodeURIComponent(existTplId)}`, {
          method: 'PUT', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ ...baseBody, product_id: pid }),
        })
      } else {
        await apiFetch('/api/templates', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ province, intent, ...baseBody, product_id: pid }),
        })
      }
    }
    // 删除去掉的 product_id
    for (const pid of oldPids) {
      if (!newPids.includes(pid)) {
        const tid = pidToTplId[pid]
        if (tid) await apiFetch(`/api/templates/${encodeURIComponent(tid)}`, { method:'DELETE' })
      }
    }
    showGroupEdit.value = false
    showToast('✅ 更新成功', true)
    await fetchPage(currentPage.value)
  } catch(e) { showToast('❌ ' + e.message, false) }
}

function openDelModal(tpl) { deletingId.value = tpl.template_id; delName.value = `「${tpl.template_name}」`; showDel.value = true }

async function confirmDelete() {
  if (!deletingId.value) return
  try {
    const res = await apiFetch(`/api/templates/${encodeURIComponent(deletingId.value)}`, { method:'DELETE' })
    if (res.status === 403) {
      const json = await res.json().catch(() => ({}))
      showDel.value = false
      showPermDeniedDialog(json.detail || '您没有权限删除其他省份的数据。')
      return
    }
    const json = await res.json()
    if (json.code === 200) { showDel.value = false; showToast('✅ 删除成功', true); await fetchPage(currentPage.value) }
    else showToast('❌ ' + (json.detail||json.message||'删除失败'), false)
  } catch(e) { showToast('❌ 网络错误: ' + e.message, false) }
}

async function toggleStatus(id, checked) {
  try {
    const res = await apiFetch(`/api/templates/${encodeURIComponent(id)}/status`, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ status: checked?'online':'offline' }) })
    if (res.status === 403) {
      const json = await res.json().catch(() => ({}))
      showPermDeniedDialog(json.detail || '您没有权限修改其他省份的数据状态。')
      await fetchPage(currentPage.value) // 恢复开关状态
      return
    }
    const json = await res.json()
    if (json.code === 200) { showToast('✅ 状态已更新', true); await fetchPage(currentPage.value) }
    else showToast('❌ ' + (json.detail||json.message||'更新失败'), false)
  } catch(e) { showToast('❌ ' + e.message, false) }
}

function openImportModal() { csvFile.value = null; importResult.value = null; importDone.value = false; showImport.value = true }
function onCsvFile(e) { csvFile.value = e.target.files[0] || null; importResult.value = null; importDone.value = false }
async function doImport() {
  if (!csvFile.value) return
  importing.value = true
  importDone.value = false
  const fd = new FormData(); fd.append('file', csvFile.value)
  try {
    const res = await apiFetch('/api/templates/import', { method:'POST', body: fd })
    const json = await res.json()
    const errors = (json.data && json.data.errors) || []
    if (json.code===200) {
      importDone.value = true
      await fetchPage(1)
      showToast('✅ ' + json.message, true)
      if (errors.length) showToast('⚠️ 部分行失败: ' + errors.slice(0,3).join(' | '), false)
      // 1.5s 后自动关闭弹窗
      setTimeout(() => { showImport.value = false; importDone.value = false }, 1500)
    } else {
      showToast('❌ ' + (json.message || '导入失败'), false)
      if (errors.length) showToast('⚠️ ' + errors.slice(0,3).join(' | '), false)
    }
  } catch(e) { showToast('❌ 网络错误: ' + e.message, false) }
  finally { importing.value = false }
}

function showToast(msg, ok) {
  const t = { msg, ok }; toasts.value.push(t)
  setTimeout(() => { toasts.value = toasts.value.filter(x => x !== t) }, 2000)
}

onMounted(async () => { await loadSkills(); await loadTemplates() })
</script>

<style scoped>
.breadcrumb{background:#fff;border-bottom:1px solid var(--border);padding:0 24px;display:flex;align-items:center;height:42px}
.breadcrumb-item{color:var(--muted);font-size:13px;display:flex;align-items:center;gap:6px}
.breadcrumb-item.active{color:var(--primary);font-weight:600}
.breadcrumb-item::after{content:'>';margin:0 8px;color:#ced4da;font-size:11px}
.breadcrumb-item:last-child::after{display:none}
.crumb-dot{width:6px;height:6px;border-radius:50%;background:var(--primary)}
.page{padding:20px 24px}
.filter-bar{background:var(--card);border-radius:var(--radius);padding:16px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;box-shadow:var(--shadow);margin-bottom:16px}
.filter-group{display:flex;align-items:center;gap:8px}
.filter-group label{font-size:13px;white-space:nowrap;font-weight:500}
.filter-input,.filter-select{height:34px;padding:0 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;outline:none;background:#fff;transition:.2s}
.filter-input{width:160px}.filter-select{width:130px;cursor:pointer}
.filter-input:focus,.filter-select:focus{border-color:var(--primary);box-shadow:0 0 0 2px rgba(59,91,219,.15)}
.filter-actions{margin-left:auto;display:flex;gap:8px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:34px;padding:0 16px;border:none;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;transition:.2s;white-space:nowrap}
.btn-default{background:#f1f3f5;color:var(--text)}.btn-default:hover{background:#e9ecef}
.btn-primary{background:var(--primary);color:#fff}.btn-primary:hover{background:var(--primary-hover)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.table-card{background:var(--card);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse}
thead{background:#f8f9fa}
th{padding:11px 14px;text-align:left;font-size:13px;font-weight:600;color:#495057;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:11px 14px;font-size:13px;border-bottom:1px solid #f1f3f5;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f8f9fa}
.td-name{font-weight:500}
.ops{display:flex;gap:2px}
.btn-link{background:transparent;color:var(--primary);font-size:13px;cursor:pointer;border:none;padding:0 4px}
.btn-link:hover{text-decoration:underline}
.btn-link.danger{color:var(--danger)}
.status-badge{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:500}
.status-online{background:#d3f9d8;color:#2b8a3e}
.status-offline{background:#f1f3f5;color:#868e96}
.dot{width:5px;height:5px;border-radius:50%;display:inline-block}
.dot-online{background:#2b8a3e}.dot-offline{background:#868e96}
.row-status-toggle{display:inline-flex;align-items:center;gap:5px;cursor:pointer;user-select:none}
.row-status-toggle input{display:none}
.row-slider{width:30px;height:16px;background:#ced4da;border-radius:8px;position:relative;transition:.2s;flex-shrink:0}
.row-slider::after{content:'';position:absolute;left:2px;top:2px;width:12px;height:12px;background:#fff;border-radius:50%;transition:.2s}
.row-status-toggle input:checked + .row-slider{background:#2b8a3e}
.row-status-toggle input:checked + .row-slider::after{left:16px}
.row-status-label{font-size:12px}
.pagination-bar{padding:12px 20px;display:flex;align-items:center;justify-content:space-between;border-top:1px solid var(--border);background:#fafafa}
.page-info{font-size:13px;color:var(--muted)}
.page-btns{display:flex;align-items:center;gap:4px}
.page-btn{min-width:30px;height:30px;padding:0 8px;border:1px solid var(--border);background:#fff;border-radius:5px;font-size:13px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s}
.page-btn:hover:not(:disabled){border-color:var(--primary);color:var(--primary)}
.page-btn.active{background:var(--primary);color:#fff;border-color:var(--primary)}
.page-btn:disabled{opacity:.4;cursor:not-allowed}
.page-size-select{height:30px;padding:0 6px;border:1px solid var(--border);border-radius:5px;font-size:13px;cursor:pointer;outline:none}
.empty-row td{text-align:center;padding:48px;color:var(--muted)}
.empty-icon{font-size:36px;margin-bottom:8px}
.toast-wrap{position:fixed;top:60px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px}
.toast{padding:10px 18px;border-radius:7px;font-size:13px;font-weight:500;box-shadow:0 4px 16px rgba(0,0,0,.15)}
.toast-ok{background:#2f9e44;color:#fff}.toast-err{background:#c92a2a;color:#fff}
.modal-mask{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:1000;display:none;align-items:center;justify-content:center}
.modal-mask.show{display:flex}
.modal-box{background:#fff;border-radius:10px;width:560px;max-width:96vw;max-height:90vh;overflow-y:auto;box-shadow:0 8px 40px rgba(0,0,0,.2);display:flex;flex-direction:column}
.modal-box.modal-sm{width:420px}
.modal-box.modal-lg{width:720px}
.modal-header{padding:18px 20px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.modal-title{font-size:15px;font-weight:600}
.modal-close{background:none;border:none;font-size:20px;cursor:pointer;color:var(--muted)}
.modal-close:hover{color:var(--text)}
.modal-body{padding:18px 20px;flex:1;overflow-y:auto}
.modal-footer{padding:12px 20px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;gap:10px;flex-shrink:0}
.form-row{margin-bottom:14px}
.form-row label{display:block;font-size:13px;font-weight:500;margin-bottom:5px}
.form-row .required::after{content:' *';color:var(--danger)}
.form-control{width:100%;height:36px;padding:0 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;outline:none;transition:.2s;font-family:inherit}
.form-control:focus{border-color:var(--primary);box-shadow:0 0 0 2px rgba(59,91,219,.15)}
textarea.form-control{height:auto;min-height:100px;padding:8px 10px;resize:vertical;line-height:1.6}
textarea.form-control.tall{min-height:150px}
.form-row-2col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.radio-group{display:flex;gap:16px;align-items:center}
.radio-label{display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px}
.modal-body :deep(.detail-row){margin-bottom:14px;display:grid;grid-template-columns:90px 1fr;gap:8px;align-items:start}
.modal-body :deep(.detail-label){font-size:13px;font-weight:500;color:var(--muted);padding-top:2px;text-align:right}
.modal-body :deep(.detail-value){font-size:13px;color:var(--text);white-space:pre-wrap;word-break:break-word;line-height:1.6}
.modal-body :deep(.detail-value.code){font-family:inherit;font-size:13px;background:#f8f9fa;padding:8px 10px;border-radius:6px;border:1px solid var(--border);line-height:1.6}
.modal-body :deep(.status-badge){display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:500}
.modal-body :deep(.status-online){background:#d3f9d8;color:#2b8a3e}
.modal-body :deep(.status-offline){background:#f1f3f5;color:#868e96}
.modal-body :deep(.dot){width:5px;height:5px;border-radius:50%;display:inline-block}
.modal-body :deep(.dot-online){background:#2b8a3e}
.modal-body :deep(.dot-offline){background:#868e96}

/* 表单辅助文字 */
.form-hint{font-size:12px;color:var(--muted);margin:0 0 6px;line-height:1.5}
.label-sub{font-size:11px;color:var(--muted);font-weight:400;margin-left:4px}

/* 恢复默认按钮 */
.btn-text-link{background:none;border:none;color:var(--primary);font-size:12px;cursor:pointer;padding:0;margin-bottom:6px;text-decoration:underline}
.btn-text-link:hover{color:var(--primary-hover)}

/* 关联变量网格 */
.var-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px;margin-top:6px}
.var-item{display:flex;align-items:flex-start;gap:6px;padding:7px 10px;border:1px solid var(--border);border-radius:6px;cursor:pointer;transition:.15s;background:#fff}
.var-item:hover{border-color:var(--primary);background:#f0f4ff}
.var-item.checked{border-color:var(--primary);background:#eef2ff}
.var-item input[type=checkbox]{margin-top:2px;flex-shrink:0;accent-color:var(--primary)}
.var-name{font-size:13px;font-weight:500;color:var(--text)}
.var-desc{font-size:11px;color:var(--muted);margin-left:auto;white-space:nowrap}
/* 推荐勾选高亮 */
.var-item.suggested{border-color:#fd7e14;background:#fff8f0}
.var-item.suggested:hover{background:#fff3e0}
/* 变量来源标签 */
.var-tag{font-size:10px;padding:1px 5px;border-radius:3px;font-weight:500;flex-shrink:0;margin-left:2px}
.var-tag-api{background:#d0ebff;color:#1864ab}
.var-tag-gen{background:#d3f9d8;color:#2b8a3e}
/* 推荐提示 */
.label-suggest{font-size:11px;color:#fd7e14;font-weight:400;margin-left:8px}
.var-suggest-bar{margin-top:6px;padding:6px 10px;background:#fff8f0;border:1px dashed #fd7e14;border-radius:5px;font-size:12px;color:#e67700;display:flex;align-items:center}

/* Prompt 预览 */
.prompt-preview-header{display:flex;align-items:center;gap:6px;cursor:pointer;padding:8px 10px;background:#f8f9fa;border:1px solid var(--border);border-radius:6px;font-size:13px;color:var(--primary);user-select:none;transition:.15s}
.prompt-preview-header:hover{background:#eef2ff}
.prompt-preview-icon{font-size:11px}
.prompt-preview-body{margin-top:6px;border:1px solid var(--border);border-radius:6px;overflow:hidden}
.prompt-preview-empty{padding:10px 12px;font-size:12px;color:var(--muted);font-style:italic}
.prompt-preview-text{padding:10px 12px;font-size:12px;color:#495057;background:#fffbf0;border-bottom:1px solid var(--border);white-space:pre-wrap;line-height:1.7}
.prompt-preview-err{padding:6px 12px;font-size:12px;color:#c92a2a;background:#fff5f5;border-bottom:1px solid #ffc9c9}
.prompt-preview-final{padding:10px 12px;font-size:13px;color:var(--text);background:#fff;white-space:pre-wrap;line-height:1.7;max-height:120px;overflow-y:auto}
</style>
