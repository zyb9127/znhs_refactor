<template>
  <div class="layout">
    <!-- 左侧：输入区 -->
    <div>
      <div class="card" style="margin-bottom:16px">
        <div class="card-title">📡 已加载技能包</div>
        <div class="skill-tags">
          <span v-if="!skillsList.length" style="font-size:13px;color:var(--muted)">加载中...</span>
          <span
            v-for="s in skillsList" :key="s.province+s.intent"
            class="skill-tag"
            :class="{ selected: selectedSkillKey === s.province+'_'+s.intent }"
            @click="onSkillTagClick(s)"
          >{{ s.meta?.province_name || s.province }} · {{ s.intent }}</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">🧪 测试参数</div>

        <label>省份</label>
        <input type="text" v-model="form.province" placeholder="beijing">

        <label>手机号</label>
        <input type="text" v-model="form.phone" placeholder="15010470528">

        <label>意图</label>
        <select v-model="form.intent" @change="onIntentChange">
          <option v-for="i in intentOptions" :key="i" :value="i">{{ i }}</option>
        </select>

        <label>推荐数量</label>
        <input type="number" v-model.number="form.topN" min="1">

        <label>环节</label>
        <select v-model="form.stage">
          <option value="">（不限）</option>
          <option v-for="s in stageOptions" :key="s" :value="s">{{ s }}</option>
        </select>

        <label>场景</label>
        <select v-model="form.scene">
          <option value="">（不限）</option>
          <option v-for="s in sceneOptions" :key="s" :value="s">{{ s }}</option>
        </select>

        <label>附加参数</label>
        <textarea v-model="form.extraData" style="resize:vertical;min-height:80px"></textarea>

        <hr class="divider">

        <button class="btn btn-primary btn-block" :disabled="loading" @click="runTest">
          {{ loading ? '⏳ 推荐中...' : '▶ 执行推荐' }}
        </button>
        <button class="btn btn-secondary btn-block" style="margin-top:8px" @click="clearResult">
          清空结果
        </button>
      </div>
    </div>

    <!-- 右侧：结果区 -->
    <div>
      <div class="status-bar" :class="statusClass">
        <span>{{ statusIcon }}</span> {{ statusText }}
      </div>
      <div class="result-pane">
        <div v-for="(card, idx) in resultCards" :key="idx" class="step-card">
          <div class="step-header" @click="card.open = !card.open">
            <div class="step-title">
              <span class="step-badge" :class="card.badgeClass">{{ card.open ? '展开' : '折叠' }}</span>
              {{ card.title }}
            </div>
            <span style="color:var(--muted)">{{ card.open ? '▲' : '▼' }}</span>
          </div>
          <div class="step-body" :class="{ open: card.open }" v-html="card.content"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { apiFetch, marketingRecommendFetch, readJsonOrThrow } from '@/utils/apiUrl'

const skillsList = ref([])
const selectedSkillKey = ref('beijing_套餐推荐')
const intentOptions = ref([])
const stageOptions = ref([])
const sceneOptions = ref([])
const loading = ref(false)
const statusClass = ref('status-idle')
const statusIcon = ref('⚡')
const statusText = ref('准备就绪，点击「执行推荐」开始测试')
const resultCards = ref([])

const form = reactive({
  province: 'beijing',
  phone: '15010470528',
  intent: '',
  topN: 2,
  stage: '',
  scene: '',
  extraData: `{
  "ioId": "demo_io_001",
  "currentMainOffer": {
    "curOfferName": "128元5G畅享套餐",
    "curOfferId": "111601000461",
    "curOfferFee": "12800"
  }
}`
})

async function loadSkills() {
  try {
    const res = await apiFetch('/api/skills')
    const json = await res.json()
    skillsList.value = json.data || []
    buildIntentOptions('beijing', '套餐推荐')
  } catch {
    skillsList.value = []
  }
}

function buildIntentOptions(province, selectedIntent) {
  const intents = skillsList.value.filter(s => s.province === province).map(s => s.intent)
  if (!intents.length) return
  intentOptions.value = intents
  form.intent = intents.includes(selectedIntent) ? selectedIntent : intents[0]
  loadStageSceneOptions(province, form.intent)
}

