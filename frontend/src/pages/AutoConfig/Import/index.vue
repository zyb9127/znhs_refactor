<template>
  <div>
    <!-- 顶部环境提示条 + 面包屑（与 SkillManager 统一） -->
    <EnvBanner />
    <div class="page-header-row" style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
      <div class="breadcrumb" style="margin-bottom:0;">
        <div class="breadcrumb-item" style="cursor:pointer;" @click="$router.push('/SkillManager')">话术智能体运营</div>
        <div class="breadcrumb-item active"><span class="crumb-dot"></span>创建智能话术配置</div>
      </div>
      <el-button size="small" plain @click="$router.push('/SkillManager')">
        <el-icon><ArrowLeft /></el-icon>&nbsp;返回上一级
      </el-button>
    </div>

    <!-- 步骤条 -->
    <div class="step-bar" style="max-width:520px;margin:0 auto 28px;">
      <div v-for="(s, i) in steps" :key="s.key"
        class="step-item"
        :class="{ active: step === s.key, done: doneStages[s.key] }"
        style="flex:1;min-width:80px;"
      >
        <div class="step-circle">
          <el-icon v-if="doneStages[s.key]"><Check /></el-icon>
          <span v-else>{{ i + 1 }}</span>
        </div>
        <div class="step-label">{{ s.label }}</div>
      </div>
    </div>

    <!-- ══════════ 第一步：步骤创建 ══════════ -->
    <div v-show="step === 'config'">
      <div class="wizard-banner">
        <span class="wizard-banner-icon">🛠️</span>
        <div>
          <div class="wizard-banner-title">创建智能话术配置三步走：① 基本信息 → ② 接口配置 · 标准数据关联 · 话术模板 → ③ 预览发布</div>
          <div class="wizard-banner-sub">
            编辑界面与 <strong>智能话术配置管理 → 编辑</strong> 完全一致，分三个 Tab 操作：
            <strong>接口配置</strong>（接口地址 + 出参映射到标准数据域）→
            <strong>话术模板</strong>（编写话术并勾选关联变量）→
            <strong>数据流映射</strong>（检查 接口产出 ↔ 话术变量 是否闭环）。
          </div>
        </div>
      </div>

      <div>
        <el-row :gutter="16">
          <!-- 左侧：分区填写表单 -->
          <el-col :span="16">
            <!-- § 1 基本信息 -->
            <div class="page-card" style="margin-bottom:0;">
              <div class="section-title">① 基本信息</div>
              <el-form label-position="left" label-width="88px" :model="wizardData">
                <el-form-item label="目标省份" required>
                  <!-- 覆盖全国 31 省，只能选择不能手动输入；选中后自动回填中文名 -->
                  <el-select
                    v-model="wizardData.province"
                    filterable
                    placeholder="请选择目标省份"
                    style="width:240px"
                    :disabled="!authStore.isHQ"
                    @change="onWizardProvinceChange"
                  >
                    <el-option
                      v-for="p in PROVINCES"
                      :key="p.code"
                      :label="`${p.name}（${p.code}）`"
                      :value="p.code"
                    />
                  </el-select>
                  <el-tag v-if="!authStore.isHQ" size="small" type="info" style="margin-left:8px;">已锁定</el-tag>
                </el-form-item>
                <el-form-item label="场景分类" required
                  :error="intentDup.exists ? `该省份下已存在场景分类「${wizardData.intent.trim()}」，请改用其他名称` : ''">
                  <el-input
                    v-model="wizardData.intent"
                    placeholder="如：套餐推荐"
                    style="width:220px;"
                    @blur="checkIntentDuplicate"
                    @input="onIntentInput"
                  />
                  <span v-if="intentChecking" style="margin-left:8px;font-size:12px;color:var(--muted);">
                    <el-icon class="is-loading"><Loading /></el-icon> 校验中…
                  </span>
                  <el-tag
                    v-else-if="intentDupChecked && !intentDup.exists && wizardData.intent.trim()"
                    size="small" type="success" style="margin-left:8px;"
                  >✓ 名称可用</el-tag>
                </el-form-item>
                <el-form-item label="描述">
                  <el-input v-model="wizardData.description" placeholder="配置功能描述" style="width:320px;" />
                </el-form-item>
                <el-form-item label="版本号">
                  <el-input v-model="wizardData.version" style="width:100px;" />
                </el-form-item>
                <el-form-item label="负责人">
                  <el-input v-model="wizardData.author" style="width:160px;" />
                </el-form-item>
              </el-form>

              <!-- 快速创建：只需省份 + 意图，接口 / 话术模板稍后在编辑页补配 -->
              <div class="quick-create-bar">
                <el-button
                  type="success" plain
                  :loading="quickCreating"
                  :disabled="!wizardData.province || !wizardData.intent || intentChecking || intentDup.exists"
                  @click="quickCreate"
                >⚡ 快速创建（暂不配置接口 / 话术，稍后再补）</el-button>
                <span class="quick-create-hint">
                  仅用省份 + 意图先建骨架并直接发布，跳过预览与校验；创建后自动进入编辑页补充接口与话术模板。
                </span>
              </div>
            </div>

            <!-- § 2/3 接口配置 + 话术模板（与编辑界面完全一致，含数据流映射） -->
            <div class="page-card" style="margin-top:0;">
              <div class="section-title">
                ② 接口 / 话术模板配置
                <span style="margin-left:10px;font-size:12px;font-weight:400;color:var(--muted);">
                  下方编辑器与「智能话术配置管理 → 编辑」完全一致，含数据流映射检查
                </span>
              </div>

              <!-- 优化4：先在 ① 填省份+意图，再进入与编辑完全一致的三 Tab 编辑器 -->
              <div v-if="!wizardReady" class="wizard-gate">
                <div class="wizard-gate-icon">🔒</div>
                <div class="wizard-gate-title">请先在上方 ① 基本信息中选择<strong>目标省份</strong>并填写<strong>场景分类</strong></div>
                <div class="wizard-gate-sub">填写完成后，将进入与「智能话术配置管理 → 编辑」完全一致的三 Tab 配置界面（接口配置 / 话术模板 / 数据流映射）。</div>
              </div>
              <template v-else>
                <SkillConfigEditor v-model="wizardConfig" />
                <div style="margin-top:16px;text-align:center;">
                  <el-button
                    type="primary" size="large"
                    :loading="generating"
                    :disabled="intentChecking || intentDup.exists"
                    @click="goPreviewFromWizard"
                  >下一步：生成预览 →</el-button>
                  <div v-if="intentDup.exists" style="margin-top:8px;font-size:12px;color:var(--danger);">
                    该省份下已存在场景分类「{{ wizardData.intent.trim() }}」，请返回上方修改名称后再继续。
                  </div>
                </div>
              </template>
            </div>

          </el-col>

          <!-- 右侧：填写说明 -->
          <el-col :span="8">
            <div class="page-card guide-card" style="position:sticky;top:20px;">
              <div class="section-title">📖 操作指南</div>
              <div class="guide-steps">
                <div class="guide-step"><div class="guide-num">1</div>
                  <div class="guide-text"><strong>基本信息</strong><br>填写省份代码（如 guangdong）、意图名称（如 套餐推荐）</div>
                </div>
                <div class="guide-step"><div class="guide-num">2</div>
                  <div class="guide-text"><strong>接口配置</strong>（Tab ①）<br>
                    填写接口 URL、请求模板。接口地址未确定时可先开启 <strong>Mock 模式</strong>，录入模拟响应 JSON 即可联调
                  </div>
                </div>
                <div class="guide-step"><div class="guide-num">3</div>
                  <div class="guide-text"><strong>标准数据关联</strong>（接口编辑 → 出参映射）<br>
                    把接口响应字段映射到 7 大标准数据域（当前套餐 / 历史用量 / 用户标签 等），推荐用「智能自动映射」一键生成规则
                  </div>
                </div>
                <div class="guide-step"><div class="guide-num">4</div>
                  <div class="guide-text"><strong>话术模板</strong>（Tab ②）<br>
                    至少添加一条话术；内容用 <code>{pkg_brief}</code>、<code>{usage}</code> 等占位符，并在「关联变量」中勾选对应变量（系统会自动推荐）
                  </div>
                </div>
                <div class="guide-step"><div class="guide-num">5</div>
                  <div class="guide-text"><strong>数据流映射检查</strong>（Tab ③）<br>
                    确认每条话术需要的变量都有接口供应，出现"变量未闭环"提示时返回上两步补配
                  </div>
                </div>
                <div class="guide-step"><div class="guide-num">6</div>
                  <div class="guide-text"><strong>生成预览 → 校验 → 发布</strong><br>
                    校验通过后发布到 skills-runtime，可选择自动热重载使其立即生效
                  </div>
                </div>
              </div>
              <el-alert
                type="info" :closable="false"
                style="margin-top:16px;font-size:12px;"
                title="提示：标准数据域的字段含义可查看「智能话术配置管理 → 标准数据域说明」用户手册"
              />
            </div>
          </el-col>
        </el-row>
      </div>

    </div>

    <!-- ══════════ 第二步：预览 & 校验 ══════════ -->
    <div v-show="step === 'preview'">
      <div class="page-card" style="margin-bottom:14px;">
        <div style="font-weight:600;font-size:15px;margin-bottom:10px;">
          📁 生成文件预览 / 编辑
          <span v-if="previewData" style="font-size:12px;color:var(--muted);font-weight:400;margin-left:8px;">
            {{ previewData.province }}/{{ previewData.intent }}
          </span>
        </div>
        <el-alert type="info" :closable="false" style="margin-bottom:14px;font-size:12px;">
          可在下方直接修改接口地址、请求字段、话术模板等。修改结果将在发布时生效。
        </el-alert>
        <SkillConfigEditor v-model="previewConfig" />
      </div>

      <div style="text-align:center;margin-top:16px;display:flex;justify-content:center;gap:12px;">
        <el-button @click="step = 'config'">← 返回修改</el-button>
        <el-button type="primary" size="large" :disabled="!canPublish" @click="goPublishSection">
          下一步：去发布 →
        </el-button>
      </div>
    </div>

    <!-- ══════════ 第三步：发布 ══════════ -->
    <div v-show="step === 'publish'">
      <div class="page-card" style="max-width:600px;margin:0 auto;">
        <div style="font-weight:600;font-size:15px;margin-bottom:20px;">🚀 发布配置</div>

        <!-- 权限提示 -->
        <el-alert
          v-if="publishTargetProvince && !authStore.canWrite(publishTargetProvince)"
          type="error"
          title="无发布权限"
          :description="`当前账号（${authStore.province}省）无权发布到目标省份 ${publishTargetProvince}`"
          :closable="false"
          show-icon
          style="margin-bottom:16px;"
        />

        <!-- 重名校验已前移至第一步「场景分类」填写后即时校验；此处仅保留热重载提示 -->
        <el-alert
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom:16px;"
          title="发布后将自动热重载生效"
          description="配置写入后自动触发话术智能体热重载，无需额外操作。"
        />

        <div v-if="publishProgress.length" style="margin:16px 0;">
          <div v-for="s in publishProgress" :key="s.key"
            style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border);">
            <span v-if="s.state==='done'" style="color:var(--success);">✅</span>
            <span v-else-if="s.state==='error'" style="color:var(--danger);">❌</span>
            <span v-else-if="s.state==='doing'" style="color:var(--primary);">
              <el-icon class="is-loading"><Loading /></el-icon>
            </span>
            <span v-else style="color:var(--muted);">○</span>
            <span style="flex:1;font-size:13px;">{{ s.label }}</span>
            <span v-if="s.detail" style="font-size:12px;color:var(--muted);">{{ s.detail }}</span>
          </div>
        </div>

        <div v-if="publishResult" style="margin-top:16px;">
          <el-alert type="success" title="发布成功！" :closable="false" show-icon style="margin-bottom:12px;" />
          <div style="font-size:13px;color:var(--muted);">
            路径：skills-runtime/{{ publishResult.province }}/{{ publishResult.intent }}
          </div>
          <div style="font-size:13px;color:var(--muted);margin-top:4px;">
            写入文件：{{ publishResult.files_written?.join(', ') }}
          </div>
          <div style="margin-top:16px;display:flex;gap:10px;">
            <el-button type="success" @click="$router.push('/SkillManager')">前往智能话术配置管理</el-button>
            <el-button @click="resetAll">再创建一个</el-button>
          </div>
        </div>

        <div v-else style="text-align:center;margin-top:16px;display:flex;justify-content:center;gap:12px;">
          <el-button @click="step = 'preview'">← 返回预览</el-button>
          <el-button
            type="primary"
            size="large"
            :loading="publishing"
            :disabled="publishTargetProvince ? !authStore.canWrite(publishTargetProvince) : false"
            @click="doPublish"
          >🚀 确认发布</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'
