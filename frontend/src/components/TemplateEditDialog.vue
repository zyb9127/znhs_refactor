<template>
  <el-dialog
    v-model="visible"
    :title="isNew ? '新建话术模板' : '编辑话术模板'"
    :width="dialogWidth"
    :close-on-click-modal="false"
    destroy-on-close
    class="tpl-edit-dialog"
    align-center
  >
    <el-form ref="formRef" :model="form" label-position="top" class="tpl-edit-form">

      <!-- ── Province + Intent (global 模式 / TemplateConfig) ── -->
      <template v-if="mode === 'global'">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item prop="province" required>
              <template #label><span class="fl">应用省份<span class="req-star">*</span></span></template>
              <el-select v-model="form.province" placeholder="请选择省份" style="width:100%" @change="onProvinceChange">
                <el-option v-for="o in editableProvinceOptions" :key="o.province" :value="o.province" :label="o.label" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item prop="intent" required>
              <template #label><span class="fl">场景分类<span class="req-star">*</span></span></template>
              <el-select v-model="form.intent" placeholder="请先选择省份" style="width:100%" :disabled="!form.province">
                <el-option v-for="i in editIntents" :key="i" :value="i" :label="i" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
      </template>

      <!-- ── Skill 模式：省份 + 场景分类（只读） ── -->
      <template v-if="mode === 'skill'">
        <el-row v-if="multiProduct && form.province" :gutter="16">
          <el-col :span="12">
            <el-form-item>
              <template #label><span class="fl">应用省份</span></template>
              <el-input :value="provinceLabel" disabled />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item>
              <template #label><span class="fl">场景分类</span></template>
              <el-input :value="form.intent || form.template_name" disabled />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item v-else prop="template_name" required>
          <template #label>
            <span class="fl">场景分类名称<span class="req-star">*</span></span>
            <span class="fh">必填。用于标识该话术模板，建议含意图与卖点，如「套餐推荐话术_5G升档」</span>
          </template>
          <el-input v-model="form.template_name" placeholder="如：套餐推荐话术_5G升档" clearable />
        </el-form-item>
      </template>

      <!-- ── 多产品 ID（multiProduct 模式） ── -->
      <el-form-item v-if="multiProduct" prop="product_ids_text">
        <template #label>
          <span class="fl">产品 ID</span>
          <span class="fh">该模板适用的套餐产品编码；多个用逗号 / 换行分隔；留空 = 该场景分类兜底模板（无匹配产品时使用）</span>
        </template>
        <el-input v-model="form.product_ids_text" type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          placeholder="例：prod001, prod002, prod003" />
      </el-form-item>

      <!-- ── 单产品 Product ID + Stage（非多产品模式） ── -->
      <el-row v-if="!multiProduct" :gutter="16">
        <el-col :span="12">
          <el-form-item prop="product_id">
            <template #label>
              <span class="fl">产品 ID</span>
              <span class="fh">留空 = 兜底模板</span>
            </template>
            <el-input v-model="form.product_id" placeholder="套餐产品 ID（可留空）" clearable />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item prop="stage">
            <template #label><span class="fl">应用环节</span></template>
            <el-autocomplete v-model="form.stage" :fetch-suggestions="stageSuggest"
              placeholder="如：切入环节" style="width:100%" clearable />
          </el-form-item>
        </el-col>
      </el-row>

      <!-- ── multiProduct 模式：环节 + 场景独立成行 ── -->
      <el-row v-if="multiProduct" :gutter="16">
        <el-col :span="12">
          <el-form-item prop="stage">
            <template #label>
              <span class="fl">应用环节</span>
              <span class="fh">话术所处销售环节（切入 / 推荐 / 异议处理 / 促成），用于按环节匹配话术</span>
            </template>
            <el-autocomplete v-model="form.stage" :fetch-suggestions="stageSuggest"
              placeholder="如：切入环节" style="width:100%" clearable />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item prop="scene">
            <template #label>
              <span class="fl">意图</span>
              <span class="fh">触发该话术的业务场景/意图（如套餐升级 / 流量超套）；留空 = 不限</span>
            </template>
            <el-autocomplete v-model="form.scene" :fetch-suggestions="sceneSuggest"
              placeholder="如：套餐升级" style="width:100%" clearable />
          </el-form-item>
        </el-col>
      </el-row>

      <!-- ── Scene（非多产品模式独立成行） ── -->
      <el-form-item v-if="!multiProduct" prop="scene">
        <template #label>
          <span class="fl">意图</span>
          <span class="fh">留空不限</span>
        </template>
        <el-autocomplete v-model="form.scene" :fetch-suggestions="sceneSuggest"
          placeholder="如：套餐升级" style="width:100%" clearable />
      </el-form-item>

      <!-- ══════════ ① 关联接口 ══════════ -->
      <el-form-item v-if="mode === 'skill' && availableApis.length" prop="linked_apis">
        <template #label>
          <span class="fl">① 关联接口</span>
          <span class="fh">可多选；用于标注本模板依赖的数据来源——勾选后，下方「可映射固定域」调色板与占位符校验只按所选接口产出的字段联动，并自动勾选其提供的关联变量</span>
        </template>
        <div class="api-grid">
          <label v-for="api in availableApis" :key="api.api_name"
            class="api-item" :class="{ checked: form.linked_apis.includes(api.api_name) }">
            <div class="api-head">
              <input type="checkbox" :checked="form.linked_apis.includes(api.api_name)"
                @change="toggleApi(api.api_name, $event.target.checked)" />
              <span class="api-name">{{ api.api_name }}</span>
              <el-tag v-if="!api.enabled" size="small" type="info" style="margin-left:6px;">已禁用</el-tag>
              <el-tag v-if="api.mock_mode" size="small" type="warning" style="margin-left:4px;">模拟数据</el-tag>
            </div>
            <div v-if="api.description" class="api-desc">{{ api.description }}</div>
            <div class="api-slots">
              <span class="api-slots-label">提供变量：</span>
              <template v-if="api.produced_slots.length || (api.passthrough && api.passthrough_fields && api.passthrough_fields.length)">
                <el-tag v-for="s in api.produced_slots" :key="s" size="small"
                  :type="isSlotLinked(s) ? 'success' : ''"
                  class="slot-tag" :title="SLOT_LABEL[s] || s">
                  {{ SLOT_LABEL[s] || s }}
                </el-tag>
                <!-- 透传模式：直接暴露的入参字段（非 7 域映射，作为可引用变量）-->
                <el-tag v-for="f in (api.passthrough ? api.passthrough_fields : [])" :key="'pt-'+f"
                  size="small" type="success" effect="plain" class="slot-tag" :title="'透传入参字段：' + f">
                  {{ f }}
                </el-tag>
              </template>
              <!-- 透传模式但未显式选字段：透传全部顶层入参字段 -->
              <span v-else-if="api.passthrough" class="api-slots-empty">透传全部顶层入参字段</span>
              <span v-else class="api-slots-empty">未配置映射规则</span>
            </div>
          </label>
        </div>
        <div v-if="form.linked_apis.length === 0" class="api-hint">
          ⚠ 未勾选任何接口时，按本技能下<strong>全部启用</strong>的接口计算可用字段（运行时也会调用全部启用接口）
        </div>
      </el-form-item>

      <!-- ══════════ ② 模板内容 ══════════ -->
      <el-form-item prop="template_content" required>
        <template #label>
          <span class="fl">② 模板内容<span class="req-star">*</span></span>
          <span class="fh">必填。支持 {变量} 占位符，占位符须与下方「可映射固定域」对应，避免生成 xx / 空值</span>
        </template>

        <!-- 优化四/五：可映射固定域调色板 —— 与「① 关联接口」联动：仅展示当前生效接口产出的域/透传字段 -->
        <div v-if="multiProduct" class="dragvar-palette">
          <span class="dragvar-palette-label">可映射固定域</span>
          <span class="dragvar-palette-tip">点击/拖拽插入整块域；带 ▾ 的域可展开选「子字段」精准匹配到槽位；随「① 关联接口」勾选联动</span>
          <template v-if="insertableVars.length">
            <span v-for="v in insertableVars" :key="v.key"
              class="dragvar-chip" :class="'chip-' + (v.kind || 'domain')" draggable="true"
              :title="chipTitle(v)"
              @dragstart="onVarDragStart($event, v.key)"
              @click="insertVar(v.key)">
              {{ v.label }}<code>{{ '{' + v.key + '}' }}</code>
              <span v-if="chipSrcTag(v)" class="chip-src">{{ chipSrcTag(v) }}</span>
              <el-popover v-if="v.subfields && v.subfields.length" placement="bottom-start"
                :width="320" trigger="click" popper-class="subfield-pop">
                <template #reference>
                  <span class="chip-caret" title="展开选择子字段（更精准匹配槽位）" @click.stop>▾</span>
                </template>
                <div class="subfield-panel">
                  <div class="subfield-head">
                    「{{ v.label }}」子字段（点击/拖入 = 精准占位符 {{ '{域[子键]}' }}）<br/>
                    <span class="subfield-head-note">字段名与样例值均为<b>映射转换后</b>（重命名/单位换算完成）的最终参数，运行时按同名动态取真实值</span>
                  </div>
                  <div v-for="s in v.subfields" :key="s.token" class="subfield-row"
                    draggable="true" :title="'插入 {' + s.token + '}'"
                    @dragstart="onTokenDragStart($event, s.token)"
                    @click="insertToken(s.token)">
                    <span class="subfield-name">{{ s.label }}</span>
                    <span v-if="s.path.includes('.')" class="subfield-path">{{ s.path }}</span>
                    <span v-if="subSampleText(s)" class="subfield-sample">例：{{ subSampleText(s) }}</span>
                  </div>
                </div>
              </el-popover>
            </span>
          </template>
          <span v-else class="dragvar-empty">先在「① 关联接口」勾选接口，这里会列出该接口映射产出的可用固定域</span>
        </div>

        <!-- 智能填充占位符：LLM 基于原话术语义，把具体值/xx 自动改写为 {占位符}（环境感知模型） -->
        <div v-if="canAutoFill" class="autofill-bar">
          <el-button size="small" type="primary" plain
            :loading="autoFillLoading" :disabled="!(form.template_content || '').trim()"
            @click="runAutoFill">
            🪄 智能填充占位符
          </el-button>
          <span class="autofill-hint">AI 优先把 XX/***/待填标记替换为可映射占位符，尽量保留原文不改写；替换前会展示对比供确认</span>
        </div>

        <div ref="contentWrapRef" class="content-area-wrap" :class="{ 'drag-over': contentDragActive }"
          @dragenter="onContentDragEnter"
          @dragover="onContentDragOver"
          @dragleave="onContentDragLeave"
          @drop="onContentDrop">
          <el-input ref="contentInputRef" v-model="form.template_content" type="textarea"
            :rows="6" resize="vertical" class="content-area"
            placeholder="如：我看您近3个月平均用了 {usage_line[近3月平均流量(GB)]}GB，推荐您办理 {pkg_brief[offerName]}！"
            @input="onContentInput" />
          <!-- 自定义拖拽落点光标：拖动时按鼠标位置实时闪烁于精确插入点，松手即插到此处 -->
          <div v-show="contentDragActive && dropCaret.visible" class="drop-caret"
            :style="{ left: dropCaret.x + 'px', top: dropCaret.y + 'px', height: dropCaret.h + 'px' }"></div>
          <div v-if="contentDragActive" class="drag-drop-hint">松开鼠标：插入到闪烁光标处</div>
        </div>
        <div v-if="contentVarHint" class="content-var-hint" :class="contentVarHint.level">
          {{ contentVarHint.text }}
        </div>
      </el-form-item>

      <!-- ══════════ ③ 关联变量 ══════════
           multiProduct（Skill 管理主流程）已移除本区：接口数据域变量由后端 auto_domain_vars
           自动并入 linked_vars；模板引用到的计算生成变量（pkg_brief/diff_str/pkg_fee…）由
           build_prompt 运行时按占位符自动注入且空值自动跳过，手动勾选纯属冗余。仅单产品/global
           旧流程保留手选网格。可拖入的字段仍由 ② 模板内容上方「可映射固定域」调色板提供。-->
      <el-form-item v-if="!multiProduct" prop="linked_vars">
        <template #label>
          <span class="fl">③ 关联变量</span>
          <el-tooltip placement="right" :show-after="200">
            <template #content>
              <div style="max-width:320px;line-height:1.7;font-size:13px;">
                <b>关联变量的作用：</b><br/>
                运行时系统会把勾选的变量数据取出，作为上下文传给大模型，大模型再依此填充话术里的 <code>{变量名}</code> 占位符。<br/><br/>
                <b>建议：</b>话术内容里用了哪个 <code>{变量}</code>，就在下方勾选对应变量，两者要配套。<br/><br/>
                <b>差异表格（table）</b>：仅在前端话术结果展示，不会注入 Prompt。
              </div>
            </template>
            <span class="fh" style="cursor:help;text-decoration:underline dotted;">
              运行时把勾选的数据注入大模型 Prompt，填充话术占位符 ⓘ
            </span>
          </el-tooltip>
        </template>

        <!-- 单产品/global 旧流程：保持原 LINKED_VAR_LIST 手选网格 -->
        <template>
          <div class="var-grid">
            <label v-for="v in LINKED_VAR_LIST" :key="v.key"
              class="var-item"
              :class="{
                checked: form.linked_vars.includes(v.key),
                supplied: providedSlotKeys.has(v.slot),
                missing: form.linked_vars.includes(v.key) && v.slot && !providedSlotKeys.has(v.slot),
              }"
              :title="v.desc">
              <input type="checkbox" :checked="form.linked_vars.includes(v.key)"
                @change="toggleVar(v.key, $event.target.checked)" />
              <span class="var-name">{{ v.label }}</span>
              <span class="var-key">{{ v.key }}</span>
              <span class="var-tag-wrap">
                <el-tag v-if="v.slot && providedSlotKeys.has(v.slot)" size="small" type="success" class="slot-tag">
                  ✓ 已由接口提供
                </el-tag>
                <el-tag v-else-if="v.slot" size="small" type="info" class="slot-tag">
                  需补接口
                </el-tag>
                <el-tag v-else size="small" type="" class="slot-tag">辅助</el-tag>
              </span>
            </label>
          </div>
          <div class="custom-var-row">
            <el-input v-model="customVarInput" size="small"
              placeholder="自定义变量名" style="width:180px;" @keyup.enter="addCustomVar" />
            <el-button size="small" @click="addCustomVar">添加</el-button>
            <el-button size="small" type="primary" link @click="autoSuggestVars">
              根据已选接口自动推荐
            </el-button>
            <template v-if="form.linked_vars.length">
              <span class="var-selected-label">已选：</span>
              <el-tag v-for="v in form.linked_vars" :key="v" closable size="small"
                type="warning" style="margin: 0 2px 2px 0;" @close="removeVar(v)">{{ v }}</el-tag>
            </template>
          </div>
        </template>
      </el-form-item>

      <!-- ══════════ ④ 话术要求 ══════════ -->
      <el-form-item prop="script_requirement">
        <template #label>
          <span class="fl">④ 话术要求</span>
          <span class="fh">字数、语气与卖点组织等风格要求（防编造 / 防串填 / 占位符匹配已由系统内置生成规则保证，此处只需补充业务侧重）；可直接选用下方 context 工程标准模板</span>
        </template>
        <div class="req-preset-bar">
          <span class="req-preset-label">标准话术要求：</span>
          <el-button v-for="p in SCRIPT_REQ_PRESETS" :key="p.name" size="small" plain
            @click="form.script_requirement = p.text">{{ p.name }}</el-button>
          <el-button link size="small" style="font-size:12px;"
            @click="form.script_requirement = DEFAULT_SCRIPT_REQ">恢复默认</el-button>
        </div>
        <el-input v-model="form.script_requirement" type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          placeholder="如：口语化、150字以内、贴合用户痛点" />
      </el-form-item>

      <!-- ══════════ ⑤ Prompt 预览 ══════════ -->
      <el-form-item>
        <template #label>
          <span class="fl">⑤ Prompt 实时预览</span>
          <span class="fh">下方为运行时真正发送给大模型的<b>完整提示词</b>（示例数据填充，含上下文数据 / 话术模板 / 生成规则），与线上生成同一条 build_prompt 路径</span>
        </template>
        <div class="prompt-preview">
          <div class="pp-toggle">
            <span class="pp-toggle-main" @click="showPreview = !showPreview">
              {{ showPreview ? '▲' : '▼' }} 展开完整 Prompt
            </span>
            <div v-if="showPreview" class="pp-toggle-right">
              <el-radio-group v-model="previewMode" size="small" @change="refreshPreview">
                <el-radio-button label="sample">示例数据</el-radio-button>
                <el-radio-button label="schema">字段来源</el-radio-button>
              </el-radio-group>
              <el-button link size="small" :loading="previewLoading" @click="refreshPreview">刷新</el-button>
            </div>
          </div>
          <template v-if="showPreview">
            <div class="pp-mode-hint">
              {{ previewMode === 'schema'
                ? '字段来源视图：展示每个上下文变量由哪些接口出参字段 / 映射域得到（配置视图，非实际发送内容）'
                : '示例数据视图：接口出参示例填充后运行态真正发送给大模型的完整提示词' }}
            </div>
            <div v-if="previewError" class="pp-error">
              预览生成失败：{{ previewError }}（以下为本地近似，仅供参考）
            </div>
            <pre class="pp-body">{{ previewText || (previewLoading ? '生成中…' : promptPreviewText) }}</pre>
          </template>
        </div>
      </el-form-item>

      <!-- ── 状态 ── -->
      <el-form-item prop="status">
        <template #label><span class="fl">模板状态</span></template>
        <el-radio-group v-model="form.status">
          <el-radio value="online">上线</el-radio>
          <el-radio value="offline">下线</el-radio>
        </el-radio-group>
      </el-form-item>

    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="handleSave">保存</el-button>
    </template>

    <!-- ── 智能填充占位符：改写前后对比确认 ── -->
    <el-dialog v-model="autoFillVisible" title="🪄 智能填充占位符 — 确认改写结果"
      width="640px" append-to-body destroy-on-close>
      <div class="autofill-result">
        <div class="af-block">
          <div class="af-block-title">改写前</div>
          <div class="af-text af-before">{{ autoFillOriginal }}</div>
        </div>
        <div class="af-block">
          <div class="af-block-title">改写后<span class="af-sub">（占位符将在运行时按真实用户数据填充）</span></div>
          <div class="af-text af-after" v-html="autoFillHighlighted"></div>
        </div>
        <div v-if="autoFillUsedVars.length" class="af-vars">
          <span class="af-vars-label">使用占位符：</span>
          <el-tag v-for="v in autoFillUsedVars" :key="v" size="small" type="success"
            style="margin:2px;font-family:monospace;">{{ '{' + v + '}' }}</el-tag>
        </div>
        <el-alert v-if="autoFillUnknownVars.length" type="warning" :closable="false" show-icon
          :title="`以下占位符不在可映射清单内，已保留原文，请人工检查：${autoFillUnknownVars.map(v => '{' + v + '}').join('、')}`"
          style="margin-top:8px;" />
        <div v-if="autoFillNotes" class="af-notes">💡 {{ autoFillNotes }}</div>
      </div>
      <template #footer>
        <el-button @click="autoFillVisible = false">放弃</el-button>
        <el-button type="primary" @click="applyAutoFill">应用改写结果</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { apiFetch } from '@/utils/apiUrl'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  template: { type: Object, default: () => ({}) },
  isNew: { type: Boolean, default: false },
  /** 'skill' | 'global' */
  mode: { type: String, default: 'skill' },
  editableProvinceOptions: { type: Array, default: () => [] },
  editIntents: { type: Array, default: () => [] },
  /**
   * skill 模式下传入：当前 skill 的接口节点列表
   * 形如：[{ api_name, description, enabled, mock_mode, produced_slots: ['usage','tags'] }, ...]
   * produced_slots 由父组件根据 field_transform / response_extract 推断
   */
  availableApis: { type: Array, default: () => [] },
  /**
   * 多产品分组编辑模式：与 TemplateConfig 的"编辑模板（多产品）"行为一致。
   *  - 产品 ID 改为 textarea 多产品输入（逗号/换行分隔）
   *  - 关联变量改为按 (province, intent) 动态加载的列表（context_vars）
   *  - 模板内容变化时调用 infer_vars 推断推荐变量
   */
  multiProduct: { type: Boolean, default: false },
  /** 多产品模式下，省份显示名映射（如 { beijing: '北京' }），用于头部 disabled 展示 */
  provinceMap: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:modelValue', 'save', 'province-change'])