function onSkillTagClick(s) {
  form.province = s.province
  selectedSkillKey.value = s.province + '_' + s.intent
  buildIntentOptions(s.province, s.intent)
}

function onIntentChange() {
  const province = form.province.trim()
  selectedSkillKey.value = province + '_' + form.intent
  loadStageSceneOptions(province, form.intent)
}

async function loadStageSceneOptions(province, intent) {
  if (!province || !intent) return
  try {
    const res = await apiFetch(`/api/skills/${encodeURIComponent(province)}/${encodeURIComponent(intent)}/biz_config`)
    const json = await res.json()
    const templates = (json.data?.script_templates_v2 || []).filter(t => !t.status || t.status === 'online')
    stageOptions.value = [...new Set(templates.map(t => t.stage).filter(Boolean))]
    sceneOptions.value = [...new Set(templates.map(t => t.scene).filter(Boolean))]
    if (stageOptions.value.includes('切入环节')) form.stage = '切入环节'
  } catch { /* 静默 */ }
}

async function runTest() {
  let extra_data = {}
  try { extra_data = JSON.parse(form.extraData || '{}') }
  catch { alert('extra_data JSON 格式错误！'); return }

  const body = {
    phone: form.phone.trim(),
    intent: form.intent,
    province: form.province.trim(),
    topN: form.topN || 3,
    callId: 'test-' + Date.now(),
    extra_data,
    extra_context: { stage: form.stage || '', scence: form.scene || '' }
  }

  loading.value = true
  statusClass.value = 'status-loading'
  statusIcon.value = '🔄'
  statusText.value = '执行中，请稍候...'
  resultCards.value = []
  const t0 = Date.now()

  try {
    const res = await marketingRecommendFetch({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    const json = await readJsonOrThrow(res)
    const elapsed = Date.now() - t0
    if (json.code === 200) {
      statusClass.value = 'status-ok'
      statusIcon.value = '✅'
      statusText.value = `推荐成功，耗时 ${elapsed}ms，生成 ${json.data?.recommend_results?.length || 0} 条话术`
      renderResult(json, body)
    } else {
      statusClass.value = 'status-err'
      statusIcon.value = '❌'
      statusText.value = `失败：${json.message || '未知错误'}`
      resultCards.value = [{ title: '错误详情', badgeClass: 'badge-blue', open: true, content: `<pre style="background:#fef2f2;color:var(--danger)">${JSON.stringify(json, null, 2)}</pre>` }]
    }
  } catch (e) {
    statusClass.value = 'status-err'
    statusIcon.value = '❌'
    statusText.value = `请求异常：${e.message}`
  } finally {
    loading.value = false
  }
}

function clearResult() {
  resultCards.value = []
  statusClass.value = 'status-idle'
  statusIcon.value = '⚡'
  statusText.value = '已清空，点击「执行推荐」重新测试'
}

function renderResult(json, reqBody) {
  const d = json.data || {}
  const rc = json.resource_context || {}
  const meta = json.metadata || {}
  const finalRecs = (d.final_recommendations?.length ? d.final_recommendations : rc.recommended_packages) || []
  const apiCalls = (json.api_calls || []).filter(
    c => !c.source_type || c.source_type === 'api'
  )
  const llmPrompts = json.llm_prompts || []

  const cards = [
    { title: '📢 Step3 · 话术生成结果', badgeClass: 'badge-green', open: true, content: renderScripts(d.recommend_results || [], finalRecs) },
  ]
  if (llmPrompts.length) {
    cards.push({
      title: `🧠 发给大模型的最终提示词 · ${llmPrompts.length} 条`,
      badgeClass: 'badge-purple',
      open: true,
      content: renderLlmPrompts(llmPrompts),
    })
  }
  if (apiCalls.length) {
    cards.push({
      title: `🔌 查询接口调用（入参 / 出参）· ${apiCalls.length} 个`,
      badgeClass: apiCalls.some(c => c.error) ? 'badge-red' : 'badge-blue',
      open: true,
      content: renderApiCalls(apiCalls),
    })
  }
  cards.push(
    { title: '📦 Step1 · 数据采集 (resource_context)', badgeClass: 'badge-blue', open: false, content: renderResourceContext(rc) },
    { title: '🎯 Step2 · 推荐筛选', badgeClass: 'badge-purple', open: false, content: renderPackages(finalRecs) },
    { title: '📊 执行元信息', badgeClass: 'badge-blue', open: false, content: renderMeta(meta, json) },
    { title: '🗂 原始响应 (JSON)', badgeClass: 'badge-blue', open: false, content: `<pre>${escHtml(JSON.stringify(json, null, 2))}</pre>` },
  )
  resultCards.value = cards
}

function renderLlmPrompts(prompts) {
  if (!prompts?.length) {
    return '<p style="color:var(--muted);font-size:13px">本次未生成 LLM 提示词</p>'
  }
  const section = (title, body, open = false) => {
    const text = (body || '').trim()
    if (!text) {
      return `<details style="margin-top:8px;"><summary style="cursor:pointer;font-size:12px;font-weight:600;color:var(--muted);">${escHtml(title)} · 空</summary>
        <pre style="max-height:160px;margin-top:6px;">（无）</pre></details>`
    }
    return `<details ${open ? 'open' : ''} style="margin-top:8px;">
      <summary style="cursor:pointer;font-size:12px;font-weight:600;color:var(--muted);">${escHtml(title)} · ${text.length} 字</summary>
      <pre style="max-height:280px;margin-top:6px;white-space:pre-wrap;">${escHtml(text)}</pre>
    </details>`
  }
  return prompts.map((p, idx) => {
    const meta = [
      p.package_name ? escHtml(p.package_name) : '',
      p.product_id ? `ID ${escHtml(String(p.product_id))}` : '',
      p.stage ? `环节 ${escHtml(p.stage)}` : '',
      p.scence ? `意图 ${escHtml(p.scence)}` : '',
    ].filter(Boolean).join(' · ')
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:12px;">
      <div style="font-weight:600;font-size:14px;margin-bottom:4px;">
        第 ${p.rank || idx + 1} 条提示词
        ${meta ? `<span style="font-weight:400;color:var(--muted);font-size:12px;margin-left:8px;">${meta}</span>` : ''}
      </div>
      ${renderMissingSlots(p.missing_slots, p.missing_slot_hints)}
      ${section('① 上下文数据', p.context_data, true)}
      ${section('② 话术模板', p.template, true)}
      ${section('③ 话术要求', p.script_requirement, true)}
      ${section('④ 其他提示（角色说明 / 生成规则 / 输出指令）', p.other, false)}
      ${section('完整 Prompt（最终发给大模型）', p.full, false)}
    </div>`
  }).join('')
}

/** 模板引用但本次无事实的槽位（缺数据的直接线索，对应提示词里的【缺失事实】段） */
function renderMissingSlots(slots, hints) {
  if (!slots?.length) return ''
  const hintMap = hints && typeof hints === 'object' ? hints : {}
  // 「父域有数据但叶子子键失配」的槽位单独高亮：映射其实成功了，只是键名对不上，
  // 这类往往是模板占位符或 field_rename 目标名差一字，给出候选键直接可定位。
  const mismatch = slots.filter(s => hintMap[s])
  const mismatchHtml = mismatch.length
    ? `<div style="margin-top:6px;padding-top:6px;border-top:1px dashed #fed7aa;">
        <div style="font-weight:600;">键名失配（映射有数据、子键对不上，很可能是模板占位符或 field_rename 目标名写错）：</div>
        ${mismatch.map(s => `<div style="margin-top:2px;">• <code>${escHtml(s)}</code> — ${escHtml(hintMap[s])}</div>`).join('')}
      </div>`
    : ''
  return `<div style="margin:6px 0 2px;padding:8px 10px;background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;color:#9a3412;font-size:12px;">
    ⚠ 模板引用但本次无数据的槽位：<code>${escHtml(slots.join('、'))}</code>
    <div style="margin-top:2px;">这些槽位不会进入上下文数据，已在提示词【缺失事实】段显式禁止编造与串填；若本应有数据，请看下方接口调用里的「字段映射诊断」。</div>
    ${mismatchHtml}
  </div>`
}

/** 字段映射诊断：每条 field_transform 规则的数据源与命中情况 */
function renderMappingDiag(c) {
  const rows = c.mapping || []
  if (!rows.length) return ''
  const meta = {
    ok: ['已产出', '#2f9e44'],
    empty_source: ['数据源为空', '#c92a2a'],
    no_key_matched: ['键名未命中', '#c92a2a'],
    all_excluded: ['全部被排除', '#e8590c'],
  }
  const body = rows.map(m => {
    const [text, color] = meta[m.status] || [m.status || '—', 'var(--muted)']
    const hint = m.status === 'no_key_matched'
      ? `<div style="margin-top:4px;font-size:11px;color:#b91c1c;">配置键名：${escHtml((m.config_keys || []).join('、')) || '—'}<br>接口实际键名：${escHtml((m.source_keys || []).join('、')) || '—'}</div>`
      : ''
    return `<tr>
      <td><code>${escHtml(m.target || '')}</code></td>
      <td>${escHtml(m.source || m.from || '—')}</td>
      <td style="color:${color};font-weight:600;">${escHtml(text)}</td>
      <td>${escHtml((m.output_keys || []).join('、')) || '—'}${hint}</td>
    </tr>`
  }).join('')
  const bad = rows.some(m => m.status !== 'ok')
  return `<div style="font-size:12px;font-weight:600;color:${bad ? '#b91c1c' : 'var(--muted)'};margin:10px 0 4px;">
      🧭 字段映射诊断（field_transform → 标准域）${bad ? ' · 存在未产出的映射域' : ''}
    </div>
    <table class="diff-table" style="width:100%;font-size:12px;">
      <tr><th>目标标准域</th><th>数据源</th><th>结果</th><th>产出字段</th></tr>
      ${body}
    </table>
    <div style="font-size:11px;color:var(--muted);margin-top:4px;">本节点写入的标准域：${escHtml((c.mapped_domains || []).join('、')) || '（无）'}</div>`
}

function renderApiCalls(calls) {
  if (!calls?.length) {
    return '<p style="color:var(--muted);font-size:13px">本次未调用外部查询接口</p>'
  }
  return calls.map((c, idx) => {
    const ok = !c.error
    const statusColor = ok ? '#2f9e44' : '#c92a2a'
    const statusText = ok ? '成功' : '失败'
    const mockTag = c.mock_mode
      ? '<span style="margin-left:8px;font-size:11px;padding:1px 6px;border-radius:10px;background:#fef3c7;color:#92400e;">MOCK</span>'
      : ''
    const elapsed = c.elapsed_ms != null ? `${c.elapsed_ms}ms` : '—'
    const wrapHint = c.request_body_wrapper
      ? `<div class="meta-row"><span>body 包装键</span><span><code>${escHtml(c.request_body_wrapper)}</code></span></div>`
      : ''
    const errHtml = c.error
      ? `<div style="margin:8px 0;padding:8px 10px;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;color:#b91c1c;font-size:12px;">⚠ ${escHtml(String(c.error))}</div>`
      : ''
    return `<div style="border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:12px;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
        <div style="font-weight:600;font-size:14px;">
          ${idx + 1}. ${escHtml(c.api_name || 'unnamed')}${mockTag}
          <span style="font-weight:400;color:var(--muted);font-size:12px;margin-left:8px;">${escHtml(c.method || 'POST')} ${escHtml(c.url || '(无 URL)')}</span>
        </div>
        <div style="font-size:12px;">
          <span style="color:${statusColor};font-weight:600;">${statusText}</span>
          <span style="color:var(--muted);margin-left:8px;">耗时 ${escHtml(elapsed)}</span>
        </div>
      </div>
      ${wrapHint}${errHtml}
      <div style="font-size:12px;font-weight:600;color:var(--muted);margin:8px 0 4px;">📤 查询入参（实际上送）</div>
      <pre style="max-height:220px;">${escHtml(JSON.stringify(c.request ?? null, null, 2))}</pre>
      <div style="font-size:12px;font-weight:600;color:var(--muted);margin:10px 0 4px;">📥 查询出参（原始响应）</div>
      <pre style="max-height:280px;">${escHtml(JSON.stringify(c.response ?? null, null, 2))}</pre>
      ${renderMappingDiag(c)}
    </div>`
  }).join('')
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
}

function renderScripts(scripts, finalRecs) {
  if (!scripts?.length) return '<p style="color:var(--muted);font-size:13px">暂无话术结果</p>'
  const pkgMap = {}
  finalRecs.forEach(p => { const pid = p.offerId || p.product_id || ''; if (pid) pkgMap[pid] = p.offerName || p.package_name || '' })
  return scripts.map((s, i) => {
    const assocId = s.offerId || s.product_id || ''
    const pkgName = pkgMap[assocId] || s.package_name || ''
    const subtitle = pkgName ? escHtml(pkgName) : (assocId ? escHtml(assocId) : '')
    let tableHtml = ''
    if (s.diff_table?.rows?.length) {
      const hds = s.diff_table.headers || []
      tableHtml = '<div style="margin-top:12px"><div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:5px">📊 差异对比表</div><table class="diff-table" style="width:100%">'
      tableHtml += '<tr>' + hds.map(h => `<th>${escHtml(String(h))}</th>`).join('') + '</tr>'
      s.diff_table.rows.forEach(row => {
        const d = String(row.diff || '')
        const ds = d.startsWith('+') ? 'color:#2f9e44;font-weight:600' : d.startsWith('-') ? 'color:#c92a2a;font-weight:600' : ''
        tableHtml += `<tr><td>${escHtml(String(row.label||''))}</td><td>${escHtml(String(row.current||'—'))}</td><td>${escHtml(String(row.target||'—'))}</td><td style="${ds}">${escHtml(d||'—')}</td></tr>`
      })
      tableHtml += '</table></div>'
    }
    return `<div class="script-item"><div class="script-rank">第 ${s.rank||i+1} 推荐${subtitle?' · '+subtitle:''}</div><div class="script-text">${escHtml(s.marketing_text||'')}</div>${tableHtml}</div>`
  }).join('')
}

function renderResourceContext(rc) {
  const cp = rc.current_package || {}
  const usage = rc.usage || {}
  const tags = rc.tags || {}
  let html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
  html += '<div><div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">当前套餐</div>'
  html += Object.entries(cp).map(([k,v]) => `<div class="meta-row"><span>${k}</span><span>${escHtml(String(v))}</span></div>`).join('') || '<span style="color:var(--muted);font-size:13px">无</span>'
  html += '</div><div><div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">用户标签</div>'
  html += Object.entries(tags).slice(0,8).map(([k,v]) => `<div class="meta-row"><span>${k}</span><span>${escHtml(String(v))}</span></div>`).join('') || '<span style="color:var(--muted);font-size:13px">无</span>'
  html += '</div></div>'
  if (usage.data_usage || usage.voice_usage || usage.consumption) {
    const allUsage = {...(usage.data_usage||{}), ...(usage.voice_usage||{}), ...(usage.consumption||{})}
    html += '<hr class="divider"><div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">用量数据</div>'
    html += '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:4px">' + Object.entries(allUsage).map(([k,v]) => `<div class="meta-row"><span>${k}</span><span>${escHtml(String(v))}</span></div>`).join('') + '</div>'
  }
  return html
}

function renderPackages(pkgs) {
  if (!pkgs?.length) return '<p style="color:var(--muted);font-size:13px">无推荐产品数据</p>'
  return pkgs.map(p => {
    const name = p.offerName||p.package_name||p.productName||p.name||'—'
    const fee = p.initFee!==undefined?p.initFee:p.monthly_fee!==undefined?p.monthly_fee:p.price!==undefined?p.price:'?'
    const flow = p.offerFlow||p.data_quota||p.dataGB||p.flow||'?'
    const voice = p.offerVoice||p.voice_quota||p.voiceMinutes||p.voice||'?'
    const bw = p.bandWidth||p.bandwidth||(p.broadband&&p.broadband.bandwidth)||'无'
    const rights = p.otherRight||p.features||[]
    const pid = p.offerId||p.product_id||p.package_id||''
    let html = `<div style="border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px"><div style="font-weight:600;font-size:14px;margin-bottom:8px">${escHtml(name)}<span style="font-weight:400;color:var(--muted);font-size:12px;margin-left:8px">Rank ${p.rank||'?'}${pid?' · '+escHtml(pid):''}</span></div>`
    html += `<table class="diff-table"><tr><th>价格</th><td><b>${escHtml(String(fee))}元/月</b></td><th>流量</th><td><b>${escHtml(String(flow))}GB</b></td></tr>`
    html += `<tr><th>语音</th><td>${escHtml(String(voice))}分钟</td><th>宽带</th><td>${escHtml(String(bw))}</td></tr>`
    if (rights.length) html += `<tr><th>权益</th><td colspan="3">${escHtml(rights.join('、'))}</td></tr>`
    html += '</table></div>'
    return html
  }).join('')
}

function renderMeta(meta, json) {
  const rows = [
    ['elapsed_ms', (meta.elapsed_ms||0)+'ms'],
    ['推荐数量', meta.recommendation_count||0],
    ['话术数量', meta.script_count||0],
    ['trace_id', json.data?.callId||''],
    ['province', json.data?.province||''],
    ['intent', json.data?.intent||'']
  ]
  return rows.map(([k,v]) => `<div class="meta-row"><span style="color:var(--muted)">${k}</span><span><b>${escHtml(String(v))}</b></span></div>`).join('')
}

onMounted(loadSkills)
</script>

<style scoped>
.layout{display:grid;grid-template-columns:340px 1fr;gap:20px;padding:20px 24px;max-width:1400px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:20px}
.card-title{font-size:15px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px;margin-top:12px}
label:first-child{margin-top:0}
input,select,textarea{width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:7px;font-size:14px;outline:none;transition:.2s;font-family:inherit}
input:focus,select:focus,textarea:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:9px 20px;border:none;border-radius:7px;font-size:14px;font-weight:500;cursor:pointer;transition:.2s}
.btn-primary{background:var(--primary);color:#fff}.btn-primary:hover{background:#1d4ed8}
.btn-secondary{background:#f1f5f9;color:var(--text)}.btn-secondary:hover{background:#e2e8f0}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-block{width:100%}
.status-bar{display:flex;align-items:center;gap:8px;font-size:13px;padding:10px 14px;border-radius:7px;margin-bottom:12px}
.status-idle{background:#f1f5f9;color:var(--muted)}
.status-loading{background:#eff6ff;color:var(--primary)}
.status-ok{background:#f0fdf4;color:var(--success)}
.status-err{background:#fef2f2;color:var(--danger)}
.result-pane{display:flex;flex-direction:column;gap:16px}
.step-card{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.step-header{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#f8fafc;border-bottom:1px solid var(--border);cursor:pointer;user-select:none}
.step-title{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px}
.step-badge{font-size:12px;padding:2px 8px;border-radius:20px;font-weight:500}
.badge-blue{background:#dbeafe;color:#1d4ed8}
.badge-green{background:#dcfce7;color:#15803d}
.badge-purple{background:#f3e8ff;color:#7e22ce}
.badge-red{background:#fee2e2;color:#b91c1c}
.step-body{padding:14px 16px;display:none}
.step-body.open{display:block}
.skill-tags{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:4px}
.skill-tag{background:#dbeafe;color:#1e40af;font-size:12px;padding:4px 12px;border-radius:20px;cursor:pointer;transition:.2s}
.skill-tag:hover{background:#bfdbfe}
.skill-tag.selected{background:#1e40af;color:#fff}
.divider{border:none;border-top:1px solid var(--border);margin:16px 0}
.meta-row{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);padding:4px 0}
:deep(.script-item){background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px;margin-bottom:10px}
:deep(.script-rank){font-size:12px;color:var(--muted);margin-bottom:6px}
:deep(.script-text){font-size:14px;line-height:1.7;color:var(--text)}
:deep(.diff-table){width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
:deep(.diff-table th){background:#f1f5f9;padding:8px 10px;text-align:left;font-weight:600;color:var(--muted);border:1px solid var(--border)}
:deep(.diff-table td){padding:8px 10px;border:1px solid var(--border)}
:deep(pre){background:#0f172a;color:#e2e8f0;padding:14px;border-radius:8px;font-size:12px;overflow:auto;max-height:360px;line-height:1.6}
</style>