import {
  acGeneratePreview as generatePreview,
  acPublishSkill    as publishSkill,
  acListSkills      as listSkills,
  acSkillExists     as skillExists,
} from '@/api/autoConfig.js'
import { $msg } from '@/utils/msg'
import { PROVINCES, provinceNameOf } from '@/utils/provinces'
import EnvBanner from '@/components/EnvBanner.vue'
import SkillConfigEditor from '../components/SkillConfigEditor.vue'

// 统一消息反馈（与 SkillManager 一致）：桥接 ElMessage → $msg
const ElMessage = {
  success: (m) => $msg.ok(m),
  warning: (m) => $msg.warn(m),
  error:   (m) => $msg.err(m),
  info:    (m) => $msg.info(m),
}

// 选中省份后自动回填中文名（省份只能选择，不能手动输入）
function onWizardProvinceChange(code) {
  wizardData.province_name = provinceNameOf(code)
  // 省份变化后重新校验重名（同一场景分类在不同省份下可各自独立存在）
  checkIntentDuplicate()
}

// ── 权限 ──────────────────────────────────────────────
const authStore = useAuthStore()
const router = useRouter()

// ── 快速创建：只需省份+意图，先建骨架直接发布，接口/话术稍后补配 ──
const quickCreating = ref(false)
async function quickCreate() {
  if (!wizardData.province || !wizardData.intent) {
    ElMessage.warning('请先选择目标省份并填写场景分类'); return
  }
  if (!authStore.canWrite(wizardData.province)) {
    ElMessage.error(`当前账号无权在省份 ${wizardData.province} 下创建配置`); return
  }
  // 重名前置拦截：确保用最新名称校验一次（防抖期间直接点按钮）
  await checkIntentDuplicate()
  if (intentDup.exists) {
    ElMessage.error(`该省份下已存在场景分类「${wizardData.intent.trim()}」，请改用其他名称`); return
  }
  const tpl = {
    meta: {
      province:      wizardData.province,
      province_name: wizardData.province_name || provinceNameOf(wizardData.province) || wizardData.province,
      intent:        wizardData.intent.trim(),
      description:   wizardData.description || `${wizardData.province_name || wizardData.province}${wizardData.intent}`,
      version:       wizardData.version || '1.0.0',
      author:        wizardData.author || authStore.username,
    },
    // 接口与话术模板留空，稍后在编辑页补配
    api: {},
    templates: [],
    strategy: { default_strategy: 'direct', top_n: 3, max_script_length: 150, max_parallel_scripts: 3 },
  }
  quickCreating.value = true
  try {
    const data = await publishSkill({
      template: tpl,
      overwrite: false,
      reload: true,
      validate_before_publish: false,   // 快速创建：跳过跑通校验
      created_by: authStore.username,
    })
    ElMessage.success('✅ 已快速创建，正在进入编辑页补充接口与话术模板…')
    const p = data?.province || tpl.meta.province
    const i = data?.intent || tpl.meta.intent
    // 进入智能话术配置管理并定位到该技能进行编辑
    router.push({
      path: '/SkillManager',
      query: { edit_province: p, edit_intent: i },
    })
  } catch (err) {
    const detail = err?.response?.data?.detail
    if (detail && typeof detail === 'object') {
      const errs = Array.isArray(detail.errors) ? detail.errors : []
      ElMessage.error(errs.length ? `创建失败：${errs.join('；')}` : (detail.message || '快速创建失败'))
    } else {
      ElMessage.error((typeof detail === 'string' && detail) || err?.message || '快速创建失败')
    }
  } finally {
    quickCreating.value = false
  }
}

