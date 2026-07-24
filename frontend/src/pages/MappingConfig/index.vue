<template>
  <div class="breadcrumb">
    <div class="breadcrumb-item">智能体运营</div>
    <div class="breadcrumb-item">接口配置</div>
    <div class="breadcrumb-item active"><span class="crumb-dot"></span>接口管理</div>
  </div>
  <div class="page">
    <div class="filter-bar">
      <div class="filter-group"><label>省份</label>
        <select class="filter-select" v-model="filter.province" @change="onFilterProvinceChange">
          <option value="">全部</option>
          <option v-for="o in filterProvinceOptions" :key="o.province" :value="o.province">{{ o.label }}</option>
        </select>
      </div>
      <div class="filter-group"><label>意图</label>
        <select class="filter-select" v-model="filter.intent" @change="debouncedLoad">
          <option value="">全部</option>
          <option v-for="i in filterIntents" :key="i" :value="i">{{ i }}</option>
        </select>
      </div>
      <div class="filter-group"><label>状态</label>
        <select class="filter-select" v-model="filter.enabled" @change="debouncedLoad">
          <option value="">全部</option><option value="true">已启用</option><option value="false">已禁用</option>
        </select>
      </div>
      <div class="filter-group"><label>接口名</label>
        <input class="filter-input" v-model="filter.name" placeholder="搜索接口名/描述" @input="debouncedLoad">
      </div>
      <div class="filter-actions">
        <button class="btn btn-default" @click="resetFilters">重置</button>
        <button class="btn btn-default" @click="loadInterfaces">🔄 刷新列表</button>
        <button class="btn btn-primary" @click="openCreateEntry">＋ 新建接口</button>
      </div>
    </div>

    <div class="table-card">
      <div class="table-wrap">
        <table>
          <thead><tr><th>省份</th><th>意图</th><th>接口名称</th><th>接口描述</th><th>接口状态</th><th>映射规则</th><th>创建人</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-if="!filteredItems.length" class="empty-row"><td colspan="9"><div class="empty-icon">🔗</div><div>暂无接口数据</div></td></tr>
            <tr v-for="item in pagedItems" :key="item.province+'_'+item.intent+'_'+item.api_name">
              <td>{{ item.province_name||item.province }}</td>
              <td class="td-intent">{{ item.intent||'—' }}</td>
              <td class="td-name">{{ item.api_name }}</td>
              <td>{{ item.description||'—' }}</td>
              <td>
                <label class="row-status-toggle">
                  <input type="checkbox" :checked="item.enabled" @change="toggleEnabled(item, $event.target.checked)">
                  <span class="row-slider"></span>
                  <span class="row-status-label" :class="item.enabled?'status-online':'status-offline'">{{ item.enabled?'已启用':'已禁用' }}</span>
                </label>
              </td>
              <td>
                <span v-if="item.has_extract" class="rule-tag">extract</span>
                <span v-if="item.has_transform" class="rule-tag">transform</span>
                <span v-if="item.mock_mode" class="mock-badge">Mock</span>
              </td>
              <td>{{ item.created_by||'—' }}</td>
              <td>{{ item.created_at||'—' }}</td>
              <td><div class="ops">
                <button class="btn-link" @click="viewInterface(item)">查看</button>
                <template v-if="authStore.canWrite(item.province)">
                  <span style="color:#dee2e6">|</span>
                  <button class="btn-link" @click="openEditInterface(item)">编辑</button>
                  <span style="color:#dee2e6">|</span>
                  <button class="btn-link" @click="openAutoMap(item)">智能映射</button>
                  <span style="color:#dee2e6">|</span>
                  <button class="btn-link danger" @click="openDelInterface(item)">删除</button>
                </template>
              </div></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination-bar">
        <div class="page-info">共 {{ filteredItems.length }} 条</div>
        <div style="display:flex;align-items:center;gap:12px">
          <select class="page-size-select" v-model.number="pageSize" @change="currentPage=1">
            <option :value="10">10条/页</option><option :value="20">20条/页</option><option :value="50">50条/页</option>
          </select>
          <div class="page-btns">
            <button class="page-btn" :disabled="currentPage<=1" @click="currentPage--">‹</button>
            <button v-for="p in pageNums" :key="p" class="page-btn" :class="{active:p===currentPage}" :disabled="p==='...'" @click="p!=='...'&&(currentPage=p)">{{ p }}</button>
            <button class="page-btn" :disabled="currentPage>=totalPages" @click="currentPage++">›</button>
          </div>
        </div>
      </div>
    </div>
  </div>

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

  <!-- 新建方式：人工 / 自动 -->
  <div class="modal-mask" :class="{show:showCreateMode}" @click.self="showCreateMode=false">
    <div class="modal-box modal-lg create-mode-box">
      <div class="modal-header">
        <span class="modal-title">新建接口 —— 选择创建方式</span>
        <button type="button" class="modal-close" @click="showCreateMode=false">×</button>
      </div>
      <div class="modal-body">
        <p class="create-mode-hint">请选择一种方式添加接口配置</p>
        <div class="create-mode-cards">
          <button type="button" class="create-mode-card" @click="pickManualCreate">
            <span class="create-mode-icon">✏️</span>
            <span class="create-mode-title">手动填写</span>
            <span class="create-mode-desc">逐字段配置接口参数。</span>
          </button>
          <button type="button" class="create-mode-card primary" @click="pickAutoCreate">
            <span class="create-mode-icon">📡</span>
            <span class="create-mode-title">上传文档自动解析</span>
            <span class="create-mode-desc">上传接口规范 docx，AI 自动完成配置。</span>
          </button>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-default" @click="showCreateMode=false">取消</button>
      </div>
    </div>
  </div>

  <!-- Agent 文档解析：上传 → 解析中 → 确认 -->
  <div class="modal-mask" :class="{show:showAgentDocModal}" @click.self="agentStep==='upload' && !agentParsing && closeAgentDocModal">
    <div class="modal-box modal-xl agent-doc-box">
      <div class="modal-header">
        <span class="modal-title">上传接口文档 — Agent 自动解析配置</span>
        <button type="button" class="modal-close" :disabled="agentStep==='parsing'" @click="closeAgentDocModal">×</button>
      </div>
      <div class="modal-body">
        <div class="agent-flow-steps">
          <div class="agent-flow-step" :class="{ active: agentStep === 'upload', done: agentStep !== 'upload' }">
            <span class="num">1</span><span>上传文档</span>
          </div>
          <div class="agent-flow-line" :class="{ done: agentStep !== 'upload' }"></div>
          <div class="agent-flow-step" :class="{ active: agentStep === 'parsing', done: agentStep === 'review' }">
            <span class="num">2</span><span>智能解析</span>
          </div>
          <div class="agent-flow-line" :class="{ done: agentStep === 'review' }"></div>
          <div class="agent-flow-step" :class="{ active: agentStep === 'review' }">
            <span class="num">3</span><span>确认保存</span>
          </div>
        </div>

        <template v-if="agentStep === 'upload'">
          <div class="form-row-2col">
            <div class="form-row">
              <label class="required">省份</label>
              <select class="form-control" v-model="agentDoc.province" @change="onAgentDocProvinceChange">
                <option value="">请选择</option>
                <option v-for="o in agentProvinceOptions" :key="o.province" :value="o.province">{{ o.label }}</option>
              </select>
            </div>
            <div class="form-row">
              <label class="required">意图</label>
              <select class="form-control" v-model="agentDoc.intent">
                <option value="">{{ agentDoc.province ? '请选择意图' : '请先选择省份' }}</option>
                <option v-for="i in agentDocIntents" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
          </div>
          <details class="agent-specs">
            <summary>接口文档规范要求（展开查看）</summary>
            <ul>
              <li>文档需包含：接口名称、描述、URL、请求方式、请求/响应说明及示例。</li>
              <li>请尽量使用与模板一致的结构，便于解析出 api_nodes 与映射规则。</li>
              <li>仅支持 <strong>.docx</strong> 格式。</li>
            </ul>
          </details>
          <p class="agent-template-row">
            <a :href="interfaceTemplateHref" target="_blank" rel="noopener noreferrer">没有文档？下载接口规范文档模板</a>
          </p>
          <div
            class="agent-drop-zone"
            :class="{ dragging: agentDropHighlight }"
            @click="triggerAgentFilePick"
            @dragenter.prevent="agentDropHighlight=true"
            @dragleave.prevent="onAgentDragLeave"
            @dragover.prevent="agentDropHighlight=true"
            @drop.prevent="onAgentFileDrop"
          >
            <input
              ref="agentFileInputRef"
              type="file"
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              class="sr-only"
              @change="onAgentFileInputChange"
            >
            <div class="agent-drop-icon">📁</div>
            <div>点击选择或拖拽 .docx 文件到此处</div>
            <div v-if="agentDocFile" class="agent-file-name">{{ agentDocFile.name }}</div>
          </div>
          <div class="agent-actions">
            <button
              type="button"
              class="btn btn-primary"
              :disabled="agentParsing || !agentDoc.province || !agentDoc.intent || !agentDocFile"
              @click="runDocPreview"
            >
              {{ agentParsing ? '🤖 解析中...' : '🤖 启动智能解析' }}
            </button>
          </div>
        </template>

        <div v-else-if="agentStep === 'parsing'" class="agent-parsing-panel">
          <p class="agent-parsing-agent-title">🤖 Interface Mapper Agent 执行中…</p>
          <div class="agent-progress-track">
            <div class="agent-progress-fill" :style="{ width: agentProgressPct + '%' }"></div>
          </div>
          <ul class="agent-pipeline-list">
            <li
              v-for="(step, idx) in AGENT_PIPELINE"
              :key="step.id"
              class="agent-pipeline-item"
              :class="{
                done: idx < agentActivePipelineStep,
                active: idx === agentActivePipelineStep,
                pending: idx > agentActivePipelineStep,
              }"
            >
              <span class="agent-pipeline-icon">{{ idx < agentActivePipelineStep ? '✓' : idx === agentActivePipelineStep ? '⚙️' : '⏳' }}</span>
              <div class="agent-pipeline-body">
                <div class="agent-pipeline-title">Step {{ idx + 1 }} — {{ step.title }}</div>
                <div class="agent-pipeline-desc">{{ step.desc }}</div>
              </div>
            </li>
          </ul>
          <p class="agent-parsing-footer-msg">{{ agentPipelineFooterText }}</p>
          <p class="agent-parsing-hint">请保持本窗口打开；若耗时较久多为 LLM 分析出参，请耐心等待。</p>
        </div>

        <div v-else-if="agentStep === 'review'" class="agent-review-panel">
          <div class="agent-review-done-bar">
            <span class="agent-review-done-text">✅ Agent 解析完成，请确认以下结果</span>
            <button type="button" class="btn btn-default btn-sm" :disabled="applySaving" @click="agentReuploadFromReview">重新上传</button>
          </div>

          <h3 class="agent-review-section-title">② 接口基础信息（可修改）</h3>
          <div class="form-row-2col">
            <div class="form-row">
              <label>接口名称（文档中的名称）</label>
              <input v-model="reviewDisplayName" class="form-control" placeholder="展示用名称">
            </div>
            <div class="form-row">
              <label class="required">配置键名（api_nodes 英文名）</label>
              <input v-model="reviewBasic.api_name" class="form-control" placeholder="如 marketing_recommend_api">
            </div>
          </div>
          <div class="form-row-2col">
            <div class="form-row">
              <label>接口版本</label>
              <input v-model="reviewBasic.version" class="form-control" placeholder="如 v1.0">
            </div>
            <div class="form-row">
              <label>请求方法</label>
              <select v-model="reviewBasic.method" class="form-control">
                <option>POST</option>
                <option>GET</option>
                <option>PUT</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <label>接口描述</label>
            <input v-model="reviewBasic.description" class="form-control">
          </div>
          <div class="form-row">
            <label>请求地址（URL）</label>
            <input v-model="reviewBasic.url" class="form-control" placeholder="http://...">
          </div>
          <div class="form-row">
            <label>请求头（JSON）</label>
            <textarea v-model="reviewBasic.headersStr" class="form-control code" rows="3" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"></textarea>
          </div>

          <h3 class="agent-review-section-title">③ 入参解析（入参映射）</h3>
          <p class="agent-review-section-sub">调整「匹配来源」与「取值」后，确认保存时会写回 request_template；亦可展开「高级」直接编辑 JSON。</p>
          <div class="agent-inparam-hint-bar">
            <span class="agent-hint-tag agent-hint-green">主服务入参</span>
            <span class="agent-hint-text">phone / intent / callId / province / topN 等</span>
            <span class="agent-hint-tag agent-hint-blue">extra_data</span>
            <span class="agent-hint-text">extra_data.currentMainOffer.* 等</span>
            <span class="agent-hint-tag agent-hint-warn">未匹配</span>
            <span class="agent-hint-text">需手动指定占位符</span>
          </div>
          <div v-if="reviewParamMatches.length" class="agent-param-table-wrap">
            <table class="agent-param-table">
              <thead>
                <tr><th>入参字段</th><th>匹配来源</th><th>取值（可修改）</th></tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in reviewParamMatches" :key="ri">
                  <td class="mono">{{ row.api_param }}</td>
                  <td>
                    <select
                      v-model="row.match_type"
                      class="form-control agent-param-select"
                      @change="onReviewParamMatchTypeChange(row)"
                    >
                      <option value="direct">主服务入参</option>
                      <option value="extra_data">extra_data</option>
                      <option value="unmatched">未匹配</option>
                    </select>
                  </td>
                  <td>
                    <input v-model="row.placeholder" class="form-control agent-param-value-input" type="text" :placeholder="'如 {{PHONE}} 或 {{extra_data.xx}}'">
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="agent-review-section-sub">（文档未解析出入参匹配表，请展开「高级」检查 request_template）</p>

          <h3 class="agent-review-section-title">④ 出参映射 &amp; 数据域映射结果</h3>
          <p class="agent-review-section-sub">解析完成后会根据出参示例自动展示右侧映射结果。修改左侧出参示例或右侧数据域结果后，点击「重新生成规则」，由 LLM 结合当前期望结果反推 extract/transform，并刷新右侧映射与「高级」中的 JSON。</p>
          <div class="agent-outmap-toolbar">
            <button type="button" class="btn btn-default" :disabled="outMapLoading" @click="runAgentRegenerateMappingRules">🤖 重新生成规则</button>
          </div>
          <div v-if="reviewAnalysis" class="agent-llm-green-box">[LLM 辅助生成] {{ reviewAnalysis }}</div>
          <div v-if="reviewUnitConversions.length" class="agent-unit-strip">
            <span class="agent-unit-label">单位转换规则</span>
            <span class="agent-unit-list">{{ formatUnitConversionsHint(reviewUnitConversions) }}</span>
          </div>
          <div class="agent-outmap-cols">
            <div class="form-row agent-outmap-col">
              <label>出参成功示例 JSON（与高级中 Mock / 出参示例同源）</label>
              <textarea v-model="reviewMockStr" class="form-control code agent-outmap-textarea" rows="14" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"></textarea>
            </div>
            <div class="form-row agent-outmap-col">
              <label>数据域映射结果（解析或重新生成后显示，可直接修改）</label>
              <textarea
                v-model="reviewDomainResultStr"
                class="form-control code agent-outmap-textarea"
                rows="14"
                style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"
                placeholder="解析或重新生成后，结果将显示在此处，可直接修改…"
              ></textarea>
            </div>
          </div>

          <details class="agent-json-advanced">
            <summary>高级：请求模板与出参映射（JSON 可编辑）</summary>
            <div class="form-row">
              <label>request_template</label>
              <textarea v-model="reviewRequestTemplateStr" class="form-control code" rows="6" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"></textarea>
            </div>
            <div class="form-row-2col">
              <div class="form-row">
                <label>response_extract</label>
                <textarea v-model="reviewResponseExtractStr" class="form-control code" rows="8" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"></textarea>
              </div>
              <div class="form-row">
                <label>field_transform</label>
                <textarea v-model="reviewFieldTransformStr" class="form-control code" rows="8" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"></textarea>
              </div>
            </div>
            <div class="form-row">
              <label>Mock / 出参成功示例</label>
              <textarea v-model="reviewMockStr" class="form-control code" rows="6" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"></textarea>
            </div>
          </details>
        </div>
      </div>
      <div class="modal-footer">
        <template v-if="agentStep === 'upload'">
          <button type="button" class="btn btn-default" :disabled="agentParsing" @click="closeAgentDocModal">取消</button>
        </template>
        <template v-else-if="agentStep === 'parsing'">
          <button type="button" class="btn btn-default" @click="cancelAgentParse">取消</button>
        </template>
        <template v-else-if="agentStep === 'review'">
          <button type="button" class="btn btn-default" :disabled="applySaving" @click="closeAgentDocModal">取消</button>
          <button type="button" class="btn btn-success" :disabled="applySaving" @click="confirmApplyParsed">
            {{ applySaving ? '保存中...' : '确认信息并写入 api_nodes.json' }}
          </button>
        </template>
      </div>
    </div>
  </div>

  <!-- 新建/编辑弹窗 -->
  <div class="modal-mask" :class="{show:showEdit}" @click.self="showEdit=false">
    <div class="modal-box modal-lg">
      <div class="modal-header"><span class="modal-title">{{ editApiName?'编辑接口':'新建接口' }}</span><button class="modal-close" @click="showEdit=false">×</button></div>
      <div class="modal-body">
        <div class="modal-tabs">
          <div v-for="tab in editTabs" :key="tab.key" class="modal-tab" :class="{active:activeTab===tab.key}" @click="activeTab=tab.key">{{ tab.label }}</div>
        </div>
        <div v-show="activeTab==='basic'">
          <div class="form-row-2col">
            <div class="form-row"><label class="required">省份</label>
              <select class="form-control" v-model="editForm.province" @change="onEditProvinceChange">
                <option value="">请选择</option>
                <option v-for="o in filterProvinceOptions" :key="o.province" :value="o.province">{{ o.label }}</option>
              </select>
            </div>
            <div class="form-row"><label class="required">意图</label>
              <select class="form-control" v-model="editForm.intent">
                <option value="">请选择省份</option>
                <option v-for="i in editIntents" :key="i" :value="i">{{ i }}</option>
              </select>
            </div>
          </div>
          <div class="form-row-2col">
            <div class="form-row"><label class="required">接口名称（英文，唯一）</label><input class="form-control" v-model="editForm.api_name" placeholder="如 user_package_api"></div>
            <div class="form-row"><label>接口描述</label><input class="form-control" v-model="editForm.description" placeholder="简述接口用途"></div>
          </div>
          <div class="form-row"><label>接口 URL</label><input class="form-control" v-model="editForm.url" placeholder="http://host/path"></div>
          <div class="form-row-2col">
            <div class="form-row"><label>请求方法</label>
              <select class="form-control" v-model="editForm.method"><option>POST</option><option>GET</option><option>PUT</option></select>
            </div>
          </div>
          <div class="form-row"><label>接口状态</label>
            <div class="radio-group">
              <label class="radio-label"><input type="radio" v-model="editForm.enabled" :value="true"> 启用</label>
              <label class="radio-label"><input type="radio" v-model="editForm.enabled" :value="false"> 禁用</label>
            </div>
          </div>
          <div class="form-row"><label>请求模板 (request_template JSON)</label>
            <textarea class="form-control code" v-model="editForm.request_template" rows="6" placeholder='{"phone":"{{PHONE}}","intent":"{{INTENT}}"}' style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;min-height:120px"></textarea>
          </div>
        </div>
        <div v-show="activeTab==='outparam'">
          <div class="inline-auto-map">
            <div class="inline-auto-map-hint">粘贴接口<strong>成功响应</strong> JSON 样例，点击「智能分析」自动生成映射规则并预览数据域结果。</div>
            <textarea
              v-model="editAutoMapSample"
              class="form-control code"
              rows="6"
              style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;min-height:100px"
              placeholder='{"rtnCode":"0","bean":{"mainoffer":{...}}}'
            ></textarea>
            <div class="inline-auto-map-actions">
              <button type="button" class="btn btn-primary" :disabled="editAutoMapLoading" @click="runEditAutoMap">
                {{ editAutoMapLoading ? '🔍 分析中...' : '🔍 智能分析' }}
              </button>
            </div>
            <div v-if="editAutoMapAnalysis" class="inline-auto-map-analysis">{{ editAutoMapAnalysis }}</div>
          </div>

          <!-- 数据域映射预览区 -->
          <div v-if="editDomainResultStr" class="edit-domain-section">
            <div class="edit-domain-title">
              <span>数据域映射结果</span>
              <div class="edit-domain-actions">
                <button type="button" class="btn btn-default btn-sm" :disabled="editRefineLoading" @click="runEditPreviewMapping">
                  {{ editRefineLoading ? '⏳ 执行中...' : '▶ 重新执行映射' }}
                </button>
                <button type="button" class="btn btn-default btn-sm" :disabled="editRefineLoading" @click="runEditRefineRules">
                  {{ editRefineLoading ? '⏳ 生成中...' : '🤖 重新生成规则' }}
                </button>
              </div>
            </div>
            <p class="edit-domain-hint">根据下方映射结果与期望结果的差异，修改右侧数据域后点击「重新生成规则」，LLM 将反推新的 extract/transform 并刷新结果，直到结果符合预期后保存。</p>
            <div class="edit-domain-cols">
              <div class="form-row edit-domain-col">
                <label>出参成功示例 JSON</label>
                <textarea
                  v-model="editAutoMapSample"
                  class="form-control code edit-domain-textarea"
                  rows="12"
                  style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"
                  placeholder='{"rtnCode":"0","bean":{...}}'
                ></textarea>
              </div>
              <div class="form-row edit-domain-col">
                <label>数据域映射结果（可直接修改作为期望值）</label>
                <textarea
                  v-model="editDomainResultStr"
                  class="form-control code edit-domain-textarea"
                  rows="12"
                  style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"
                  placeholder="映射结果显示在此处，可直接修改后点击「重新生成规则」…"
                ></textarea>
              </div>
            </div>
          </div>

          <details class="edit-advanced-json" :open="!editDomainResultStr">
            <summary>出参映射 JSON（response_extract / field_transform）</summary>
            <div style="font-size:13px;color:var(--muted);margin:10px 0 6px">response_extract（路径提取）</div>
            <textarea class="form-control code" v-model="editForm.response_extract" rows="8" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;min-height:140px"></textarea>
            <div style="font-size:13px;color:var(--muted);margin:12px 0 6px">field_transform（字段转换）</div>
            <textarea class="form-control code" v-model="editForm.field_transform" rows="8" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;min-height:140px"></textarea>
          </details>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-default" @click="showEdit=false">取消</button>
        <button class="btn btn-primary" @click="saveInterface">保存</button>
      </div>
    </div>
  </div>

  <!-- 查看弹窗 -->
  <div class="modal-mask" :class="{show:showView}" @click.self="showView=false">
    <div class="modal-box modal-lg">
      <div class="modal-header"><span class="modal-title">接口详情</span><button class="modal-close" @click="showView=false">×</button></div>
      <div class="modal-body" v-html="viewHtml"></div>
      <div class="modal-footer"><button class="btn btn-primary" @click="()=>{showView=false;openEditInterface(viewItem)}">编辑</button></div>
    </div>
  </div>

  <!-- 删除弹窗 -->
  <div class="modal-mask" :class="{show:showDel}" @click.self="showDel=false">
    <div class="modal-box modal-sm">
      <div class="modal-header"><span class="modal-title">确认删除</span><button class="modal-close" @click="showDel=false">×</button></div>
      <div class="modal-body" style="padding:24px 20px"><p style="font-size:14px;line-height:1.7">确认删除接口 <strong>{{ delApiName }}</strong>？<br><span style="color:var(--danger);font-size:13px">此操作不可恢复。</span></p></div>
      <div class="modal-footer"><button class="btn btn-default" @click="showDel=false">取消</button><button class="btn btn-danger" @click="confirmDelete">确认删除</button></div>
    </div>
  </div>

  <!-- 智能映射弹窗 -->
  <div class="modal-mask" :class="{show:showAutoMap}" @click.self="showAutoMap=false">
    <div class="modal-box modal-xl">
      <div class="modal-header">
        <span class="modal-title">🤖 智能自动映射 <span v-if="autoMapItem" style="font-size:13px;font-weight:400;color:var(--muted)">— {{ autoMapItem.api_name }}</span></span>
        <button class="modal-close" @click="showAutoMap=false">×</button>
      </div>
      <div class="modal-body">
        <!-- Step 1: 粘贴样例 + 分析 -->
        <div class="automap-step-bar">
          <div class="automap-hint">粘贴接口<strong>成功响应</strong> JSON 样例，点击「智能分析」自动生成映射规则并预览数据域结果。支持多轮修改直到满意后再保存。</div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="btn btn-primary" :disabled="autoMapLoading || autoMapRefineLoading" @click="runAutoMap">
              {{ autoMapLoading ? '🔍 分析中...' : '🔍 智能分析' }}
            </button>
            <template v-if="autoMapDomainResultStr">
              <button class="btn btn-default" :disabled="autoMapLoading || autoMapRefineLoading" @click="runAutoMapPreview">
                {{ autoMapRefineLoading && autoMapRefineMode==='preview' ? '⏳ 执行中...' : '▶ 重新执行映射' }}
              </button>
              <button class="btn btn-default" :disabled="autoMapLoading || autoMapRefineLoading" @click="runAutoMapRefine">
                {{ autoMapRefineLoading && autoMapRefineMode==='refine' ? '⏳ 生成中...' : '🤖 重新生成规则' }}
              </button>
            </template>
          </div>
        </div>

        <div v-if="autoMapAnalysis" class="automap-analysis-bar">{{ autoMapAnalysis }}</div>

        <!-- 主内容：左右两列 -->
        <div class="automap-cols">
          <!-- 左列：出参样例 JSON -->
          <div class="automap-col">
            <div class="automap-col-label">出参成功示例 JSON</div>
            <textarea
              v-model="autoMapSample"
              class="form-control code automap-textarea"
              rows="14"
              style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"
              placeholder='{"rtnCode":200,"bean":{"mainoffer":{...},"tags":{...}}}'
            ></textarea>
          </div>
          <!-- 右列：数据域映射结果（分析后才显示） -->
          <div class="automap-col">
            <div class="automap-col-label">数据域映射结果 <span v-if="autoMapDomainResultStr" style="font-weight:400;font-size:11px;color:var(--muted)">（可直接修改作为期望值，点击「重新生成规则」迭代）</span></div>
            <textarea
              v-if="autoMapDomainResultStr !== null"
              v-model="autoMapDomainResultStr"
              class="form-control code automap-textarea"
              rows="14"
              style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0"
              placeholder="智能分析后，数据域映射结果将显示在此处，可直接修改…"
            ></textarea>
            <div v-else class="automap-empty-hint">
              <span>粘贴出参样例后点击「智能分析」，映射结果将显示在此处</span>
            </div>
          </div>
        </div>

        <!-- extract/transform 折叠面板 -->
        <details class="edit-advanced-json" v-if="autoMapResult" :open="!autoMapDomainResultStr">
          <summary>出参映射 JSON（response_extract / field_transform）</summary>
          <div style="font-size:13px;color:var(--muted);margin:10px 0 6px">response_extract（路径提取）</div>
          <textarea class="form-control code" v-model="autoMapExtractStr" rows="6" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;min-height:100px"></textarea>
          <div style="font-size:13px;color:var(--muted);margin:12px 0 6px">field_transform（字段转换）</div>
          <textarea class="form-control code" v-model="autoMapTransformStr" rows="6" style="font-family:monospace;font-size:12px;background:#0f172a;color:#e2e8f0;min-height:100px"></textarea>
        </details>
      </div>
      <div class="modal-footer">
        <button class="btn btn-default" @click="showAutoMap=false">取消</button>
        <button v-if="autoMapResult" class="btn btn-success" :disabled="autoMapLoading||autoMapRefineLoading" @click="applyAutoMap">✅ 应用并保存</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { apiUrl, apiFetch } from '@/utils/apiUrl'