const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
})

const dialogWidth = computed(() => {
  if (typeof window === 'undefined') return '900px'
  return Math.min(960, Math.round(window.innerWidth * 0.92)) + 'px'
})

// ── 默认话术要求（context 工程版：只补充"风格 + 业务侧重"，防编造/防串填/占位符匹配
//    已由后端 SCRIPT_GEN_RULES 内置为生成规则 1-4，此处不重复）─────────────
const DEFAULT_SCRIPT_REQ = '结合【上下文数据】中的当前套餐、历史用量与用户标签，先点出最突出的用户痛点，再用推荐套餐对应字段的真实值说明如何解决；只讲有数据支撑的卖点，口语化、可直接对客播报，150字以内，结尾自然引导办理。'

// ── 标准话术要求预设（context 工程优化模板，点击即填入）──────────
// 定位：引导模型把「入参/映射域事实」精准对应到「话术槽位」，突出痛点→卖点闭环；
// 机械层规则（不臆造/不串填/空值跳过/同名占位符匹配）由框架 SCRIPT_GEN_RULES 保证。
// 字数：营销话术一般 150 字以内；「精简」为更短的单卖点变体。
const SCRIPT_REQ_PRESETS = [
  { name: '标准', text: DEFAULT_SCRIPT_REQ },
  { name: '精简', text: '口语化、60字以内；只讲 1 个最贴合用户用量/痛点的核心卖点（用对应字段真实值支撑），结尾一句办理引导。' },
  { name: '痛点驱动', text: '先用历史用量与用户标签点出痛点（如流量饱和度高、超套、老旧套餐），再用推荐套餐差异（月费/流量/语音等对应字段真实值）说明如何针对性解决；无对应数据的痛点或卖点不提；口语化、150字以内，结尾引导办理。' },
  { name: '严谨合规', text: '严格只用给定字段的真实值，不虚构数字/套餐名/优惠；任一字段缺失或为 0 时不提及该项，也不得用其他字段代填；150字以内，结尾引导办理。' },
]