// ── 步骤 ──────────────────────────────────────────────
const step = ref('config')
const steps = [
  { key: 'config',  label: '配置来源' },
  { key: 'preview', label: '预览校验' },
  { key: 'publish', label: '发布上线' },
]
// ── 逐步配置向导状态 ──────────────────────────────────
// SkillConfigEditor 双向绑定（包含 api_nodes / biz_config，含话术、策略）
const wizardConfig = ref({
  api_nodes: {
    main_api: {
      enabled: true,
      // 新建 skill 默认「透传模式（extra_info）」：参数直接来自入参，无需接口地址
      source_type: 'direct',
      direct_mode: 'passthrough',
      passthrough_fields: [],
      url: '',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      timeout: 30,
      max_retries: 2,
      mock_mode: false,
      request_template: { phone: '{{PHONE}}', province: '{{PROVINCE}}' },
      response_extract: {},
      field_transform: {},
      mock_response: {},
    },
  },
  biz_config: {
    strategy: { default_strategy: 'direct', top_n: 3, max_script_length: 150, max_parallel_scripts: 3 },
    field_aliases: {},
    // 不预置默认话术模板：避免生成一条空的「推荐话术/推荐环节」兜底行落库成脏数据。
    // 话术模板由用户在编辑页/模板管理里按需新增。
    script_templates_v2: [],
  },
})