import { useAuthStore } from '@/stores/authStore'

const authStore = useAuthStore()

const interfaceTemplateHref = computed(() => apiUrl('/api/download/interface_template'))

/** 与后端 parse_docx_preview 流水线说明一致（前端分步展示进度） */
const AGENT_PIPELINE = [
  { id: 'parse_docx', title: 'parse_docx', desc: '解析 docx 文档，提取接口描述、URL、请求头、入参与出参表格结构' },
  { id: 'match_params', title: 'match_params', desc: '入参与主服务 FlowContext 占位符自动匹配（phone / extra_data.* 等）' },
  { id: 'map_output', title: 'map_output（规则引擎）', desc: '基于出参成功示例，按照预设规则生成 response_extract 与 field_transform 映射' },
  { id: 'detect_units', title: 'detect_units', desc: '扫描字段名/单位说明，注入 unit_convert 规则（MB→GB，分/角→元）' },
  { id: 'llm_map', title: 'LLM 智能辅助映射', desc: '由 LLM 分析出参结构并生成映射规则（若文档无示例则可能跳过或耗时较长）' },
]

const showCreateMode = ref(false)
const showAgentDocModal = ref(false)
const agentStep = ref('upload')
const agentActivePipelineStep = ref(0)
let agentPipelineTimer = null
let agentFetchAbort = null