// ── 关联变量定义（与 steps/script_step.py 中 _VAR_LABELS 严格对齐）──
// slot 字段表示该变量需要的 resource_context 标准域；为空表示辅助变量（无需接口提供）
const LINKED_VAR_LIST = [
  { key: 'cur_brief',     label: '当前套餐信息',   slot: 'current_package',      desc: '映射 current_package 域' },
  { key: 'pkg_brief',     label: '推荐产品信息',   slot: 'recommended_packages', desc: '从 recommended_packages[0] 格式化' },
  { key: 'diff_str',      label: '套餐差异',       slot: '__computed__',         desc: '由 PackageDiff 计算（自动）' },
  { key: 'usage_line',    label: '历史用量',       slot: 'usage',                desc: '映射 usage 域' },
  { key: 'user_tags',     label: '用户标签',       slot: 'tags',                 desc: '映射 tags 域' },
  { key: 'user_info',     label: '用户基础信息',   slot: 'user_info',            desc: '映射 user_info 域' },
  { key: 'user_profile',  label: '用户画像',       slot: 'user_profile',         desc: '映射 user_profile 域' },
  { key: 'domain_ext',    label: '扩展信息',       slot: 'domain_ext',           desc: '映射 domain_ext 域' },
  { key: 'extra_info',    label: '主服务补充信息', slot: '',                     desc: '主服务 extra_info 透传' },
  { key: 'extra_context', label: '模板匹配上下文', slot: '',                     desc: '主服务 extra_context 透传' },
  { key: 'table',         label: '差异表格',       slot: '',                     desc: '仅前端展示，不进 LLM' },
]

const SLOT_LABEL = {
  current_package: '当前套餐(current_package)',
  usage: '历史用量(usage)',
  tags: '用户标签(tags)',
  user_info: '用户基础信息(user_info)',
  recommended_packages: '推荐产品(recommended_packages)',
  user_profile: '用户画像(user_profile)',
  domain_ext: '扩展域(domain_ext)',
}

const STAGE_OPTIONS = ['切入环节', '推荐环节', '异议处理', '促成成交']
const SCENE_OPTIONS = ['流量超套', '套餐升级', '套餐降档', '新业务推荐']

// ── 表单状态 ──────────────────────────────────────────────
const form = reactive({
  province: '', intent: '', template_name: '',
  product_id: '', product_ids_text: '',
  stage: '', scene: '',
  template_content: '', linked_vars: [], linked_apis: [],
  script_requirement: DEFAULT_SCRIPT_REQ,
  prompt_template: '', status: 'online', created_by: '',
})

const showPreview = ref(true)
const customVarInput = ref('')

// ── 后端真实 Prompt 预览（与运行态 build_prompt 同一路径）──────────
const previewText = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const previewMode = ref('sample')   // 'sample'=示例数据填充 | 'schema'=字段来源视图
let _previewTimer = null