const wizardData = reactive({
  province: '',
  province_name: '',
  intent: '',
  description: '',
  version: '1.0.0',
  author: '',
})

const allSkills = ref([])

// ── 场景分类重名前置校验（填写后即校验，权威来源为 ES/registry 生效配置）──────
const intentChecking   = ref(false)   // 正在向后端校验
const intentDupChecked  = ref(false)  // 是否已完成一次校验（用于展示「✓ 名称可用」）
const intentDup = reactive({ exists: false, source: '', key: '' })  // 最近一次校验结果
let _intentCheckTimer = null

/** 输入变化：清空上次校验结论并防抖触发（避免边打字边报重名） */
function onIntentInput() {
  intentDup.exists = false
  intentDupChecked.value = false
  if (_intentCheckTimer) clearTimeout(_intentCheckTimer)
  _intentCheckTimer = setTimeout(checkIntentDuplicate, 500)
}

/** 校验「省份 + 场景分类」是否已存在（后端以 registry/ES 为准，本地目录兜底）。 */
async function checkIntentDuplicate() {
  if (_intentCheckTimer) { clearTimeout(_intentCheckTimer); _intentCheckTimer = null }
  const province = wizardData.province
  const intent = (wizardData.intent || '').trim()
  if (!province || !intent) {
    intentDup.exists = false; intentDupChecked.value = false; return
  }
  const key = `${province}::${intent}`
  intentChecking.value = true
  try {
    const data = await skillExists(province, intent)
    // 校验期间用户可能又改了名，结果过期则丢弃
    if (`${wizardData.province}::${(wizardData.intent || '').trim()}` !== key) return
    intentDup.exists = !!data?.exists
    intentDup.source = data?.source || ''
    intentDup.key = key
    intentDupChecked.value = true
    if (intentDup.exists) {
      ElMessage.warning(`该省份下已存在场景分类「${intent}」，请改用其他名称`)
    }
  } catch (_) {
    // 校验接口异常时不阻断（发布端 overwrite=false 仍会最终兜底拦截）
    intentDup.exists = false; intentDupChecked.value = false
  } finally {
    intentChecking.value = false
  }
}