const agentDoc = reactive({ province: '', intent: '' })
const agentDocIntents = ref([])
const agentDocFile = ref(null)
const agentParsing = ref(false)
const agentDropHighlight = ref(false)
const agentFileInputRef = ref(null)
const agentPreviewData = ref(null)
const applySaving = ref(false)

const agentProgressPct = computed(() => {
  if (agentStep.value !== 'parsing') return 100
  const n = AGENT_PIPELINE.length
  const base = ((agentActivePipelineStep.value + 1) / n) * 92
  return Math.min(92, Math.round(base))
})

const agentPipelineFooterText = computed(() => {
  if (agentStep.value !== 'parsing') return ''
  const s = AGENT_PIPELINE[agentActivePipelineStep.value]
  if (!s) return ''
  return `Step ${agentActivePipelineStep.value + 1} / ${s.id}：${s.desc}`
})

const reviewBasic = reactive({
  api_name: '',
  description: '',
  url: '',
  method: 'POST',
  headersStr: '{}',
  version: '',
})
const reviewDisplayName = ref('')
const reviewParamMatches = ref([])
const reviewRequestTemplateStr = ref('{}')
const reviewResponseExtractStr = ref('{}')
const reviewFieldTransformStr = ref('{}')
const reviewMockStr = ref('{}')
const reviewAnalysis = ref('')
const reviewDomainResultStr = ref('')
const reviewUnitConversions = ref([])
const outMapLoading = ref(false)

const skillsList = ref([])