/** 构造发给 /api/templates/preview_prompt 的模板对象（字段与运行态一致） */
function buildPreviewTemplate() {
  return {
    template_name:      form.template_name || form.intent || '',
    template_content:   form.template_content || '',
    prompt_template:    form.prompt_template || '',
    linked_vars:        [...form.linked_vars],
    script_requirement: form.script_requirement || '',
    scene:              form.scene || '',
    stage:              form.stage || '',
    product_id:         form.product_id || '',
    intent:             form.intent || form.template_name || '',
    province:           form.province || '',
  }
}

/** 调后端返回运行态完整 Prompt（示例数据填充） */
async function refreshPreview() {
  if (!showPreview.value) return
  previewLoading.value = true
  previewError.value = ''
  try {
    // 直传透传字段：把样例值作为 extra_info 示例，并声明 passthrough_fields，
    // 使预览的【上下文数据】能逐字段展示透传入参（与运行态一致）
    const ptVars = passthroughVars.value
    const sampleExtra = {}
    for (const v of ptVars) {
      if (v.sample !== undefined && v.sample !== null) sampleExtra[v.key] = v.sample
    }
    const payload = {
      template: buildPreviewTemplate(),
      province: form.province || '',
      intent: form.intent || form.template_name || '',
      mode: previewMode.value,
    }
    if (ptVars.length) {
      payload.passthrough_fields = ptVars.map(v => v.key)
      if (Object.keys(sampleExtra).length) payload.sample_data = { extra_info: sampleExtra }
    }
    const res = await apiFetch('/api/templates/preview_prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const json = await res.json()
    if (json.code === 200 && json.data && typeof json.data.prompt === 'string') {
      previewText.value = json.data.prompt
    } else {
      previewError.value = json.detail || json.message || '未知错误'
    }
  } catch (e) {
    previewError.value = e?.message || String(e)
  } finally {
    previewLoading.value = false
  }
}

/** 防抖刷新（表单频繁变更时避免过多请求） */
function schedulePreview() {
  clearTimeout(_previewTimer)
  _previewTimer = setTimeout(refreshPreview, 500)
}

const provinceLabel = computed(() => props.provinceMap?.[form.province] || form.province || '—')

// ── 动态变量（multiProduct 模式：按 (province, intent) 加载 context_vars） ──
const FALLBACK_DYN_VARS = [
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
const dynamicVarList = ref([...FALLBACK_DYN_VARS])
const _loadedVarKey = ref('')

// 生成变量 = 话术生成层自动产出（pkg_brief / diff_str / pkg_fee / pkg_flow / pkg_voice / table）
// available===false 表示该技能样例下注定取不到值（如推荐条无语音 → pkg_voice、当前套餐缺失 → diff_str），
// 隐藏对应标签，避免拖入模板后运行时被防编造规则跳过、生成 xx/空值。
// 注：「接口数据域变量」手选区已按首页优化要求移除，改由后端默认全选，故不再需要 domainVarList。
const generatedVarList = computed(() =>
  dynamicVarList.value.filter(v => v.source === 'script_step' && v.available !== false))

async function loadContextVars(province, intent) {
  const key = `${province}:${intent}`
  if (!province || !intent || _loadedVarKey.value === key) return
  try {
    const res = await apiFetch(`/api/skills/${encodeURIComponent(province)}/${encodeURIComponent(intent)}/context_vars`)
    const json = await res.json()
    if (json.code === 200 && Array.isArray(json.data) && json.data.length) {
      dynamicVarList.value = json.data
      _loadedVarKey.value = key
    }
  } catch (_) { /* 静默降级 */ }
}

// (province, intent) 变化时重新加载动态变量
watch(() => [form.province, form.intent || form.template_name, props.multiProduct, props.modelValue], ([p, i, mp, open]) => {
  if (!mp || !open) return
  if (p && i) loadContextVars(p, i)
})

// 当前已勾选接口产出的所有 slot 集合
const providedSlotKeys = computed(() => {
  const slots = new Set()
  // 已勾选接口的产出
  for (const api of props.availableApis) {
    if (form.linked_apis.includes(api.api_name)) {
      for (const s of (api.produced_slots || [])) slots.add(s)
    }
  }
  // 若一个 linked_api 都没勾，按"全部启用接口"算，便于初次创建时给提示
  if (form.linked_apis.length === 0) {
    for (const api of props.availableApis) {
      if (api.enabled !== false) {
        for (const s of (api.produced_slots || [])) slots.add(s)
      }
    }
  }
  return slots
})

function isSlotLinked(slot) {
  // 该接口产出的 slot 是否被某个已勾的 linked_var 真正使用
  return LINKED_VAR_LIST.some(v => v.slot === slot && form.linked_vars.includes(v.key))
}

// ── 优化四/五：可映射固定域调色板（multiProduct） ─────────────
// 标准域 slot → 话术变量 key/label（话术占位符使用的名字）
const SLOT_TO_VAR = {
  current_package:      { key: 'current_package', label: '当前套餐信息' },
  usage:                { key: 'usage',           label: '历史用量' },
  tags:                 { key: 'tags',            label: '用户标签' },
  user_info:            { key: 'user_info',       label: '用户基础信息' },
  user_profile:         { key: 'user_profile',    label: '用户画像' },
  domain_ext:           { key: 'domain_ext',      label: '扩展信息' },
  recommended_packages: { key: 'pkg_brief',       label: '推荐产品信息' },
}

// 当前生效的接口集合（与运行口径一致）：勾选了接口 → 勾选集合；未勾选 → 全部启用接口
const activeApiNames = computed(() => {
  const useAll = form.linked_apis.length === 0
  return new Set(
    props.availableApis
      .filter(a => (useAll ? a.enabled !== false : form.linked_apis.includes(a.api_name)))
      .map(a => a.api_name)
  )
})

// 已勾选接口（未勾选=全部启用接口）映射产出的可用固定域（带产出接口名，供调色板标注来源）
const mappedDomainVars = computed(() => {
  if (!props.multiProduct) return []
  const slotApis = new Map()   // slot → 产出接口名列表
  const useAll = form.linked_apis.length === 0
  for (const api of props.availableApis) {
    const active = useAll ? api.enabled !== false : form.linked_apis.includes(api.api_name)
    if (!active) continue
    for (const s of (api.produced_slots || [])) {
      if (!slotApis.has(s)) slotApis.set(s, [])
      const arr = slotApis.get(s)
      if (!arr.includes(api.api_name)) arr.push(api.api_name)
    }
  }
  const out = []
  const byKey = new Map()
  for (const [s, apis] of slotApis) {
    const v = SLOT_TO_VAR[s]
    if (!v) continue
    if (!byKey.has(v.key)) {
      const item = { ...v, apis: [...apis] }
      byKey.set(v.key, item)
      out.push(item)
    } else {
      const item = byKey.get(v.key)
      for (const a of apis) if (!item.apis.includes(a)) item.apis.push(a)
    }
  }
  return out
})

// 直传透传字段（context_vars 返回 source=passthrough）：入参字段直接作为可用占位符。
// 与「① 关联接口」联动：仅展示当前生效接口产出的透传字段（api_names 由后端返回；
// 旧响应无 api_names 或本弹窗未传接口列表时不过滤，保持兼容）
const passthroughVars = computed(() =>
  dynamicVarList.value.filter(v => {
    if (v.source !== 'passthrough') return false
    const producers = Array.isArray(v.api_names) ? v.api_names : []
    if (!producers.length || !props.availableApis.length) return true
    return producers.some(n => activeApiNames.value.has(n))
  }))

// 各域「下一级子字段」映射：key → [{token,label,path,sample}]（后端 context_vars 按接口 mock 映射推导）
// 用于调色板展开精确子字段占位符（{域[子键]}），把入参字段更精准地匹配到话术槽位。
const subfieldsByKey = computed(() => {
  const map = {}
  for (const v of dynamicVarList.value) {
    if (Array.isArray(v.subfields) && v.subfields.length) map[v.key] = v.subfields
  }
  return map
})

// 可拖入模板的变量 = 映射固定域 + 直传透传字段 + 计算生成变量（去重，带来源标注 + 可选子字段）
const insertableVars = computed(() => {
  const sub = subfieldsByKey.value
  const list = mappedDomainVars.value.map(v => ({
    key: v.key, label: v.label, apis: v.apis || [], kind: 'domain',
    subfields: sub[v.key] || [],
  }))
  const seen = new Set(list.map(v => v.key))
  for (const p of passthroughVars.value) {
    if (!seen.has(p.key)) {
      seen.add(p.key)
      list.push({
        key: p.key, label: p.label || p.key,
        apis: Array.isArray(p.api_names) ? p.api_names : [], kind: 'passthrough',
        subfields: sub[p.key] || [],
      })
    }
  }
  for (const g of generatedVarList.value) {
    if (!seen.has(g.key)) {
      seen.add(g.key)
      list.push({ key: g.key, label: g.label, apis: [], kind: 'generated', subfields: sub[g.key] || [] })
    }
  }
  return list
})

// 调色板 chip 的来源角标 / 悬浮提示
function chipSrcTag(v) {
  if (v.kind === 'generated') return '计算生成'
  if (v.kind === 'passthrough') return v.apis[0] ? `直传·${v.apis[0]}` : '直传字段'
  return v.apis[0] ? `接口·${v.apis[0]}` : ''
}
function chipTitle(v) {
  const base = `点击或拖入模板：{${v.key}}`
  if (v.kind === 'generated') return `${base}\n来源：话术生成步骤实时计算，不依赖接口`
  if (!v.apis.length) return base
  const kindTxt = v.kind === 'passthrough' ? '直传接口（透传入参）' : '接口出参映射'
  return `${base}\n来源${kindTxt}：${v.apis.join('、')}`
}

// 合法占位符 key 集合（含常见别名，避免误报；与后端 prompt_builder 同义变量组一致）
const availableVarKeys = computed(() => {
  const s = new Set(insertableVars.value.map(v => v.key))
  const ALIAS = {
    cur_brief: 'current_package', cur_name: 'current_package',
    usage_line: 'usage', user_tags: 'tags', pkg_name: 'pkg_brief',
  }
  for (const [alias, dom] of Object.entries(ALIAS)) if (s.has(dom)) s.add(alias)
  // 反向：调色板给的是别名（如单产品模式 cur_brief）时也接受标准域名
  for (const [alias, dom] of Object.entries(ALIAS)) if (s.has(alias)) s.add(dom)
  return s
})

// ── 智能填充占位符（LLM 语义改写，环境感知模型）─────────────
const canAutoFill = computed(() => !!(form.province && (form.intent || form.template_name)))
const autoFillLoading    = ref(false)
const autoFillVisible    = ref(false)
const autoFillOriginal   = ref('')
const autoFillResult     = ref('')
const autoFillUsedVars   = ref([])
const autoFillUnknownVars = ref([])
const autoFillNotes      = ref('')

function _escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
// 改写后文本：合法占位符高亮为绿色 chip，便于一眼核对替换点
const autoFillHighlighted = computed(() => {
  const used = new Set(autoFillUsedVars.value)
  return _escapeHtml(autoFillResult.value).replace(
    /\{([A-Za-z_][A-Za-z0-9_.]*)\}/g,
    (m, key) => used.has(key)
      ? `<span class="af-token">{${key}}</span>`
      : `<span class="af-token-unknown">{${key}}</span>`,
  )
})

async function runAutoFill() {
  const content = (form.template_content || '').trim()
  if (!content || autoFillLoading.value) return
  autoFillLoading.value = true
  try {
    const res = await apiFetch('/api/templates/auto_fill_placeholders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        province: form.province,
        intent: form.intent || form.template_name,
        template_content: content,
        linked_apis: [...form.linked_apis],
      }),
    })
    const json = await res.json()
    if (json.code === 200 && json.data?.filled_template) {
      autoFillOriginal.value    = content
      autoFillResult.value      = json.data.filled_template
      autoFillUsedVars.value    = json.data.used_vars || []
      autoFillUnknownVars.value = json.data.unknown_vars || []
      autoFillNotes.value       = json.data.notes || ''
      if (autoFillResult.value === content) {
        ElMessage.info('AI 未找到可替换为占位符的内容，模板保持不变')
      } else {
        autoFillVisible.value = true
      }
    } else {
      ElMessage.error(json.detail || json.message || '智能填充失败')
    }
  } catch (e) {
    ElMessage.error(e?.message || '请求失败')
  } finally {
    autoFillLoading.value = false
  }
}