async function goPreviewFromWizard() {
  if (!wizardData.province || !wizardData.intent) {
    ElMessage.warning('请填写省份和意图')
    return
  }
  // 重名前置拦截：进入预览前用最新名称再校验一次
  await checkIntentDuplicate()
  if (intentDup.exists) {
    ElMessage.error(`该省份下已存在场景分类「${wizardData.intent.trim()}」，请改用其他名称后再继续`)
    return
  }
  // 从 SkillConfigEditor 双向绑定数据中提取首个接口节点 + 模板列表
  // 接口与话术模板均可留空（快速创建/稍后补配），不再强制拦截。
  const apiNodes = wizardConfig.value?.api_nodes || {}
  const firstKey = Object.keys(apiNodes)[0]
  const n = firstKey ? apiNodes[firstKey] : null
  const biz = wizardConfig.value?.biz_config || {}
  const tplList = biz.script_templates_v2 || []
  const tpl = {
    meta: {
      province:      wizardData.province,
      province_name: wizardData.province_name || wizardData.province,
      intent:        wizardData.intent,
      description:   wizardData.description,
      version:       wizardData.version,
      author:        wizardData.author || authStore.username,
    },
    api: n ? {
      name:              firstKey,
      url:               n.url,
      method:            n.method || 'POST',
      headers:           n.headers || { 'Content-Type': 'application/json' },
      timeout:           n.timeout || 30,
      max_retries:       n.max_retries ?? 2,
      mock_mode:         !!n.mock_mode,
      request_template:  n.request_template || {},
      response_extract:  n.response_extract || {},
      field_transform:   n.field_transform || {},
      mock_response:     n.mock_response || {},
      // 直传/透传模式：透传给后端保留，避免被当作普通接口查询（url 必填）而校验失败
      source_type:       n.source_type || 'api',
      direct_mode:       n.direct_mode || undefined,
      passthrough_fields: n.passthrough_fields || undefined,
    } : {},
    strategy: biz.strategy || { top_n: 3, sort_by: 'score', filter_enabled: true },
    templates: tplList.map(t => ({ ...t })),
  }
  parsedTemplate.value = tpl
  parseErrors.value    = []
  await goPreview()
}