/** skills 按 province+intent 多条返回，省份下拉需去重（接口筛选、上传向导、编辑表单共用） */
const filterProvinceOptions = computed(() => {
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

/** 新建/上传向导中的省份下拉：非本部用户只能选自己省份 */
const editableProvinceOptions = computed(() => {
  if (authStore.isHQ) return filterProvinceOptions.value
  return filterProvinceOptions.value.filter(o => o.province === authStore.province)
})

const agentProvinceOptions = editableProvinceOptions

const allItems = ref([])
const toasts = ref([])
const currentPage = ref(1)
const pageSize = ref(20)

const filter = reactive({ province:'', enabled:'', name:'', intent:'' })
const filterIntents = ref([])
const editTabs = [{ key:'basic', label:'接口基础信息' }, { key:'outparam', label:'出参映射' }]

/** 是否为开发环境（development 模式使用 mock 数据） */
const IS_DEV = import.meta.env.MODE === 'development'
const activeTab = ref('basic')

const showEdit = ref(false)
const editApiName = ref(null)
const editIntents = ref([])
const editForm = reactive({ province:'', intent:'', api_name:'', description:'', url:'', method:'POST', enabled:true, created_by:'admin', request_template:'', response_extract:'{}', field_transform:'{}', mock_mode:false, mock_response:'{}' })

const showView = ref(false)
const viewHtml = ref('')
const viewItem = ref(null)

const showDel = ref(false)
const delApiName = ref('')
const delItem = ref(null)

const showAutoMap = ref(false)
const autoMapItem = ref(null)
const autoMapSample = ref('')
const autoMapLoading = ref(false)
const autoMapResult = ref(null)
// 智能映射弹窗新增状态
const autoMapDomainResultStr = ref('')
const autoMapAnalysis = ref('')
const autoMapExtractStr = ref('{}')
const autoMapTransformStr = ref('{}')
const autoMapRefineLoading = ref(false)
const autoMapRefineMode = ref('') // 'preview' | 'refine'

// 权限拦截弹窗
const showPermDenied = ref(false)
const permDeniedMsg = ref('')
function showPermDeniedDialog(msg) {
  permDeniedMsg.value = msg || '您没有权限对其他省份的数据进行此操作。'
  showPermDenied.value = true
}

/** 新建/编辑弹窗「出参映射」内联智能分析 */
const editAutoMapSample = ref('')
const editAutoMapLoading = ref(false)
const editAutoMapAnalysis = ref('')
const editDomainResultStr = ref('')
const editRefineLoading = ref(false)
const editDomainProvince = ref('')
const editDomainIntent = ref('')

const filteredItems = computed(() => {
  let items = allItems.value
  if (filter.province) items = items.filter(i => i.province === filter.province)
  if (filter.intent) items = items.filter(i => i.intent === filter.intent)
  if (filter.enabled === 'true') items = items.filter(i => i.enabled)
  if (filter.enabled === 'false') items = items.filter(i => !i.enabled)
  if (filter.name) { const q = filter.name.toLowerCase(); items = items.filter(i => i.api_name.toLowerCase().includes(q) || (i.description||'').toLowerCase().includes(q)) }
  return items
})

const totalPages = computed(() => Math.ceil(filteredItems.value.length / pageSize.value) || 1)
const pagedItems = computed(() => { const s = (currentPage.value-1)*pageSize.value; return filteredItems.value.slice(s, s+pageSize.value) })
const pageNums = computed(() => {
  const pages = [], tp = totalPages.value, cp = currentPage.value
  for (let i = 1; i <= tp; i++) {
    if (tp <= 7 || i === 1 || i === tp || Math.abs(i-cp) <= 1) pages.push(i)
    else if (Math.abs(i-cp) === 2 && pages[pages.length-1] !== '...') pages.push('...')
  }
  return pages
})

let debTimer = null
function debouncedLoad() { clearTimeout(debTimer); debTimer = setTimeout(loadInterfaces, 300) }

async function loadInterfaces() {
  try {
    const res = await apiFetch('/api/interfaces')
    const json = await res.json()
    allItems.value = json.data || []
    currentPage.value = 1
  } catch(e) { showToast('加载失败: '+e.message, false) }
}

function onFilterProvinceChange() {
  filterIntents.value = [...new Set(skillsList.value.filter(s => s.province === filter.province).map(s => s.intent))]
  filter.intent = ''
  debouncedLoad()
}

function resetFilters() { Object.assign(filter, { province:'', enabled:'', name:'', intent:'' }); filterIntents.value = []; loadInterfaces() }

function onEditProvinceChange() {
  editIntents.value = [...new Set(skillsList.value.filter(s => s.province === editForm.province).map(s => s.intent))]
  editForm.intent = ''
}

function openCreateEntry() {
  showCreateMode.value = true
}

function pickManualCreate() {
  showCreateMode.value = false
  openManualCreateModal()
}

function pickAutoCreate() {
  showCreateMode.value = false
  clearAgentPipelineTimer()
  agentFetchAbort?.abort()
  agentFetchAbort = null
  agentStep.value = 'upload'
  agentActivePipelineStep.value = 0
  agentPreviewData.value = null
  agentDocFile.value = null
  agentDropHighlight.value = false
  agentParsing.value = false
  applySaving.value = false
  Object.assign(agentDoc, { province: '', intent: '' })
  agentDocIntents.value = []
  resetAgentReviewForm()
  showAgentDocModal.value = true
  requestAnimationFrame(() => {
    if (agentFileInputRef.value) agentFileInputRef.value.value = ''
  })
}

function onAgentDocProvinceChange() {
  agentDocIntents.value = [...new Set(skillsList.value.filter(s => s.province === agentDoc.province).map(s => s.intent))]
  agentDoc.intent = ''
}

function resetAgentReviewForm() {
  Object.assign(reviewBasic, {
    api_name: '',
    description: '',
    url: '',
    method: 'POST',
    headersStr: '{}',
    version: '',
  })
  reviewDisplayName.value = ''
  reviewParamMatches.value = []
  reviewRequestTemplateStr.value = '{}'
  reviewResponseExtractStr.value = '{}'
  reviewFieldTransformStr.value = '{}'
  reviewMockStr.value = '{}'
  reviewAnalysis.value = ''
  reviewDomainResultStr.value = ''
  reviewUnitConversions.value = []
}

function clearAgentPipelineTimer() {
  if (agentPipelineTimer) {
    clearInterval(agentPipelineTimer)
    agentPipelineTimer = null
  }
}

function startAgentPipelineTimer() {
  clearAgentPipelineTimer()
  agentActivePipelineStep.value = 0
  agentPipelineTimer = window.setInterval(() => {
    if (agentActivePipelineStep.value < AGENT_PIPELINE.length - 1) {
      agentActivePipelineStep.value += 1
    }
  }, 2600)
}

function setByPath(obj, path, value) {
  const keys = String(path || '').split('.').filter(Boolean)
  if (!keys.length) return
  let cur = obj
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i]
    if (cur[k] === undefined || cur[k] === null || typeof cur[k] !== 'object' || Array.isArray(cur[k])) {
      cur[k] = {}
    }
    cur = cur[k]
  }
  cur[keys[keys.length - 1]] = value
}

/** 将入参表中的 placeholder 写回 request_template JSON 字符串 */
function syncRequestTemplateFromParamRows() {
  const tpl = JSON.parse(reviewRequestTemplateStr.value || '{}')
  for (const row of reviewParamMatches.value) {
    if (row.api_param) setByPath(tpl, row.api_param, row.placeholder)
  }
  reviewRequestTemplateStr.value = JSON.stringify(tpl, null, 2)
}

function onReviewParamMatchTypeChange(row) {
  const path = row.api_param || ''
  const leaf = (path.split('.').pop() || path).toLowerCase()
  const norm = leaf.replace(/_/g, '')
  if (row.match_type === 'direct') {
    const m = {
      phone: '{{PHONE}}',
      intent: '{{INTENT}}',
      callid: '{{CALL_ID}}',
      province: '{{PROVINCE}}',
      topn: '{{TOP_N}}',
      ioid: '{{CALL_ID}}',
      taskid: '{{TASK_ID}}',
      sessionid: '{{CALL_ID}}',
    }
    row.placeholder = m[norm] || row.placeholder
  } else if (row.match_type === 'extra_data') {
    row.placeholder = '{{extra_data.' + path + '}}'
  }
}

function formatUnitConversionsHint(list) {
  if (!Array.isArray(list) || !list.length) return ''
  return list
    .map((x) => {
      if (typeof x === 'string') return x
      if (x?.target_path && x?.converter) {
        const fld = x.field || x.label || ''
        return `${fld || x.target_path} -> ${x.converter} (${x.target_path})`
      }
      const t = x?.target || x?.label || x?.field || ''
      const fn = x?.fn || x?.convert || x?.converter || x?.type || ''
      const src = x?.from || x?.path || x?.source || x?.target_path || ''
      return [t, fn, src].filter(Boolean).join(' ← ')
    })
    .join('；')
}

function extractApiError(json, res) {
  const d = json?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) {
    return d.map((x) => (x && typeof x === 'object' ? x.msg || JSON.stringify(x) : String(x))).join('; ')
  }
  if (d != null && typeof d === 'object') return JSON.stringify(d)
  return json?.message || (res && res.statusText) || '请求失败'
}

async function runAgentPreviewMapping() {
  let mockRes
  let resExt
  let fldTr
  try {
    mockRes = JSON.parse(reviewMockStr.value || '{}')
  } catch {
    showToast('出参示例 JSON 格式错误', false)
    return
  }
  try {
    resExt = JSON.parse(reviewResponseExtractStr.value || '{}')
  } catch {
    showToast('response_extract JSON 格式错误', false)
    return
  }
  try {
    fldTr = JSON.parse(reviewFieldTransformStr.value || '{}')
  } catch {
    showToast('field_transform JSON 格式错误', false)
    return
  }
  if (!agentDoc.province || !agentDoc.intent) {
    showToast('缺少省份或意图', false)
    return
  }
  outMapLoading.value = true
  try {
    const res = await apiFetch('/api/skills/preview_mapping', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        province: agentDoc.province,
        intent: agentDoc.intent,
        mock_response: mockRes,
        response_extract: resExt,
        field_transform: fldTr,
      }),
    })
    let json = {}
    try {
      json = await res.json()
    } catch {
      json = {}
    }
    const okCode = json.code === 0 || json.code === '0'
    if (res.ok && okCode && json.data) {
      const dr = json.data.domain_result
      reviewDomainResultStr.value = JSON.stringify(dr !== undefined && dr !== null ? dr : {}, null, 2)
      showToast('✅ 映射已执行', true)
    } else {
      showToast('❌ ' + extractApiError(json, res), false)
    }
  } catch (e) {
    showToast('❌ ' + (e.message || String(e)), false)
  } finally {
    outMapLoading.value = false
  }
}