function applyAutoFill() {
  form.template_content = autoFillResult.value
  // 非 multiProduct 旧流程：占位符需配套勾选关联变量（multiProduct 由后端自动并入/运行时注入）
  if (!props.multiProduct) {
    for (const k of autoFillUsedVars.value) {
      if (!form.linked_vars.includes(k)) form.linked_vars.push(k)
    }
  }
  autoFillVisible.value = false
  ElMessage.success(`✅ 已应用，共 ${autoFillUsedVars.value.length} 个占位符`)
}

// ── 光标位置插入 / 拖拽插入 ─────────────────────────────
const contentInputRef = ref(null)
function _contentTextarea() {
  return contentInputRef.value?.$el?.querySelector('textarea') || null
}
function insertAtCursor(text) {
  const el = _contentTextarea()
  const cur = form.template_content || ''
  if (!el) { form.template_content = cur + text; return }
  const s = el.selectionStart ?? cur.length
  const e = el.selectionEnd ?? cur.length
  form.template_content = cur.slice(0, s) + text + cur.slice(e)
  nextTick(() => {
    el.focus()
    const pos = s + text.length
    try { el.setSelectionRange(pos, pos) } catch (_) { /* ignore */ }
  })
}
function insertVar(key) { insertAtCursor('{' + key + '}') }
function onVarDragStart(e, key) {
  // 原生拖入 textarea 时自动在落点插入 {key}
  e.dataTransfer.setData('text/plain', '{' + key + '}')
  e.dataTransfer.effectAllowed = 'copy'
}
// 子字段占位符：整 token 已含方括号路径（如 usage[data_usage][近6月平均流量(GB)]），直接插入
function insertToken(token) { insertAtCursor('{' + token + '}') }
function onTokenDragStart(e, token) {
  e.dataTransfer.setData('text/plain', '{' + token + '}')
  e.dataTransfer.effectAllowed = 'copy'
}
// 子字段样例值预览（截断，避免过长）
function subSampleText(s) {
  if (s == null || s.sample == null || s.sample === '') return ''
  let t = String(s.sample)
  if (t.length > 18) t = t.slice(0, 18) + '…'
  return t
}

// ── 拖拽落点可视化（业内通用方案：mirror div 定位 + 自定义闪烁光标 + 接管插入）──
// 原理：
//  1) 由鼠标坐标经 caretRangeFromPoint / caretPositionFromPoint 求出 textarea 内字符偏移；
//  2) 用「镜像 div」复刻 textarea 样式，量出该偏移对应的像素坐标，渲染一个自定义闪烁光标覆盖层；
//  3) drop 时按同一偏移接管插入，保证「看到的光标 = 实际落点」，彻底解决拖不准 / 无光标。
const contentWrapRef = ref(null)
const contentDragActive = ref(false)
const dropCaret = reactive({ visible: false, x: 0, y: 0, h: 18 })
let _dropOffset = null

// 复制到镜像 div 的样式项（textarea-caret-position 通用实现）
const _MIRROR_STYLE_PROPS = [
  'boxSizing', 'width', 'height',
  'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'fontStyle', 'fontVariant', 'fontWeight', 'fontStretch', 'fontSize',
  'lineHeight', 'fontFamily', 'textAlign', 'textTransform', 'textIndent',
  'letterSpacing', 'wordSpacing', 'tabSize',
]

// 鼠标坐标 → textarea 内字符偏移
function _offsetFromPoint(x, y) {
  try {
    if (document.caretRangeFromPoint) {           // Chrome / Safari / Edge
      const r = document.caretRangeFromPoint(x, y)
      if (r && typeof r.startOffset === 'number') return r.startOffset
    }
    if (document.caretPositionFromPoint) {         // Firefox
      const p = document.caretPositionFromPoint(x, y)
      if (p && typeof p.offset === 'number') return p.offset
    }
  } catch (_) { /* 不支持则回退 */ }
  return null
}

// 字符偏移 → textarea 内像素坐标（相对 textarea 左上角，已计入换行/滚动）
function _caretCoords(el, offset) {
  const cs = window.getComputedStyle(el)
  const div = document.createElement('div')
  const s = div.style
  s.position = 'absolute'
  s.visibility = 'hidden'
  s.whiteSpace = 'pre-wrap'
  s.wordWrap = 'break-word'
  s.overflow = 'hidden'
  _MIRROR_STYLE_PROPS.forEach(p => { s[p] = cs[p] })
  // 高度按内容撑开，宽度沿用 textarea，才能得到一致的换行
  s.height = 'auto'
  div.textContent = (el.value || '').substring(0, offset)
  const span = document.createElement('span')
  span.textContent = (el.value || '').substring(offset) || '.'
  div.appendChild(span)
  document.body.appendChild(div)
  const top = span.offsetTop + parseFloat(cs.borderTopWidth || '0')
  const left = span.offsetLeft + parseFloat(cs.borderLeftWidth || '0')
  document.body.removeChild(div)
  return { top: top - el.scrollTop, left: left - el.scrollLeft }
}