// ── 通用状态 ──────────────────────────────────────────
const generating = ref(false)
const parsedTemplate = ref(null)
const parseErrors    = ref([])

// ── 预览状态 ──────────────────────────────────────────
const previewData      = ref(null)
const previewConfig    = ref({ api_nodes: {}, biz_config: {} })

// ── 发布状态 ──────────────────────────────────────────
const publishing       = ref(false)
const publishResult    = ref(null)
const publishProgress  = ref([])

/** 当前发布目标省份（用于权限校验） */
const publishTargetProvince = computed(() => parsedTemplate.value?.meta?.province || '')

// ── 计算属性 ──────────────────────────────────────────
const doneStages = computed(() => ({
  config:  !!parsedTemplate.value && parseErrors.value.length === 0,
  preview: !!previewData.value,
  publish: !!publishResult.value,
}))

// 进入预览后即可发布（校验环节已移除；重名在发布步骤单独拦截）
const canPublish = computed(() => !!previewData.value)
// 优化4：省份+意图齐全后才进入三 Tab 编辑器
const wizardReady = computed(() => !!wizardData.province && !!wizardData.intent)

// ── 生成预览 ──────────────────────────────────────────
async function goPreview() {
  if (!parsedTemplate.value) { ElMessage.warning('请先上传配置文件或选择模板'); return }
  generating.value = true
  try {
    const data = await generatePreview(parsedTemplate.value)
    previewData.value = data
    const parseContent = (raw) => {
      if (typeof raw === 'object') return raw
      try { return JSON.parse(raw) } catch { return {} }
    }
    previewConfig.value = {
      api_nodes:  parseContent(data.files?.api_nodes?.content  ?? data.files?.api_nodes  ?? {}),
      biz_config: parseContent(data.files?.biz_config?.content ?? data.files?.biz_config ?? {}),
    }
    step.value = 'preview'
  } catch (e) {
    // 422：后端 detail 为 { message, errors }，逐条提示，避免“点击无响应”
    const detail = e?.response?.data?.detail
    if (detail && typeof detail === 'object') {
      const errs = Array.isArray(detail.errors) ? detail.errors : []
      ElMessage.error(
        errs.length
          ? `${detail.message || '模板验证失败'}：${errs.join('；')}`
          : (detail.message || '生成预览失败'),
      )
    } else {
      ElMessage.error(
        (typeof detail === 'string' && detail) || e?.message || '生成预览失败',
      )
    }
  } finally {
    generating.value = false
  }
}