async function runAgentRegenerateMappingRules() {
  let mockRes
  let userDomain
  let resExt
  let fldTr
  try {
    mockRes = JSON.parse(reviewMockStr.value.trim() || '{}')
  } catch {
    showToast('左侧出参示例 JSON 格式错误', false)
    return
  }
  if (mockRes === null || typeof mockRes !== 'object' || Array.isArray(mockRes)) {
    showToast('左侧请粘贴 JSON 对象作为出参样例', false)
    return
  }
  try {
    userDomain = JSON.parse(reviewDomainResultStr.value.trim() || '{}')
  } catch {
    showToast('右侧数据域映射结果 JSON 格式错误', false)
    return
  }
  if (userDomain === null || typeof userDomain !== 'object' || Array.isArray(userDomain)) {
    showToast('右侧数据域结果应为 JSON 对象', false)
    return
  }
  try {
    resExt = JSON.parse(reviewResponseExtractStr.value || '{}')
  } catch {
    showToast('response_extract JSON 格式错误', false)
    return
  }
  try {
    fldTr = JSON.parse(reviewFieldTransformStr.value || '{}')
  } catch {
    showToast('field_transform JSON 格式错误', false)
    return
  }
  outMapLoading.value = true
  try {
    const res = await apiFetch('/api/skills/refine_mapping_preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mock_response: mockRes,
        user_domain_result: userDomain,
        response_extract: resExt,
        field_transform: fldTr,
      }),
    })
    let json = {}
    try {
      json = await res.json()
    } catch {
      json = {}
    }
    const okCode = json.code === 0 || json.code === '0'
    if (res.ok && okCode && json.data) {
      const d = json.data
      reviewResponseExtractStr.value = JSON.stringify(d.response_extract ?? {}, null, 2)
      reviewFieldTransformStr.value = JSON.stringify(d.field_transform ?? {}, null, 2)
      if (d.analysis) reviewAnalysis.value = d.analysis
      const dr = d.domain_result
      reviewDomainResultStr.value = JSON.stringify(dr !== undefined && dr !== null ? dr : {}, null, 2)
      if (Array.isArray(d.field_transform?._unit_conversions)) {
        reviewUnitConversions.value = d.field_transform._unit_conversions
      }
      showToast('✅ 已根据期望结果重新生成规则', true)
    } else {
      showToast('❌ ' + extractApiError(json, res), false)
    }
  } catch (e) {
    showToast('❌ ' + (e.message || String(e)), false)
  } finally {
    outMapLoading.value = false
  }
}

function cancelAgentParse() {
  agentFetchAbort?.abort()
}

function agentReuploadFromReview() {
  agentStep.value = 'upload'
  agentPreviewData.value = null
  agentDocFile.value = null
  resetAgentReviewForm()
  if (agentFileInputRef.value) agentFileInputRef.value.value = ''
}

function closeAgentDocModal() {
  clearAgentPipelineTimer()
  agentFetchAbort?.abort()
  agentFetchAbort = null
  showAgentDocModal.value = false
  agentStep.value = 'upload'
  agentActivePipelineStep.value = 0
  agentPreviewData.value = null
  agentDocFile.value = null
  agentDropHighlight.value = false
  agentParsing.value = false
  applySaving.value = false
  Object.assign(agentDoc, { province: '', intent: '' })
  agentDocIntents.value = []
  resetAgentReviewForm()
  if (agentFileInputRef.value) agentFileInputRef.value.value = ''
}

function populateReviewFromPreview(d) {
  const b = d.basic_info || {}
  const rawName = (b.api_name || '').trim()
  reviewDisplayName.value = rawName
  const slug = rawName
    .replace(/\s+/g, '_')
    .replace(/[^a-zA-Z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '')
    .toLowerCase()
  reviewBasic.api_name = slug || 'parsed_api'
  reviewBasic.description = b.description || ''
  reviewBasic.url = b.url || ''
  reviewBasic.method = b.method || 'POST'
  reviewBasic.version = b.version || ''
  reviewBasic.headersStr = JSON.stringify(b.headers || {}, null, 2)
  reviewRequestTemplateStr.value = JSON.stringify(d.request_template || {}, null, 2)
  reviewResponseExtractStr.value = JSON.stringify(d.response_extract || {}, null, 2)
  reviewFieldTransformStr.value = JSON.stringify(d.field_transform || {}, null, 2)
  reviewMockStr.value = JSON.stringify(d.success_example || {}, null, 2)
  reviewAnalysis.value = d.analysis || ''
  reviewUnitConversions.value = Array.isArray(d.unit_conversions) ? d.unit_conversions : []
  const dm = d.domain_mapping_preview
  if (dm != null && typeof dm === 'object' && !Array.isArray(dm) && Object.keys(dm).length) {
    reviewDomainResultStr.value = JSON.stringify(dm, null, 2)
  } else {
    reviewDomainResultStr.value = ''
  }
  reviewParamMatches.value = Array.isArray(d.param_matches)
    ? d.param_matches.map((x) => {
        const mt = x.match_type
        const match_type = mt === 'direct' || mt === 'extra_data' || mt === 'unmatched' ? mt : 'unmatched'
        return { ...x, match_type, placeholder: x.placeholder != null ? String(x.placeholder) : '' }
      })
    : []
  nextTick(() => {
    if (!reviewDomainResultStr.value.trim()) {
      runAgentPreviewMapping()
    }
  })
}

function triggerAgentFilePick() {
  agentFileInputRef.value?.click()
}

function setAgentDocFile(file) {
  if (!file) {
    agentDocFile.value = null
    return
  }
  if (!file.name.toLowerCase().endsWith('.docx')) {
    showToast('请上传 .docx 文件', false)
    return
  }
  agentDocFile.value = file
}

function onAgentFileInputChange(e) {
  const f = e.target.files?.[0]
  setAgentDocFile(f || null)
}

function onAgentFileDrop(e) {
  agentDropHighlight.value = false
  const f = e.dataTransfer?.files?.[0]
  setAgentDocFile(f || null)
}

function onAgentDragLeave(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) agentDropHighlight.value = false
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => {
      const s = r.result
      if (typeof s !== 'string') {
        reject(new Error('读取文件失败'))
        return
      }
      const i = s.indexOf(',')
      resolve(i >= 0 ? s.slice(i + 1) : s)
    }
    r.onerror = () => reject(r.error || new Error('读取文件失败'))
    r.readAsDataURL(file)
  })
}

async function runDocPreview() {
  if (!agentDocFile.value || !agentDoc.province || !agentDoc.intent) return
  agentFetchAbort?.abort()
  agentFetchAbort = new AbortController()
  agentParsing.value = true
  agentStep.value = 'parsing'
  startAgentPipelineTimer()
  try {
    const docx_content_b64 = await fileToBase64(agentDocFile.value)
    const res = await apiFetch('/api/parse_docx_preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        province: agentDoc.province,
        intent: agentDoc.intent,
        docx_content_b64,
      }),
      signal: agentFetchAbort.signal,
    })
    let json = {}
    try {
      json = await res.json()
    } catch {
      json = {}
    }
    if (res.ok && json.code === 0 && json.data) {
      agentPreviewData.value = json.data
      populateReviewFromPreview(json.data)
      agentActivePipelineStep.value = AGENT_PIPELINE.length - 1
      agentStep.value = 'review'
    } else {
      agentStep.value = 'upload'
      const errText =
        json.message ||
        (typeof json.detail === 'string' ? json.detail : '') ||
        (Array.isArray(json.detail) ? json.detail.map((x) => x.msg || JSON.stringify(x)).join('; ') : '') ||
        res.statusText ||
        '解析失败'
      showToast('❌ ' + errText, false)
    }
  } catch (e) {
    if (e?.name === 'AbortError') {
      agentStep.value = 'upload'
      return
    }
    agentStep.value = 'upload'
    showToast('❌ ' + (e.message || String(e)), false)
  } finally {
    clearAgentPipelineTimer()
    agentParsing.value = false
    agentFetchAbort = null
  }
}

async function confirmApplyParsed() {
  if (!reviewBasic.api_name?.trim()) {
    showToast('接口名称必填', false)
    return
  }
  let reqTpl, resExt, fldTr, mockRes, headers
  try {
    if (reviewParamMatches.value.length) syncRequestTemplateFromParamRows()
    reqTpl = JSON.parse(reviewRequestTemplateStr.value || '{}')
  } catch {
    showToast('request_template JSON 格式错误', false)
    return
  }
  try {
    resExt = JSON.parse(reviewResponseExtractStr.value || '{}')
  } catch {
    showToast('response_extract JSON 格式错误', false)
    return
  }
  try {
    fldTr = JSON.parse(reviewFieldTransformStr.value || '{}')
  } catch {
    showToast('field_transform JSON 格式错误', false)
    return
  }
  try {
    mockRes = JSON.parse(reviewMockStr.value || '{}')
  } catch {
    showToast('Mock 示例 JSON 格式错误', false)
    return
  }
  try {
    headers = JSON.parse(reviewBasic.headersStr || '{}')
  } catch {
    showToast('headers JSON 格式错误', false)
    return
  }

  const se = agentPreviewData.value?.success_example
  applySaving.value = true
  try {
    // 开发环境自动开启 mock，灰度/生产环境使用真实接口
    const body = {
      province: agentDoc.province,
      intent: agentDoc.intent,
      api_name: reviewBasic.api_name.trim(),
      description: (reviewBasic.description || reviewDisplayName.value || '').trim(),
      url: reviewBasic.url,
      method: reviewBasic.method,
      headers,
      request_template: reqTpl,
      response_extract: resExt,
      field_transform: fldTr,
      mock_mode: IS_DEV,
      mock_response: mockRes,
      success_example: se && typeof se === 'object' ? se : undefined,
    }
    const res = await apiFetch('/api/interfaces/apply_parsed', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    let json = {}
    try {
      json = await res.json()
    } catch {
      json = {}
    }
    if (res.ok && json.code === 0) {
      closeAgentDocModal()
      showToast(json.message || '✅ 接口配置已写入', true)
      await loadSkills()
      await loadInterfaces()
    } else {
      const errText =
        json.message ||
        (typeof json.detail === 'string' ? json.detail : '') ||
        (Array.isArray(json.detail) ? json.detail.map((x) => x.msg || JSON.stringify(x)).join('; ') : '') ||
        res.statusText ||
        '保存失败'
      showToast('❌ ' + errText, false)
    }
  } catch (e) {
    showToast('❌ ' + e.message, false)
  } finally {
    applySaving.value = false
  }
}

function openManualCreateModal() {
  editApiName.value = null; activeTab.value = 'basic'
  const defaultProvince = authStore.isHQ ? '' : (authStore.province || '')
  Object.assign(editForm, { province: defaultProvince, intent:'', api_name:'', description:'', url:'', method:'POST', enabled:true, created_by: authStore.username || 'admin', request_template:'', response_extract:'{}', field_transform:'{}', mock_mode:false, mock_response:'{}' })
  if (defaultProvince) {
    editIntents.value = [...new Set(skillsList.value.filter(s => s.province === defaultProvince).map(s => s.intent))]
  } else {
    editIntents.value = []
  }
  editAutoMapSample.value = ''
  editAutoMapAnalysis.value = ''
  showEdit.value = true
}

async function openEditInterface(item) {
  editApiName.value = item.api_name; activeTab.value = 'basic'
  try {
    const res = await apiFetch(`/api/interfaces/${item.province}/${item.intent}/${item.api_name}`)
    const json = await res.json()
    const cfg = json.data || {}
    editIntents.value = [...new Set(skillsList.value.filter(s => s.province === item.province).map(s => s.intent))]
    Object.assign(editForm, {
      province: item.province, intent: item.intent, api_name: item.api_name,
      description: cfg._comment||cfg.description||'', url: cfg.url||'', method: cfg.method||'POST',
      enabled: cfg.enabled!==false, created_by: authStore.username || 'admin',
      request_template: JSON.stringify(cfg.request_template||{}, null, 2),
      response_extract: JSON.stringify(cfg.response_extract||{}, null, 2),
      field_transform: JSON.stringify(cfg.field_transform||{}, null, 2),
      mock_mode: cfg.mock_mode||false,
      mock_response: JSON.stringify(cfg.mock_response||{}, null, 2)
    })
  } catch(e) { showToast('加载接口详情失败', false); return }
  editAutoMapSample.value = ''
  editAutoMapAnalysis.value = ''
  editDomainResultStr.value = ''
  editDomainProvince.value = editForm.province
  editDomainIntent.value = editForm.intent
  showEdit.value = true
}

async function saveInterface() {
  if (!editForm.province || !editForm.intent || !editForm.api_name) { showToast('省份、意图、接口名称必填', false); return }
  let req_tpl = {}, res_ext = {}, fld_tr = {}, mock_res = {}
  try { req_tpl = JSON.parse(editForm.request_template||'{}') } catch { showToast('请求模板 JSON 格式错误', false); return }
  try { res_ext = JSON.parse(editForm.response_extract||'{}') } catch { showToast('response_extract JSON 格式错误', false); return }
  try { fld_tr = JSON.parse(editForm.field_transform||'{}') } catch { showToast('field_transform JSON 格式错误', false); return }
  try { mock_res = JSON.parse(editForm.mock_response||'{}') } catch { showToast('mock_response JSON 格式错误', false); return }
  // 开发环境自动开启 mock，灰度/生产环境使用真实接口
  const body = { _comment: editForm.description, url: editForm.url, method: editForm.method, enabled: editForm.enabled, created_by: editForm.created_by, request_template: req_tpl, response_extract: res_ext, field_transform: fld_tr, mock_mode: IS_DEV, mock_response: mock_res }
  try {
    const res = await apiFetch(`/api/interfaces/${editForm.province}/${editForm.intent}/${editForm.api_name}`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) })
    if (res.status === 403) {
      const json = await res.json().catch(() => ({}))
      showEdit.value = false
      showPermDeniedDialog(json.detail || '您没有权限对其他省份的接口进行此操作。')
      return
    }
    const json = await res.json()
    if (json.code === 200) { showEdit.value = false; showToast('✅ 保存成功', true); await loadInterfaces() }
    else showToast('❌ '+(json.detail||json.message||'保存失败'), false)
  } catch(e) { showToast('❌ '+e.message, false) }
}