function _updateDropCaret(e) {
  const el = _contentTextarea()
  const wrap = contentWrapRef.value
  if (!el || !wrap) return
  const offset = _offsetFromPoint(e.clientX, e.clientY)
  if (offset == null) { dropCaret.visible = false; _dropOffset = null; return }
  _dropOffset = offset
  const coords = _caretCoords(el, offset)
  const taRect = el.getBoundingClientRect()
  const wrapRect = wrap.getBoundingClientRect()
  const cs = window.getComputedStyle(el)
  dropCaret.h = parseFloat(cs.lineHeight) || (parseFloat(cs.fontSize) * 1.6) || 18
  dropCaret.x = (taRect.left - wrapRect.left) + coords.left
  dropCaret.y = (taRect.top - wrapRect.top) + coords.top
  dropCaret.visible = true
}

function onContentDragEnter(e) {
  e.preventDefault()
  contentDragActive.value = true
}
function onContentDragOver(e) {
  e.preventDefault()                      // 必须：允许 drop 并由我们接管
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
  contentDragActive.value = true
  _updateDropCaret(e)
}
function onContentDragLeave(e) {
  const wrap = e.currentTarget
  if (!wrap || !e.relatedTarget || !(wrap instanceof Node) || !wrap.contains(e.relatedTarget)) {
    contentDragActive.value = false
    dropCaret.visible = false
  }
}
function onContentDrop(e) {
  e.preventDefault()                      // 接管插入，保证与自定义光标位置一致
  contentDragActive.value = false
  dropCaret.visible = false
  const el = _contentTextarea()
  const text = e.dataTransfer ? e.dataTransfer.getData('text/plain') : ''
  if (!el || !text) { _dropOffset = null; return }
  const cur = form.template_content || ''
  const pos = _dropOffset != null ? Math.min(_dropOffset, cur.length)
    : (el.selectionStart ?? cur.length)
  form.template_content = cur.slice(0, pos) + text + cur.slice(pos)
  const after = pos + text.length
  nextTick(() => {
    el.focus()
    try { el.setSelectionRange(after, after) } catch (_) { /* ignore */ }
  })
  _dropOffset = null
}

// 模板内容引用的占位符 vs 可映射固定域一致性（优化四）
const contentVarHint = computed(() => {
  const text = form.template_content || ''
  const refs = new Set()
  // 同时识别 {var} 与 {{var}}
  for (const m of text.matchAll(/\{\{?(\w+)\}?\}/g)) refs.add(m[1])
  // 子字段占位符 {域[子键]…}：仅校验根域是否已知（子键随数据变化，不做白名单）
  const subRoots = new Set()
  for (const m of text.matchAll(/\{(\w+)(?:\[[^\[\]]+\])+\}/g)) subRoots.add(m[1])
  if (!refs.size && !subRoots.size) return null
  if (props.multiProduct) {
    const known = availableVarKeys.value
    const unmapped = [...refs].filter(k => !known.has(k))
      .concat([...subRoots].filter(k => !known.has(k)))
    if (unmapped.length) {
      return {
        level: 'warn',
        text: `⚠ 占位符 ${unmapped.map(k => '{' + k + '}').join('、')} 未对应当前生效接口产出的固定域/透传字段：请从上方「可映射固定域」拖入，或检查「① 关联接口」是否勾选了产出该字段的接口，或先在「接口配置」映射对应域，避免生成 xx / 空值`,
      }
    }
    return { level: 'ok', text: '✓ 模板占位符均已对应可映射固定域' }
  }
  const varDefs = LINKED_VAR_LIST
  const known = new Set(varDefs.map(v => v.key))
  const missing = [...refs].filter(k => known.has(k) && !form.linked_vars.includes(k))
  if (missing.length) {
    return { level: 'warn', text: `⚠ 模板引用了 ${missing.join(', ')}，建议在下方「关联变量」中勾选` }
  }
  return { level: 'ok', text: `✓ 模板引用的变量（${[...refs].join('、')}）已与勾选一致` }
})

// 实时 Prompt 预览
const promptPreviewText = computed(() => {
  // 多产品模式：与 TemplateConfig「编辑模板（多产品）」预览格式完全一致
  if (props.multiProduct) {
    const selected = dynamicVarList.value.filter(v => form.linked_vars.includes(v.key))
    const parts = []
    if (selected.length) {
      parts.push('【关联变量上下文】\n' + selected.map(v => `  [${v.label}] → ${v.desc}`).join('\n'))
    }
    parts.push('【话术模板】\n' + (form.template_content || '（请填写模板内容）'))
    if (form.script_requirement) {
      parts.push('【输出要求】\n' + form.script_requirement)
    }
    return parts.join('\n\n')
  }

  // 默认模式：按 steps/script_step.py _build_prompt 顺序
  const lines = [
    '你是套餐营销推荐坐席。请基于以下用户信息和话术模板生成个性化套餐营销推荐话术。',
    `话术模板：${form.template_content || '（请填写模板内容）'}`,
  ]
  for (const key of form.linked_vars) {
    if (key === 'table') continue
    const def = LINKED_VAR_LIST.find(v => v.key === key)
    const label = def?.label || key
    const placeholder = def?.slot
      ? (providedSlotKeys.value.has(def.slot) ? `<运行时注入 ${def.slot} 域>` : `<⚠ 未配置接口提供 ${def.slot}>`)
      : '<运行时注入>'
    lines.push(`${label}：${placeholder}`)
  }
  if (form.script_requirement) lines.push(`话术要求：${form.script_requirement}`)
  return lines.join('\n')
})

// 表单关键字段变化 / 展开预览 / 弹窗打开时，防抖拉取后端真实 Prompt
watch(
  () => [
    props.modelValue,
    showPreview.value,
    form.template_content,
    form.prompt_template,
    form.script_requirement,
    form.linked_vars.join('|'),
    form.province,
    form.intent,
  ],
  () => {
    if (props.modelValue && showPreview.value) schedulePreview()
  },
  { immediate: true },
)

// ── 数据初始化 ─────────────────────────────────────────────
watch(
  () => [props.template, props.modelValue],
  ([tpl, open]) => {
    if (!open) return
    if (!tpl) return
    // 多产品模式：从 _product_ids 数组反推 product_ids_text
    const pids = Array.isArray(tpl._product_ids) ? tpl._product_ids.filter(p => p !== undefined && p !== null) : []
    const pidsText = props.multiProduct
      ? pids.join(', ')
      : ''
    Object.assign(form, {
      province:           tpl.province           ?? '',
      intent:             tpl.intent             ?? tpl.template_name ?? '',
      template_name:      tpl.template_name      ?? '',
      product_id:         tpl.product_id         ?? '',
      product_ids_text:   pidsText,
      stage:              tpl.stage              ?? '',
      scene:              tpl.scene              ?? '',
      template_content:   tpl.template_content   ?? '',
      linked_vars:        [...(tpl.linked_vars   ?? [])],
      linked_apis:        [...(tpl.linked_apis   ?? [])],
      script_requirement: tpl.script_requirement ?? DEFAULT_SCRIPT_REQ,
      prompt_template:    tpl.prompt_template    ?? '',
      status:             tpl.status             ?? 'online',
      created_by:         tpl.created_by         ?? '',
    })
    customVarInput.value = ''
    // 触发动态变量加载（multiProduct 模式）
    if (props.multiProduct && form.province && (form.intent || form.template_name)) {
      loadContextVars(form.province, form.intent || form.template_name)
    }
  },
  { immediate: true, deep: true },
)

// ── 操作函数 ──────────────────────────────────────────────
function onContentInput() {
  // 仅做识别，不强制改动 linked_vars，由用户决定（避免误增）
}

function toggleVar(key, checked) {
  if (checked && !form.linked_vars.includes(key)) form.linked_vars.push(key)
  else if (!checked) form.linked_vars = form.linked_vars.filter(x => x !== key)
}

function removeVar(v) { form.linked_vars = form.linked_vars.filter(x => x !== v) }

function addCustomVar() {
  const v = customVarInput.value.trim()
  if (v && !form.linked_vars.includes(v)) form.linked_vars.push(v)
  customVarInput.value = ''
}

function toggleApi(name, checked) {
  if (checked && !form.linked_apis.includes(name)) {
    form.linked_apis.push(name)
    // 自动勾选该接口提供的关联变量
    const api = props.availableApis.find(a => a.api_name === name)
    for (const slot of (api?.produced_slots || [])) {
      if (props.multiProduct) {
        // 多产品模式：dynamicVarList 的 key 即标准数据域名，直接匹配
        if (dynamicVarList.value.some(v => v.key === slot) && !form.linked_vars.includes(slot)) {
          form.linked_vars.push(slot)
        }
      } else {
        // 默认模式：通过 LINKED_VAR_LIST 的 slot 映射找到变量 key
        const def = LINKED_VAR_LIST.find(v => v.slot === slot)
        if (def && !form.linked_vars.includes(def.key)) form.linked_vars.push(def.key)
      }
    }
  } else if (!checked) {
    form.linked_apis = form.linked_apis.filter(x => x !== name)
  }
}