function buildMergedTemplate() {
  let tpl = { ...(parsedTemplate.value || {}) }
  const cfg = previewConfig.value
  if (cfg?.api_nodes) {
    const firstKey = Object.keys(cfg.api_nodes)[0]
    if (firstKey) {
      const n = cfg.api_nodes[firstKey]
      tpl = {
        ...tpl,
        api: {
          ...(tpl.api || {}),
          name: firstKey, url: n.url, method: n.method,
          headers: n.headers, timeout: n.timeout, max_retries: n.max_retries,
          mock_mode: n.mock_mode, request_body_wrapper: n.request_body_wrapper,
          request_template: n.request_template, response_extract: n.response_extract,
          field_transform: n.field_transform, mock_response: n.mock_response,
        },
      }
    }
  }
  if (cfg?.biz_config?.script_templates_v2) {
    tpl = {
      ...tpl,
      templates: cfg.biz_config.script_templates_v2.map(t => ({
        template_id: t.template_id, template_name: t.template_name,
        stage: t.stage, scene: t.scene, product_id: t.product_id,
        template_content: t.template_content, linked_vars: t.linked_vars,
        prompt_template: t.prompt_template, script_requirement: t.script_requirement,
        status: t.status,
      })),
      strategy: cfg.biz_config.strategy,
    }
  }
  return tpl
}

// ── 进入发布 ──────────────────────────────────────────
// 校验为可选：未校验或校验未通过也允许发布（接口/话术可稍后补配）
function goPublishSection() {
  step.value = 'publish'
}

// ── 执行发布 ──────────────────────────────────────────
async function doPublish() {
  // 权限拦截
  if (!authStore.canWrite(publishTargetProvince.value)) {
    ElMessage.error(`当前账号无权发布到省份 ${publishTargetProvince.value}`)
    return
  }
  // 重名已在第一步「场景分类」填写后即时校验拦截；此处不再重复校验。
  // 发布端 overwrite=false 仍会对已存在配置返回 409，作为最终兜底（错误会在 catch 中透出）。
  parsedTemplate.value = buildMergedTemplate()
  publishProgress.value = [
    { key: 'validate', label: '校验配置模板',    state: 'done' },
    { key: 'generate', label: '生成配置代码', state: 'doing' },
    { key: 'write',    label: '写入 skills-runtime', state: 'pending' },
    { key: 'reload',   label: '触发热重载',      state: 'pending' },
  ]
  const setStep = (key, state, detail) => {
    const s = publishProgress.value.find(x => x.key === key)
    if (s) { s.state = state; if (detail !== undefined) s.detail = detail }
  }
  publishing.value = true
  try {
    const data = await publishSkill({
      template: parsedTemplate.value,
      overwrite: false,                 // 不覆盖：重名已在前面拦截
      reload: true,                     // 发布后默认自动热重载
      validate_before_publish: false,   // 校验为可选，不阻断发布（接口/话术可稍后补配）
      validate_run_api_call: false,
      created_by: authStore.username,   // 注入当前用户
    })
    setStep('generate', 'done')
    setStep('write', 'done', `${data.files_written?.length || 0} 个文件`)
    setStep('reload',
      data.reload?.success ? 'done' : 'error',
      data.reload?.success ? '成功' : '失败/不可达',
    )
    publishResult.value = data
    ElMessage.success('发布成功！')
    loadAllSkills()
  } catch (err) {
    const detail = err.response?.data?.detail
    // 把后端失败原因透出到步骤详情 + toast，避免只显示「X生成配置代码」无从排查
    let reason = ''
    if (detail && typeof detail === 'object') reason = detail.message || ''
    else if (typeof detail === 'string') reason = detail
    reason = reason || err?.message || '发布失败'
    setStep('generate', 'error', reason)
    ElMessage.error(`发布失败：${reason}`)
  } finally { publishing.value = false }
}