function viewInterface(item) {
  viewItem.value = item
  const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  const statusVal = item.enabled ? '✅ 已启用' : '⚫ 已禁用'
  const mockTag = item.mock_mode ? ' <span class="mock-badge">MOCK</span>' : ''
  const rules = [
    item.has_extract ? '✅ 响应提取' : '',
    item.has_transform ? '✅ 字段转换' : '',
  ].filter(Boolean).join(' ') || '未配置'
  viewHtml.value = `
    <div class="detail-row"><span class="detail-label">省份</span><span class="detail-value">${esc(item.province_name || item.province)}</span></div>
    <div class="detail-row"><span class="detail-label">意图</span><span class="detail-value">${esc(item.intent || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">接口名称</span><span class="detail-value detail-mono">${esc(item.api_name)}</span></div>
    <div class="detail-row"><span class="detail-label">描述</span><span class="detail-value">${esc(item.description || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">URL</span><span class="detail-value code">${esc(item.url || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">方法</span><span class="detail-value">${esc(item.method || 'POST')}</span></div>
    <div class="detail-row"><span class="detail-label">状态</span><span class="detail-value">${statusVal}${mockTag}</span></div>
    <div class="detail-row"><span class="detail-label">映射规则</span><span class="detail-value">${rules}</span></div>
    <div class="detail-row"><span class="detail-label">创建人</span><span class="detail-value">${esc(item.created_by || '—')}</span></div>
    <div class="detail-row"><span class="detail-label">创建时间</span><span class="detail-value">${esc(item.created_at || '—')}</span></div>`
  showView.value = true
}

function openDelInterface(item) { delItem.value = item; delApiName.value = item.api_name; showDel.value = true }

async function confirmDelete() {
  if (!delItem.value) return
  const { province, intent, api_name } = delItem.value
  try {
    const res = await apiFetch(`/api/interfaces/${province}/${intent}/${api_name}`, { method:'DELETE' })
    if (res.status === 403) {
      const json = await res.json().catch(() => ({}))
      showDel.value = false
      showPermDeniedDialog(json.detail || '您没有权限删除其他省份的接口。')
      return
    }
    const json = await res.json()
    if (json.code === 200) { showDel.value = false; showToast('✅ 删除成功', true); await loadInterfaces() }
    else showToast('❌ '+(json.detail||json.message||'删除失败'), false)
  } catch(e) { showToast('❌ '+e.message, false) }
}

async function toggleEnabled(item, checked) {
  try {
    const res = await apiFetch(`/api/interfaces/${item.province}/${item.intent}/${item.api_name}/status`, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ enabled: checked }) })
    if (res.status === 403) {
      const json = await res.json().catch(() => ({}))
      showPermDeniedDialog(json.detail || '您没有权限修改其他省份的接口状态。')
      await loadInterfaces() // 恢复开关状态
      return
    }
    const json = await res.json()
    if (json.code === 200) { showToast('✅ 状态已更新', true); await loadInterfaces() }
    else showToast('❌ '+(json.detail||json.message||'更新失败'), false)
  } catch(e) { showToast('❌ '+e.message, false) }
}

function openAutoMap(item) {
  autoMapItem.value = item
  autoMapSample.value = ''
  autoMapResult.value = null
  autoMapDomainResultStr.value = ''
  autoMapAnalysis.value = ''
  autoMapExtractStr.value = '{}'
  autoMapTransformStr.value = '{}'
  autoMapRefineLoading.value = false
  autoMapRefineMode.value = ''
  showAutoMap.value = true
}

async function runEditAutoMap() {
  let sample
  try {
    sample = JSON.parse(editAutoMapSample.value.trim() || '{}')
  } catch {
    showToast('样例 JSON 格式错误', false)
    return
  }
  if (sample === null || typeof sample !== 'object' || Array.isArray(sample)) {
    showToast('请粘贴 JSON 对象作为响应样例', false)
    return
  }
  editAutoMapLoading.value = true
  editAutoMapAnalysis.value = ''
  editDomainResultStr.value = ''
  try {
    const res = await apiFetch('/api/interfaces/auto_map', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sample_response: sample }),
    })
    const json = await res.json()
    if (json.code === 200 && json.data) {
      const d = json.data
      editForm.response_extract = JSON.stringify(d.response_extract ?? {}, null, 2)
      editForm.field_transform = JSON.stringify(d.field_transform ?? {}, null, 2)
      editAutoMapAnalysis.value = d.analysis || ''
      showToast('✅ 已生成映射规则，正在预览数据域结果…', true)
      // 自动触发 preview_mapping，展示数据域结果
      await runEditPreviewMapping()
    } else {
      showToast('❌ ' + (json.detail || json.message || '分析失败'), false)
    }
  } catch (e) {
    showToast('❌ ' + e.message, false)
  } finally {
    editAutoMapLoading.value = false
  }
}

/** 用当前的 response_extract + field_transform 对出参样例执行映射，更新数据域结果 */
async function runEditPreviewMapping() {
  const province = editDomainProvince.value || editForm.province
  const intent = editDomainIntent.value || editForm.intent
  if (!province || !intent) {
    showToast('请先在基础信息中选择省份和意图', false)
    return
  }
  let mockRes, resExt, fldTr
  try { mockRes = JSON.parse(editAutoMapSample.value.trim() || '{}') } catch { showToast('出参样例 JSON 格式错误', false); return }
  try { resExt = JSON.parse(editForm.response_extract || '{}') } catch { showToast('response_extract JSON 格式错误', false); return }
  try { fldTr = JSON.parse(editForm.field_transform || '{}') } catch { showToast('field_transform JSON 格式错误', false); return }
  editRefineLoading.value = true
  try {
    const res = await apiFetch('/api/skills/preview_mapping', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ province, intent, mock_response: mockRes, response_extract: resExt, field_transform: fldTr }),
    })
    let json = {}
    try { json = await res.json() } catch { json = {} }
    const okCode = json.code === 0 || json.code === '0'
    if (res.ok && okCode && json.data) {
      const dr = json.data.domain_result
      editDomainResultStr.value = JSON.stringify(dr !== undefined && dr !== null ? dr : {}, null, 2)
      showToast('✅ 数据域映射结果已更新', true)
    } else {
      showToast('❌ ' + extractApiError(json, res), false)
    }
  } catch (e) {
    showToast('❌ ' + (e.message || String(e)), false)
  } finally {
    editRefineLoading.value = false
  }
}

/** 根据用户修改后的期望数据域结果，重新生成 extract/transform 规则 */
async function runEditRefineRules() {
  let mockRes, userDomain, resExt, fldTr
  try { mockRes = JSON.parse(editAutoMapSample.value.trim() || '') } catch { showToast('出参样例 JSON 格式错误', false); return }
  if (!mockRes || typeof mockRes !== 'object' || Array.isArray(mockRes)) { showToast('出参样例请粘贴 JSON 对象', false); return }
  try { userDomain = JSON.parse(editDomainResultStr.value.trim() || '{}') } catch { showToast('数据域映射结果 JSON 格式错误', false); return }
  if (!userDomain || typeof userDomain !== 'object' || Array.isArray(userDomain)) { showToast('数据域结果应为 JSON 对象', false); return }
  try { resExt = JSON.parse(editForm.response_extract || '{}') } catch { showToast('response_extract JSON 格式错误', false); return }
  try { fldTr = JSON.parse(editForm.field_transform || '{}') } catch { showToast('field_transform JSON 格式错误', false); return }
  editRefineLoading.value = true
  try {
    const res = await apiFetch('/api/skills/refine_mapping_preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mock_response: mockRes, user_domain_result: userDomain, response_extract: resExt, field_transform: fldTr }),
    })
    let json = {}
    try { json = await res.json() } catch { json = {} }
    const okCode = json.code === 0 || json.code === '0'
    if (res.ok && okCode && json.data) {
      const d = json.data
      editForm.response_extract = JSON.stringify(d.response_extract ?? {}, null, 2)
      editForm.field_transform = JSON.stringify(d.field_transform ?? {}, null, 2)
      if (d.analysis) editAutoMapAnalysis.value = d.analysis
      const dr = d.domain_result
      editDomainResultStr.value = JSON.stringify(dr !== undefined && dr !== null ? dr : {}, null, 2)
      showToast('✅ 已重新生成映射规则，请确认数据域结果后保存', true)
    } else {
      showToast('❌ ' + extractApiError(json, res), false)
    }
  } catch (e) {
    showToast('❌ ' + (e.message || String(e)), false)
  } finally {
    editRefineLoading.value = false
  }
}