/** 根据已选接口产出的 slot 自动勾选对应的 linked_vars */
function autoSuggestVars() {
  const slots = providedSlotKeys.value
  for (const v of LINKED_VAR_LIST) {
    if (v.slot && slots.has(v.slot) && !form.linked_vars.includes(v.key)) {
      form.linked_vars.push(v.key)
    }
  }
  // diff_str 只要勾了 cur_brief 和 pkg_brief 就自动加
  if (form.linked_vars.includes('cur_brief') && form.linked_vars.includes('pkg_brief')
      && !form.linked_vars.includes('diff_str')) {
    form.linked_vars.push('diff_str')
  }
}

function onProvinceChange() {
  emit('province-change', form.province)
  form.intent = ''
}

function stageSuggest(q, cb) { cb(STAGE_OPTIONS.filter(s => !q || s.includes(q)).map(s => ({ value: s }))) }
function sceneSuggest(q, cb) { cb(SCENE_OPTIONS.filter(s => !q || s.includes(q)).map(s => ({ value: s }))) }

/**
 * 保存前校验（R4）：
 *  - 模板内容必填
 *  - 产品 ID 可留空：留空表示该意图下的「兜底话术」（product_id='' 走三级降级的兜底槽）
 *  - 产品 ID 若填写则校验格式（逗号/换行分隔，编码仅限 字母/数字/下划线/连字符）
 */
function validateBeforeSave() {
  if (!(form.template_content || '').trim()) {
    ElMessage.warning('请填写「模板内容」')
    return false
  }
  const pidsRaw = (props.multiProduct ? form.product_ids_text : form.product_id) || ''
  const pidsTrim = pidsRaw.trim()
  if (pidsTrim) {
    const tokens = pidsTrim.split(/[,，\n]+/).map(s => s.trim()).filter(Boolean)
    // 产品 ID 支持中文名称（如「扩容」「流量扩容」）；仅禁止空白与可能破坏匹配/存储的分隔符与引号括号。
    const bad = tokens.filter(t => !/^[A-Za-z0-9_\u4e00-\u9fa5·・()（）-]+$/.test(t))
    if (bad.length) {
      ElMessage.warning(`产品 ID 格式不正确：${bad.join('、')}；支持中文/字母/数字/下划线/连字符，多个用逗号或换行分隔`)
      return false
    }
  }
  return true
}

function handleSave() {
  if (!validateBeforeSave()) return
  const payload = {
    ...form,
    linked_vars: [...form.linked_vars],
    linked_apis: [...form.linked_apis],
  }
  if (props.multiProduct) {
    // 解析多产品文本（半/全角逗号、换行分隔，去空去重）
    const pids = (form.product_ids_text || '')
      .split(/[,，\n]+/)
      .map(p => p.trim())
      .filter(Boolean)
      .filter((p, i, arr) => arr.indexOf(p) === i)
    payload.product_ids = pids.length ? pids : ['']
  }
  emit('save', payload)
}
</script>

<style scoped>
:deep(.tpl-edit-dialog .el-dialog__body) { padding: 12px 24px 0; }
:deep(.tpl-edit-dialog .el-dialog__footer) { padding: 12px 24px 16px; }