// ── 重置 ─────────────────────────────────────────────
function resetAll() {
  step.value = 'config'
  Object.assign(wizardData, {
    province: authStore.isHQ ? '' : authStore.province,
    province_name: authStore.isHQ ? '' : provinceNameOf(authStore.province),
    intent: '', description: '',
    version: '1.0.0', author: authStore.username,
  })
  parsedTemplate.value  = null; parseErrors.value     = []
  previewData.value     = null
  previewConfig.value   = { api_nodes: {}, biz_config: {} }
  publishResult.value   = null; publishProgress.value  = []
  intentDup.exists = false; intentDup.source = ''; intentDup.key = ''
  intentDupChecked.value = false; intentChecking.value = false
}

// ── 初始化 ────────────────────────────────────────────
async function loadAllSkills() {
  try {
    const data = await listSkills()
    allSkills.value = data.skills || []
  } catch (_) {}
}

onMounted(async () => {
  await authStore.fetchMe()
  // 非本部用户：省份锁定为自身省份，并回填中文名
  if (!authStore.isHQ && authStore.province) {
    wizardData.province = authStore.province
    wizardData.province_name = provinceNameOf(authStore.province)
  }
  wizardData.author = authStore.username
  loadAllSkills()
})
</script>

<style scoped>
.section-title {
  font-weight: 600; font-size: 14px; color: var(--text);
  margin-bottom: 14px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
}
.guide-card { background: #fafbff; }
.guide-steps { display: flex; flex-direction: column; gap: 14px; }
.guide-step { display: flex; gap: 12px; align-items: flex-start; }
.guide-num {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%;
  background: var(--primary); color: #fff; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}
.guide-text { font-size: 13px; color: var(--muted); line-height: 1.7; }
.guide-text strong { color: var(--text); }
.quick-create-bar {
  margin-top: 6px; padding-top: 14px; border-top: 1px dashed var(--border);
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.quick-create-hint { font-size: 12px; color: var(--muted); line-height: 1.5; flex: 1; min-width: 220px; }
.wizard-banner {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 14px 18px; margin-bottom: 16px;
  background: linear-gradient(90deg, #f0f4ff 0%, #fff 100%);
  border: 1px solid #d0d9ff; border-radius: 10px;
}
.wizard-banner-icon { font-size: 24px; line-height: 1.2; flex-shrink: 0; }
.wizard-banner-title { font-size: 14px; font-weight: 600; color: var(--text); }
.wizard-banner-sub { font-size: 12px; color: var(--muted); margin-top: 4px; line-height: 1.65; }
.wizard-gate {
  text-align: center; padding: 40px 20px; border: 1px dashed var(--border);
  border-radius: 10px; background: #fafbff;
}
.wizard-gate-icon { font-size: 32px; margin-bottom: 10px; }
.wizard-gate-title { font-size: 14px; color: var(--text); }
.wizard-gate-title strong { color: var(--primary); }
.wizard-gate-sub { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.65; max-width: 460px; margin-left: auto; margin-right: auto; }
</style>