async function runAutoMap() {
  let sample
  try { sample = JSON.parse(autoMapSample.value.trim()) } catch { showToast('JSON 格式错误', false); return }
  if (!sample || typeof sample !== 'object' || Array.isArray(sample)) { showToast('请粘贴 JSON 对象作为响应样例', false); return }
  autoMapLoading.value = true
  autoMapResult.value = null
  autoMapDomainResultStr.value = ''
  autoMapAnalysis.value = ''
  try {
    const res = await apiFetch('/api/interfaces/auto_map', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ sample_response: sample }) })
    const json = await res.json()
    if (json.code === 200 && json.data) {
      autoMapResult.value = json.data
      autoMapExtractStr.value = JSON.stringify(json.data.response_extract ?? {}, null, 2)
      autoMapTransformStr.value = JSON.stringify(json.data.field_transform ?? {}, null, 2)
      autoMapAnalysis.value = json.data.analysis || ''
      showToast('✅ 已生成映射规则，正在预览数据域结果…', true)
      await runAutoMapPreview()
    } else {
      showToast('❌ '+(json.detail||json.message||'分析失败'), false)
    }
  } catch(e) { showToast('❌ '+e.message, false) }
  finally { autoMapLoading.value = false }
}

async function runAutoMapPreview() {
  if (!autoMapItem.value) { showToast('缺少接口信息', false); return }
  let mockRes, resExt, fldTr
  try { mockRes = JSON.parse(autoMapSample.value.trim() || '{}') } catch { showToast('出参样例 JSON 格式错误', false); return }
  try { resExt = JSON.parse(autoMapExtractStr.value || '{}') } catch { showToast('response_extract JSON 格式错误', false); return }
  try { fldTr = JSON.parse(autoMapTransformStr.value || '{}') } catch { showToast('field_transform JSON 格式错误', false); return }
  const { province, intent } = autoMapItem.value
  autoMapRefineLoading.value = true
  autoMapRefineMode.value = 'preview'
  try {
    const res = await apiFetch('/api/skills/preview_mapping', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ province, intent, mock_response: mockRes, response_extract: resExt, field_transform: fldTr }),
    })
    let json = {}
    try { json = await res.json() } catch { json = {} }
    const okCode = json.code === 0 || json.code === '0'
    if (res.ok && okCode && json.data) {
      const dr = json.data.domain_result
      autoMapDomainResultStr.value = JSON.stringify(dr !== undefined && dr !== null ? dr : {}, null, 2)
      showToast('✅ 数据域映射结果已更新', true)
    } else {
      showToast('❌ ' + extractApiError(json, res), false)
    }
  } catch(e) { showToast('❌ '+(e.message||String(e)), false) }
  finally { autoMapRefineLoading.value = false; autoMapRefineMode.value = '' }
}

async function runAutoMapRefine() {
  if (!autoMapItem.value) { showToast('缺少接口信息', false); return }
  let mockRes, userDomain, resExt, fldTr
  try { mockRes = JSON.parse(autoMapSample.value.trim() || '') } catch { showToast('出参样例 JSON 格式错误', false); return }
  if (!mockRes || typeof mockRes !== 'object' || Array.isArray(mockRes)) { showToast('出参样例请粘贴 JSON 对象', false); return }
  try { userDomain = JSON.parse(autoMapDomainResultStr.value.trim() || '') } catch { showToast('数据域映射结果 JSON 格式错误', false); return }
  if (!userDomain || typeof userDomain !== 'object' || Array.isArray(userDomain)) { showToast('数据域结果应为 JSON 对象', false); return }
  try { resExt = JSON.parse(autoMapExtractStr.value || '{}') } catch { showToast('response_extract JSON 格式错误', false); return }
  try { fldTr = JSON.parse(autoMapTransformStr.value || '{}') } catch { showToast('field_transform JSON 格式错误', false); return }
  autoMapRefineLoading.value = true
  autoMapRefineMode.value = 'refine'
  try {
    const res = await apiFetch('/api/skills/refine_mapping_preview', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ mock_response: mockRes, user_domain_result: userDomain, response_extract: resExt, field_transform: fldTr }),
    })
    let json = {}
    try { json = await res.json() } catch { json = {} }
    const okCode = json.code === 0 || json.code === '0'
    if (res.ok && okCode && json.data) {
      const d = json.data
      autoMapExtractStr.value = JSON.stringify(d.response_extract ?? {}, null, 2)
      autoMapTransformStr.value = JSON.stringify(d.field_transform ?? {}, null, 2)
      if (d.analysis) autoMapAnalysis.value = d.analysis
      const dr = d.domain_result
      autoMapDomainResultStr.value = JSON.stringify(dr !== undefined && dr !== null ? dr : {}, null, 2)
      autoMapResult.value = { ...(autoMapResult.value||{}), response_extract: d.response_extract, field_transform: d.field_transform }
      showToast('✅ 已重新生成映射规则，请确认数据域结果后保存', true)
    } else {
      showToast('❌ ' + extractApiError(json, res), false)
    }
  } catch(e) { showToast('❌ '+(e.message||String(e)), false) }
  finally { autoMapRefineLoading.value = false; autoMapRefineMode.value = '' }
}