.tpl-edit-form { max-height: 74vh; overflow-y: auto; padding-right: 8px; }
.tpl-edit-form::-webkit-scrollbar { width: 6px; }
.tpl-edit-form::-webkit-scrollbar-thumb { background: #ced4da; border-radius: 4px; }

.fl { font-size: 13px; font-weight: 600; color: #212529; }
.req-star { color: #c92a2a; margin-left: 2px; }
.fh { font-size: 11px; color: #868e96; margin-left: 8px; font-weight: 400; }

.req-preset-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.req-preset-label { font-size: 12px; color: #868e96; }

.content-area { width: 100%; }
.content-area :deep(textarea) {
  line-height: 1.8; font-size: 14px; font-family: monospace;
  /* 宽度随容器自适应铺满（横向拉宽、稳定不变）；高度默认约 6 行，可鼠标拖右下角纵向放大 */
  width: 100%; min-height: 150px; resize: vertical;
  /* 加粗常规光标颜色，拖拽落点更醒目 */
  caret-color: #f03e3e;
}
.content-var-hint {
  margin-top: 6px; font-size: 12px; padding: 4px 10px; border-radius: 4px;
}

/* ─── 拖拽落点可视化：拖动调色板变量到模板时的高亮 + 落点提示 ─── */
/* el-form-item__content 是 flex 容器，本 wrap 需强制占满整行，否则会被压成 textarea 固有的 ~20 列窄宽 */
.content-area-wrap { position: relative; flex: 1 1 100%; width: 100%; }
.content-area-wrap.drag-over :deep(.el-textarea__inner) {
  border-color: #4263eb;
  box-shadow: 0 0 0 3px rgba(66, 99, 235, .18);
  background: #f7faff;
}
.content-area-wrap.drag-over :deep(textarea) {
  /* 拖动时放大光标存在感 */
  caret-color: #f03e3e;
}
.drag-drop-hint {
  position: absolute; top: 6px; right: 8px; z-index: 3; pointer-events: none;
  font-size: 11px; color: #fff; background: rgba(66, 99, 235, .92);
  padding: 3px 8px; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.15);
}
/* 自定义拖拽落点光标：明显的红色竖线，闪烁跟随鼠标，精确指示插入位置 */
.drop-caret {
  position: absolute; z-index: 2; width: 2px; pointer-events: none;
  background: #f03e3e; border-radius: 1px;
  box-shadow: 0 0 3px rgba(240, 62, 62, .7);
  animation: drop-caret-blink .9s step-start infinite;
}
@keyframes drop-caret-blink {
  50% { opacity: 0; }
}

/* ─── 可映射固定域调色板（可点击 / 拖拽插入模板） ─── */
/* ── 智能填充占位符 ── */
.autofill-bar {
  display: flex; align-items: center; gap: 10px;
  margin: 4px 0 8px;
  width: 100%;
}
.autofill-hint { font-size: 12px; color: var(--muted, #909399); line-height: 1.5; }
.autofill-result { display: flex; flex-direction: column; gap: 12px; }
.af-block-title { font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 6px; }
.af-sub { font-weight: 400; font-size: 12px; color: var(--muted, #909399); margin-left: 6px; }
.af-text {
  font-size: 13px; line-height: 1.9; padding: 10px 12px;
  border-radius: 8px; white-space: pre-wrap; word-break: break-all;
}
.af-before { background: #f5f7fa; color: #606266; border: 1px solid #e4e7ed; }
.af-after  { background: #f0f9eb; color: #303133; border: 1px solid #d1edc4; }
.af-after :deep(.af-token) {
  display: inline-block; padding: 0 4px; margin: 0 1px;
  border-radius: 4px; background: #67c23a; color: #fff;
  font-family: monospace; font-size: 12px;
}
.af-after :deep(.af-token-unknown) {
  display: inline-block; padding: 0 4px; margin: 0 1px;
  border-radius: 4px; background: #e6a23c; color: #fff;
  font-family: monospace; font-size: 12px;
}
.af-vars { margin-top: 4px; }
.af-vars-label { font-size: 12px; color: var(--muted, #909399); }
.af-notes {
  margin-top: 8px; font-size: 12.5px; color: #606266;
  background: #fdf6ec; border: 1px solid #faecd8; border-radius: 6px;
  padding: 8px 10px; line-height: 1.7;
}

.dragvar-palette {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  margin-bottom: 8px; padding: 8px 10px; border: 1px dashed #c3d0e8;
  border-radius: 6px; background: #f7faff;
}
.dragvar-palette-label { font-size: 12px; font-weight: 600; color: #4263eb; margin-right: 2px; }
.dragvar-palette-tip { font-size: 11px; color: #868e96; margin-right: 4px; }
.dragvar-chip {
  display: inline-flex; align-items: center; gap: 4px; cursor: grab;
  padding: 3px 8px; border: 1px solid #bfdbfe; border-radius: 12px;
  background: #fff; font-size: 12px; color: #1c4ed8; user-select: none;
  transition: all .15s;
}
.dragvar-chip:hover { background: #eef2ff; border-color: #4263eb; }
.dragvar-chip:active { cursor: grabbing; }
.dragvar-chip code { font-size: 11px; color: #868e96; font-family: monospace; }
/* 来源角标：标注该占位符由哪个接口产出 / 计算生成 */
.chip-src {
  font-size: 10px; line-height: 1; padding: 2px 5px; border-radius: 8px;
  background: #e7f0ff; color: #3b5bdb; max-width: 130px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.chip-passthrough { border-color: #b2f2bb; color: #2b8a3e; }
.chip-passthrough:hover { background: #ebfbee; border-color: #2b8a3e; }
.chip-passthrough .chip-src { background: #e6fcf0; color: #2b8a3e; }
.chip-generated { border-color: #ffd8a8; color: #d9480f; }
.chip-generated:hover { background: #fff4e6; border-color: #e8590c; }
.chip-generated .chip-src { background: #fff0e0; color: #d9480f; }
.dragvar-empty { font-size: 12px; color: #adb5bd; }
/* 子字段展开箭头 ▾：点击弹出该域下一级子字段列表，精准插入 {域[子键]} */
.chip-caret {
  margin-left: 2px; padding: 0 3px; border-radius: 6px; cursor: pointer;
  font-size: 11px; color: #4263eb; background: #e7f0ff; line-height: 1.4;
}
.chip-caret:hover { background: #4263eb; color: #fff; }
.var-group--auto .var-group-hint { color: #2b8a3e; }
.content-var-hint.warn { color: #b45309; background: #fff3bf; }
.content-var-hint.ok { color: #2b8a3e; background: #ebfbee; }

/* ─── 接口卡片 ─── */
.api-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 8px; margin-bottom: 8px;
}
.api-item {
  display: flex; flex-direction: column; gap: 4px;
  padding: 8px 10px; border: 1px solid #dee2e6; border-radius: 6px;
  background: #fff; cursor: pointer; transition: all .15s;
  font-size: 12px;
}
.api-item:hover { border-color: #4263eb; background: #f0f4ff; }
.api-item.checked { border-color: #4263eb; background: #eef2ff; }
.api-head { display: flex; align-items: center; gap: 6px; }
.api-head input[type=checkbox] { accent-color: #4263eb; }
.api-name { font-weight: 600; font-family: monospace; color: #4263eb; }
.api-desc { font-size: 11px; color: #868e96; padding-left: 22px; }
.api-slots { display: flex; flex-wrap: wrap; gap: 4px; padding-left: 22px; align-items: center; }
.api-slots-label { font-size: 11px; color: #868e96; }
.api-slots-empty { font-size: 11px; color: #c92a2a; font-style: italic; }
.slot-tag { font-size: 11px; }
.api-hint {
  font-size: 12px; color: #b45309; background: #fff8e1;
  padding: 6px 10px; border-radius: 4px; margin-top: 4px;
}

/* ─── 关联变量网格 ─── */
.var-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 6px; margin-bottom: 10px;
}
.var-item {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 10px; border: 1px solid #dee2e6; border-radius: 6px;
  background: #fff; cursor: pointer; transition: all .15s;
  font-size: 12px; user-select: none;
}
.var-item:hover { border-color: #4263eb; background: #f0f4ff; }
.var-item.checked { border-color: #4263eb; background: #eef2ff; }
.var-item.supplied { border-left: 3px solid #2b8a3e; }
.var-item.missing { border-left: 3px solid #c92a2a; background: #fff5f5; }
.var-item input[type=checkbox] { accent-color: #4263eb; flex-shrink: 0; }
.var-name { font-weight: 600; color: #212529; flex-shrink: 0; }
.var-key { font-family: monospace; font-size: 11px; color: #868e96; }
.var-tag-wrap { margin-left: auto; }

.custom-var-row {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 4px;
}
.var-selected-label { font-size: 12px; color: #868e96; white-space: nowrap; }

/* ─── 多产品动态变量样式 ─── */
.var-item.dyn { gap: 4px; padding: 6px 8px; }
.var-item.dyn .var-name { font-size: 12px; }
.var-item.dyn.suggested { border-color: #f59f00; background: #fff8e1; }
.var-desc { font-size: 11px; color: #868e96; margin-left: 4px; }
.dyn-tag {
  display: inline-block; font-size: 10px; line-height: 16px; padding: 0 6px;
  border-radius: 8px; margin-left: auto; font-weight: 600; flex-shrink: 0;
}
.dyn-tag-api   { background: #d3e4ff; color: #1c4ed8; }
.dyn-tag-gen   { background: #d3f9d8; color: #2b8a3e; }
.dyn-tag-fixed { background: #f1f3f5; color: #495057; }
/* ─── 关联变量·高级折叠区 ─── */
.adv-vars { width: 100%; margin-bottom: 10px; border: 1px solid #e9ecef; border-radius: 6px; }
.adv-vars-toggle {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 7px 12px; cursor: pointer; user-select: none;
  font-size: 12px; font-weight: 600; color: #495057; background: #f8f9fa;
  border-radius: 6px;
}
.adv-vars-toggle:hover { background: #f1f3f5; }
.adv-vars-caret { color: #868e96; }
.adv-vars-hint { font-size: 11px; font-weight: 400; color: #adb5bd; }
.adv-vars-badge {
  margin-left: auto; font-size: 11px; font-weight: 600; color: #b45309;
  background: #fff3bf; border-radius: 8px; padding: 1px 8px;
}
.adv-vars-body { padding: 10px 12px 2px; border-top: 1px solid #e9ecef; }

.var-suggest-bar {
  width: 100%; margin-bottom: 10px; padding: 7px 12px; background: #fff8e1;
  border: 1px solid #ffe066; border-left: 3px solid #f59f00; border-radius: 6px;
  font-size: 12px; color: #b45309;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}

/* ─── 变量分组（数据域 / 生成） ─── */
.var-group { width: 100%; margin-bottom: 10px; }
.var-group-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 600; color: #495057; margin-bottom: 6px;
}
.var-group-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.dot-blue  { background: #4263eb; }
.dot-green { background: #2b8a3e; }
.var-group-hint { font-size: 11px; color: #adb5bd; font-weight: 400; }
.var-selected-row {
  width: 100%; display: flex; align-items: center; flex-wrap: wrap; gap: 4px;
  padding: 8px 10px; background: #f8f9fa; border-radius: 6px; margin-top: 2px;
}

/* ─── Prompt 预览 ─── */
.prompt-preview {
  border: 1px solid #dee2e6; border-radius: 8px; overflow: hidden;
}
.pp-toggle {
  padding: 8px 12px; font-size: 12px; font-weight: 600; color: #4263eb;
  background: #f8f9ff; display: flex; gap: 6px;
  align-items: center; justify-content: space-between; user-select: none;
}
.pp-toggle-main { cursor: pointer; flex: 1; }
.pp-toggle-main:hover { text-decoration: underline; }
.pp-toggle-right { display: flex; align-items: center; gap: 8px; }
.pp-mode-hint {
  padding: 6px 12px; font-size: 12px; color: #868e96;
  background: #fbfcff; border-top: 1px solid #e9ecef;
}
.pp-error {
  padding: 6px 12px; font-size: 12px; color: #c92a2a;
  background: #fff5f5; border-top: 1px solid #ffc9c9;
}
.pp-body {
  margin: 0; padding: 12px 14px; font-size: 12px; font-family: monospace;
  color: #495057; background: #f8f9fa; white-space: pre-wrap;
  word-break: break-all; max-height: 240px; overflow-y: auto; line-height: 1.65;
  border-top: 1px solid #dee2e6;
}
</style>

<!-- 子字段选择弹层（el-popover 内容 teleport 到 body，需非 scoped 全局样式） -->
<style>
.subfield-pop { padding: 6px !important; }
.subfield-panel { max-height: 300px; overflow-y: auto; }
.subfield-head {
  font-size: 11px; color: #868e96; padding: 2px 6px 6px;
  border-bottom: 1px dashed #e4e7ed; margin-bottom: 4px; line-height: 1.5;
}
.subfield-head-note { color: #2b8a3e; font-size: 10.5px; }
.subfield-row {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  padding: 5px 8px; border-radius: 6px; font-size: 12px; user-select: none;
}
.subfield-row:hover { background: #eef2ff; }
.subfield-row:active { cursor: grabbing; }
.subfield-name { font-weight: 600; color: #1c4ed8; flex-shrink: 0; }
.subfield-path {
  font-family: monospace; font-size: 11px; color: #adb5bd;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.subfield-sample {
  margin-left: auto; font-size: 11px; color: #2b8a3e;
  background: #ebfbee; border-radius: 8px; padding: 1px 6px; flex-shrink: 0;
}
</style>