async function applyAutoMap() {
  if (!autoMapItem.value) return
  const { province, intent, api_name } = autoMapItem.value
  let resExt, fldTr
  try { resExt = JSON.parse(autoMapExtractStr.value || '{}') } catch { showToast('response_extract JSON 格式错误', false); return }
  try { fldTr = JSON.parse(autoMapTransformStr.value || '{}') } catch { showToast('field_transform JSON 格式错误', false); return }
  try {
    const res = await apiFetch(`/api/interfaces/${province}/${intent}/${api_name}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ response_extract: resExt, field_transform: fldTr })
    })
    const json = await res.json()
    if (json.code === 200) { showAutoMap.value = false; showToast('✅ 映射规则已应用', true); await loadInterfaces() }
    else showToast('❌ '+(json.detail||json.message||'保存失败'), false)
  } catch(e) { showToast('❌ '+e.message, false) }
}

function showToast(msg, ok) {
  const t = { msg, ok }; toasts.value.push(t)
  setTimeout(() => { toasts.value = toasts.value.filter(x => x !== t) }, 2000)
}

async function loadSkills() {
    const res = await apiFetch('/api/skills'); const json = await res.json()
  skillsList.value = json.data || []
}

onMounted(async () => { await loadSkills(); await loadInterfaces() })

onUnmounted(() => {
  clearAgentPipelineTimer()
  agentFetchAbort?.abort()
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
.filter-bar{background:var(--card);border-radius:var(--radius);padding:16px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;box-shadow:var(--shadow);margin-bottom:16px}
.filter-group{display:flex;align-items:center;gap:8px}
.filter-group label{font-size:13px;white-space:nowrap;font-weight:500}
.filter-input,.filter-select{height:34px;padding:0 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;outline:none;background:#fff;transition:.2s}
.filter-input{width:160px}.filter-select{width:130px;cursor:pointer}
.filter-actions{margin-left:auto;display:flex;gap:8px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:34px;padding:0 16px;border:none;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;transition:.2s;white-space:nowrap}
.btn-default{background:#f1f3f5;color:var(--text)}.btn-default:hover{background:#e9ecef}
.btn-primary{background:var(--primary);color:#fff}.btn-primary:hover{background:var(--primary-hover)}
.btn-success{background:#2b8a3e;color:#fff}.btn-success:hover{background:#237032}
.btn-sm{height:30px;padding:0 12px;font-size:12px}
.btn-success{background:var(--success);color:#fff}
.btn-danger{background:var(--danger);color:#fff}
.btn:disabled{opacity:.5;cursor:not-allowed}
.table-card{background:var(--card);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse}
thead{background:#f8f9fa}
th{padding:11px 14px;text-align:left;font-size:13px;font-weight:600;color:#495057;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:11px 14px;font-size:13px;border-bottom:1px solid #f1f3f5;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f8f9fa}
.td-name{font-weight:500;font-family:monospace;font-size:12px}
.td-intent{font-size:13px;color:var(--primary);font-weight:500}
.ops{display:flex;gap:2px}
.btn-link{background:transparent;color:var(--primary);font-size:13px;cursor:pointer;border:none;padding:0 4px}
.btn-link:hover{text-decoration:underline}
.btn-link.danger{color:var(--danger)}
.status-online{background:#d3f9d8;color:#2b8a3e}
.status-offline{background:#f1f3f5;color:#868e96}
.rule-tag{display:inline-block;background:var(--primary-light);color:var(--primary);font-size:11px;padding:1px 6px;border-radius:3px;font-family:monospace;margin:1px}
.mock-badge{background:#fff3bf;color:#b45309;font-size:11px;padding:2px 7px;border-radius:4px;font-weight:500}
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
.modal-box.modal-lg{width:880px}
.modal-box.modal-xl{width:min(1100px,96vw);max-width:96vw}
.modal-box.modal-sm{width:440px}
.modal-header{padding:18px 20px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.modal-title{font-size:15px;font-weight:600}
.modal-close{background:none;border:none;font-size:20px;cursor:pointer;color:var(--muted)}
.modal-body{padding:18px 20px;flex:1;overflow-y:auto}
.modal-footer{padding:12px 20px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;gap:10px;flex-shrink:0}
.modal-tabs{display:flex;border-bottom:2px solid var(--border);margin-bottom:18px}
.modal-tab{padding:8px 16px;cursor:pointer;font-size:13px;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px;transition:.2s}
.modal-tab.active{color:var(--primary);border-bottom-color:var(--primary);font-weight:600}
.inline-auto-map{margin-bottom:18px;padding:14px;background:#f8f9fa;border-radius:8px;border:1px solid var(--border)}
.inline-auto-map-hint{font-size:13px;color:var(--muted);margin-bottom:10px;line-height:1.55}
.inline-auto-map-actions{margin-top:10px}
.inline-auto-map-analysis{margin-top:12px;padding:10px 12px;font-size:13px;color:var(--warn);background:#fff8f0;border:1px solid #ffd8a8;border-radius:6px;white-space:pre-wrap}
.form-row{margin-bottom:14px}
.form-row label{display:block;font-size:13px;font-weight:500;margin-bottom:5px}
.form-row .required::after{content:' *';color:var(--danger)}
.form-control{width:100%;height:36px;padding:0 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;outline:none;transition:.2s;font-family:inherit}
.form-control:focus{border-color:var(--primary);box-shadow:0 0 0 2px rgba(59,91,219,.15)}
textarea.form-control{height:auto;padding:8px 10px;resize:vertical;line-height:1.6}
.form-row-2col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.radio-group{display:flex;gap:16px;align-items:center}
.radio-label{display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px}
.modal-body :deep(.detail-row){margin-bottom:12px;display:grid;grid-template-columns:100px 1fr;gap:8px;align-items:start}
.modal-body :deep(.detail-label){font-size:13px;font-weight:500;color:var(--muted);padding-top:2px;text-align:right}
.modal-body :deep(.detail-value){font-size:13px;color:var(--text);white-space:pre-wrap;word-break:break-word;line-height:1.6}
.modal-body :deep(.detail-value.code){font-family:monospace;font-size:12px;background:#f8f9fa;padding:8px 10px;border-radius:6px;border:1px solid var(--border);line-height:1.6}
.modal-body :deep(.detail-value.detail-mono){font-family:monospace}
.modal-body :deep(.mock-badge){background:#fff3bf;color:#b45309;font-size:11px;padding:2px 7px;border-radius:4px;font-weight:500}
.create-mode-hint{font-size:13px;color:var(--muted);margin:0 0 14px;line-height:1.5}
.create-mode-cards{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.create-mode-card{text-align:left;padding:18px;border:2px solid var(--border);border-radius:10px;background:#fff;cursor:pointer;transition:.2s;display:flex;flex-direction:column;gap:8px;font:inherit}
.create-mode-card:hover{border-color:var(--primary);box-shadow:0 4px 12px rgba(59,91,219,.12)}
.create-mode-card.primary{border-color:#c5d4fc;background:#f8faff}
.create-mode-icon{font-size:28px;line-height:1}
.create-mode-title{font-size:15px;font-weight:700;color:var(--text)}
.create-mode-desc{font-size:12px;color:var(--muted);line-height:1.45}
.agent-step{display:flex;align-items:center;gap:10px;margin-bottom:18px}
.agent-step-badge{width:26px;height:26px;border-radius:50%;background:var(--primary);color:#fff;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.agent-step-text{font-size:14px;font-weight:600;color:var(--text)}
.agent-specs{margin:14px 0;font-size:13px;border:1px solid var(--border);border-radius:8px;padding:10px 12px;background:#fafafa}
.agent-specs summary{cursor:pointer;font-weight:500;color:var(--primary);user-select:none}
.agent-specs ul{margin:10px 0 0 18px;line-height:1.6;color:var(--muted);padding:0}
.agent-template-row{margin:12px 0;font-size:13px}
.agent-template-row a{color:var(--primary)}
.agent-drop-zone{border:2px dashed var(--border);border-radius:10px;padding:28px;text-align:center;cursor:pointer;transition:.2s;background:#fafafa}
.agent-drop-zone:hover,.agent-drop-zone.dragging{border-color:var(--primary);background:#f8faff}
.agent-drop-icon{font-size:36px;margin-bottom:8px;line-height:1}
.agent-file-name{margin-top:10px;font-size:13px;color:var(--primary);font-weight:500;word-break:break-all}
.agent-actions{margin-top:16px;display:flex;justify-content:flex-end}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.agent-flow-steps{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:4px;margin-bottom:20px;padding:12px 8px;border-bottom:1px solid var(--border);background:#fafafa;border-radius:8px}
.agent-flow-step{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.agent-flow-step.active{color:var(--primary);font-weight:600}
.agent-flow-step.done{color:var(--success)}
.agent-flow-step .num{width:22px;height:22px;border-radius:50%;background:#e9ecef;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0}
.agent-flow-step.active .num{background:var(--primary);color:#fff}
.agent-flow-step.done .num{background:var(--success);color:#fff}
.agent-flow-line{flex:1;height:2px;background:#e9ecef;min-width:24px;max-width:64px}
.agent-flow-line.done{background:#96f2a9}
.agent-parsing-panel{padding:8px 0 12px}
.agent-parsing-agent-title{font-size:15px;font-weight:600;text-align:center;margin:0 0 14px;color:var(--text)}
.agent-progress-track{height:8px;background:#e9ecef;border-radius:4px;overflow:hidden;margin-bottom:18px}
.agent-progress-fill{height:100%;background:linear-gradient(90deg,var(--primary),#748ffc);border-radius:4px;transition:width .4s ease}
.agent-pipeline-list{list-style:none;margin:0;padding:0;text-align:left;max-height:min(52vh,420px);overflow-y:auto}
.agent-pipeline-item{display:flex;gap:12px;padding:12px 10px;border-radius:8px;margin-bottom:6px;border:1px solid var(--border);background:#fff;transition:.2s}
.agent-pipeline-item.active{border-color:var(--primary);background:#f8faff;box-shadow:0 0 0 1px rgba(59,91,219,.12)}
.agent-pipeline-item.done{opacity:.85;border-color:#d3f9d8;background:#f4fcf6}
.agent-pipeline-item.pending{opacity:.65}
.agent-pipeline-icon{font-size:18px;line-height:1.4;flex-shrink:0;width:26px;text-align:center}
.agent-pipeline-title{font-size:13px;font-weight:600;color:var(--text);margin-bottom:4px}
.agent-pipeline-desc{font-size:12px;color:var(--muted);line-height:1.5}
.agent-parsing-footer-msg{font-size:12px;color:var(--primary);text-align:center;margin:14px 0 6px;line-height:1.5}
.agent-parsing-hint{font-size:12px;color:var(--muted);text-align:center}
.agent-footer-muted{font-size:13px;color:var(--muted);padding:0 8px}
.agent-review-panel{padding-top:4px}
.agent-review-done-bar{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;padding:12px 14px;background:#e6fcf5;border:1px solid #96f2d7;border-radius:8px;margin-bottom:18px}
.agent-review-done-text{font-size:14px;font-weight:600;color:#2b8a3e}
.agent-review-section-title{font-size:14px;font-weight:700;margin:18px 0 12px;color:var(--text);border-left:3px solid var(--primary);padding-left:10px}
.agent-review-section-sub{font-size:12px;color:var(--muted);margin:-6px 0 12px;line-height:1.55}
.agent-param-table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:8px;margin-bottom:8px}
.agent-param-table{width:100%;border-collapse:collapse;font-size:12px}
.agent-param-table th,.agent-param-table td{padding:10px 12px;text-align:left;border-bottom:1px solid #f1f3f5}
.agent-param-table th{background:#f8f9fa;font-weight:600}
.agent-param-table .mono{font-family:monospace;font-size:11px}
.agent-match-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.agent-match-badge.mt-direct{background:#e6fcf5;color:#2b8a3e}
.agent-match-badge.mt-extra_data{background:#e7f5ff;color:#1864ab}
.agent-match-badge.mt-unmatched{background:#fff4e6;color:#d9480f}
.agent-inparam-hint-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0 0 12px;padding:10px 12px;background:#f8f9fa;border:1px solid var(--border);border-radius:8px;font-size:12px;line-height:1.5}
.agent-hint-tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.agent-hint-green{background:#d3f9d8;color:#2b8a3e}
.agent-hint-blue{background:#d0ebff;color:#1864ab}
.agent-hint-warn{background:#ffe8cc;color:#d9480f}
.agent-hint-text{color:var(--muted)}
.agent-param-select{min-width:140px;height:34px;padding:0 8px;font-size:12px}
.agent-param-value-input{height:34px;font-size:12px;font-family:monospace}
.agent-outmap-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:10px 0 14px}
.agent-llm-green-box{margin:0 0 12px;padding:12px 14px;background:#e6fcf5;border:1px solid #96f2d7;border-radius:8px;font-size:13px;color:#2b8a3e;line-height:1.55;white-space:pre-wrap}
.agent-unit-strip{margin:0 0 12px;padding:10px 12px;background:#f8f9fa;border:1px dashed var(--border);border-radius:8px;font-size:12px;color:var(--muted);line-height:1.5}
.agent-unit-label{font-weight:600;color:var(--text);margin-right:8px}
.agent-unit-list{font-family:monospace;font-size:11px;word-break:break-word}
.agent-outmap-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:8px}
@media (max-width:900px){.agent-outmap-cols{grid-template-columns:1fr}}
.agent-outmap-col{margin-bottom:0}
.agent-outmap-textarea{min-height:220px}
.agent-json-advanced{margin-top:16px;border:1px solid var(--border);border-radius:8px;padding:12px;background:#fafafa}
.agent-json-advanced summary{cursor:pointer;font-weight:600;font-size:13px;color:var(--primary)}
.agent-review-analysis-inline{margin:12px 0}
.agent-review-analysis{margin-bottom:16px;padding:12px;background:#fff8f0;border:1px solid #ffd8a8;border-radius:8px;font-size:13px;color:var(--warn);white-space:pre-wrap;line-height:1.5}
.agent-review-note{font-size:12px;color:var(--muted);margin-top:14px;line-height:1.5}
.edit-domain-section{margin:0 0 16px;border:1px solid #c5d4fc;border-radius:8px;overflow:hidden;background:#f8faff}
.edit-domain-title{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:10px 14px;background:#eef2ff;border-bottom:1px solid #c5d4fc;font-size:13px;font-weight:600;color:var(--primary)}
.edit-domain-actions{display:flex;gap:8px}
.edit-domain-hint{font-size:12px;color:var(--muted);margin:0;padding:8px 14px 0;line-height:1.55}
.edit-domain-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:12px 14px}
@media (max-width:860px){.edit-domain-cols{grid-template-columns:1fr}}
.edit-domain-col{margin-bottom:0}
.edit-domain-textarea{min-height:200px}
.edit-advanced-json{margin-top:12px;border:1px solid var(--border);border-radius:8px;padding:12px;background:#fafafa}
.edit-advanced-json summary{cursor:pointer;font-weight:600;font-size:13px;color:var(--primary);user-select:none}
/* 智能映射弹窗 */
.automap-step-bar{display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:10px;padding:12px 14px;background:#f8faff;border:1px solid #c5d4fc;border-radius:8px;margin-bottom:12px}
.automap-hint{font-size:13px;color:var(--muted);line-height:1.55;flex:1;min-width:0}
.automap-analysis-bar{margin:0 0 12px;padding:10px 14px;background:#e6fcf5;border:1px solid #96f2d7;border-radius:8px;font-size:13px;color:#2b8a3e;line-height:1.5;white-space:pre-wrap}
.automap-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:10px}
@media (max-width:860px){.automap-cols{grid-template-columns:1fr}}
.automap-col{display:flex;flex-direction:column;gap:6px}
.automap-col-label{font-size:13px;font-weight:500;color:var(--text)}
.automap-textarea{min-height:280px;flex:1}
.automap-empty-hint{display:flex;align-items:center;justify-content:center;min-height:280px;background:#0f172a;border:1px solid var(--border);border-radius:6px;color:#64748b;font-size:13px;text-align:center;padding:20px}
</style>
