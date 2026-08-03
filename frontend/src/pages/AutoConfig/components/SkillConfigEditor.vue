<template>
  <div class="skill-config-editor">
    <el-tabs v-model="activeTab" type="border-card">

      <!-- ═══════════ Tab 1：接口配置 ═══════════ -->
      <el-tab-pane label="接口配置" name="api">

        <!-- ═══ 模式 A：有 province + intent → 直连后端 /api/interfaces ═══ -->
        <div v-if="props.province && props.intent">
          <!-- 工具栏 -->
          <div class="ifc-toolbar">
            <span class="ifc-toolbar-title">接口节点（{{ ifcItems.length }} 个）</span>

            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
              <el-input v-model="ifcSearch" size="small" placeholder="搜索接口名 / 描述 / URL"
                clearable style="width:200px;" @input="ifcPage=1" />
              <el-button size="small" plain @click="loadIfcItems">
                <el-icon><Refresh /></el-icon>&nbsp;刷新
                </el-button>
              <el-button size="small" type="primary" plain @click="openIfcCreateMode">
                <el-icon><Plus /></el-icon>&nbsp;新建接口
              </el-button>
            </div>
            </div>

          <!-- 接口表格 -->
          <el-table :data="pagedIfcItems" border stripe size="small" style="width:100%;margin-top:8px;"
            :row-class-name="({row}) => !row.enabled ? 'api-row-disabled' : ''">
            <el-table-column type="index" :index="(i)=>(ifcPage-1)*IFC_PAGE_SIZE+i+1"
              width="44" align="center" label="#" />
            <el-table-column label="接口名称" width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <span style="font-weight:600;font-family:monospace;font-size:12px;color:var(--primary);">{{ row.api_name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="接口描述" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.description || '—' }}</template>
            </el-table-column>
            <el-table-column label="接口状态" width="110" align="center">
              <template #default="{ row }">
                <el-switch :model-value="row.enabled" size="small"
                  @change="(v) => toggleIfcEnabled(row, v)" />
                <span style="font-size:11px;color:var(--muted);margin-left:4px;">{{ row.enabled ? '已启用' : '已禁用' }}</span>
              </template>
            </el-table-column>
          <el-table-column label="数据来源" width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.source_type === 'direct'" size="small" type="success">直传</el-tag>
                <el-tag v-else size="small" type="info">接口查询</el-tag>
              </template>
            </el-table-column>
          <el-table-column label="映射规则" width="130">
              <template #default="{ row }">
                <el-tag v-if="row.has_extract" size="small" type="primary" style="margin:1px;">响应提取</el-tag>
                <el-tag v-if="row.has_transform" size="small" type="warning" style="margin:1px;">字段转换</el-tag>
                <el-tag v-if="row.mock_mode" size="small" style="margin:1px;background:#fff3bf;color:#b45309;">模拟数据</el-tag>
                <span v-if="row.source_type === 'direct' && !row.has_extract && !row.has_transform" style="color:var(--muted);font-size:12px;">同名域透传</span>
                <span v-else-if="!row.has_extract && !row.has_transform && !row.mock_mode" style="color:var(--muted);font-size:12px;">未配置</span>
              </template>
            </el-table-column>
            <el-table-column label="创建人" width="100" show-overflow-tooltip>

              <template #default="{ row }">{{ row.created_by || '—' }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="136" show-overflow-tooltip />
            <el-table-column label="操作" width="200" align="center" fixed="right">
              <template #default="{ row }">
                <button class="ifc-btn-link" @click="openMapResult(row)">映射结果</button>
                <span class="ifc-sep">|</span>
                <button class="ifc-btn-link" @click="openIfcEdit(row)">编辑</button>
                <span class="ifc-sep">|</span>
                <button class="ifc-btn-link" @click="openIfcAutoMap(row)">智能映射</button>
                <span class="ifc-sep">|</span>
                <button class="ifc-btn-link danger" @click="openIfcDel(row)">删除</button>
              </template>
            </el-table-column>
          </el-table>

          <div v-if="!filteredIfcItems.length" class="empty-tip">{{ ifcSearch ? '无匹配结果' : '暂无接口节点' }}</div>
          <div v-if="filteredIfcItems.length > IFC_PAGE_SIZE" class="tpl-pagination">
            <el-pagination v-model:current-page="ifcPage" :page-size="IFC_PAGE_SIZE"
              :total="filteredIfcItems.length" layout="prev,pager,next,jumper,total" small />
            </div>
            </div>

        <!-- ═══ 模式 B：无 province/intent → 简易节点编辑器（Import 页面使用）═══ -->
        <div v-else>

        <!-- 接口节点工具栏 -->
        <div class="tpl-toolbar" style="margin-bottom:8px;">
          <span class="tpl-toolbar-title">
            接口节点（{{ apiNodeList.length }} 个）
          </span>
          <div style="display:flex;gap:8px;align-items:center;">
            <el-input
              v-model="apiSearch"
              size="small"
              placeholder="搜索节点名 / URL"
              clearable
              style="width:200px;"
              @input="apiPage = 1"
            />
            <el-button size="small" type="primary" plain @click="openIfcCreateMode">
              <el-icon><Plus /></el-icon>&nbsp;新建接口
            </el-button>
        </div>
        </div>

        <!-- 接口节点表格 -->
        <el-table
          :data="pagedApiNodes"
          border
          stripe
          size="small"
          style="width:100%;"
          :row-class-name="({row}) => !row.enabled ? 'api-row-disabled' : ''"
        >
          <el-table-column type="index" :index="(i) => (apiPage-1)*API_PAGE_SIZE + i + 1"
            width="48" align="center" label="#" />
          <el-table-column label="节点名" width="130" show-overflow-tooltip>
            <template #default="{ row }">
              <span style="font-weight:600;color:var(--primary);">{{ row._key }}</span>
            </template>
          </el-table-column>
          <el-table-column label="接口地址" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.source_type === 'direct'" size="small" type="success">直传 extra_info</el-tag>
              <code v-else style="font-size:11px;">{{ row.url || '（待填写）' }}</code>
            </template>
          </el-table-column>
          <el-table-column label="方法" width="70" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.method === 'POST' ? 'primary' : 'success'">
                {{ row.method }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="超时(s)" prop="timeout" width="72" align="center" />
          <el-table-column label="提取字段" min-width="130">
            <template #default="{ row }">
              <span style="font-size:11px;color:var(--muted);">
                {{ Object.keys(row.response_extract || {}).join(' / ') || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.enabled ? 'success' : 'info'" style="margin-right:4px;">
                {{ row.enabled ? '启用' : '禁用' }}
              </el-tag>
              <el-tag size="small" type="warning" v-if="row.mock_mode">模拟数据</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" align="center" fixed="right">
            <template #default="{ row, $index }">
              <el-button size="small" link @click="openLocalIfcEdit(row)">编辑</el-button>
              <el-button size="small" link type="danger"
                @click="removeNode((apiPage-1)*API_PAGE_SIZE + $index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="!filteredApiNodes.length" class="empty-tip">
          {{ apiSearch ? '无匹配结果' : '暂无接口节点，可点击右上角「新建接口」新增' }}
        </div>

        <!-- 分页 -->
        <div v-if="filteredApiNodes.length > API_PAGE_SIZE" class="tpl-pagination">
          <el-pagination
            v-model:current-page="apiPage"
            :page-size="API_PAGE_SIZE"
            :total="filteredApiNodes.length"
            layout="prev, pager, next, jumper, total"
            small
          />
        </div>


        </div><!-- end v-else 模式B -->
      </el-tab-pane>

      <!-- ═══ 模式 A 弹窗（在 el-tab-pane 外，作为 el-tabs 子级）═══ -->

      <!-- 映射结果弹窗（单接口标准数据关联：只读展示每个标准域的来源/字段 + 一键改映射）-->
      <el-dialog v-model="mapResultVisible" :title="`映射结果：${mapResultApiName}`"
        width="720px" destroy-on-close class="mapres-dialog">
        <div v-if="mapResultApi">
          <div class="dl-hint" style="margin-top:0;">
            该接口经「出参映射」规则后，会被写入下面 7 大标准数据域；标注「直传 extra_info」的节点不调外部接口，
            由调用方入参 extra_info 经同样规则写入标准域。
            <a @click="openStdDomainsHelp">7 大标准域说明 ›</a>
          </div>
          <div class="mapres-head">
            <el-tag v-if="mapResultApi.source_type === 'direct'" size="small" type="success">直传 extra_info</el-tag>
            <span v-if="mapResultApi.description" class="mapres-desc">{{ mapResultApi.description }}</span>
            <span class="mapres-cov">已填充 {{ getSimulatedSlots(mapResultApiName).filledCount }}/7 域</span>
          </div>

          <!-- 已映射的标准域 -->
          <div class="dl-section">
            <div v-if="filledSlotsOf(mapResultApiName).length" class="dl-domain-grid">
              <div v-for="slot in filledSlotsOf(mapResultApiName)" :key="'mf-'+slot.key" class="dl-domain-cell filled">
                <div class="dl-domain-head">
                  <span class="dl-domain-label">{{ slot.label }}</span>
                  <span v-if="getSimulatedSlots(mapResultApiName).map[slot.key]?.dataMissing"
                    class="dl-domain-warn-mark" title="规则已配置但 mock 数据缺失">⚠</span>
                  <span class="dl-domain-rule">
                    {{ getSimulatedSlots(mapResultApiName).map[slot.key].ruleLabel }}<template v-if="getSimulatedSlots(mapResultApiName).map[slot.key].fields.length">
                      · {{ getSimulatedSlots(mapResultApiName).map[slot.key].fields.length }} 个字段
                    </template>
                  </span>
                </div>
                <div v-if="getSimulatedSlots(mapResultApiName).map[slot.key].fields.length" class="dl-domain-fields">
                  <span v-for="f in getSimulatedSlots(mapResultApiName).map[slot.key].fields.slice(0, 5)" :key="f.name"
                    class="dl-field-text"
                    :class="{ excluded: getSimulatedSlots(mapResultApiName).map[slot.key].fieldsType === 'exclude' }">{{ f.name }}<span v-if="f.unit" class="dl-unit-mark" title="单位换算">⚖</span></span>
                  <span v-if="getSimulatedSlots(mapResultApiName).map[slot.key].fields.length > 5" class="dl-fields-more">+{{ getSimulatedSlots(mapResultApiName).map[slot.key].fields.length - 5 }}</span>
                </div>
                <details class="dl-preview-fold">
                  <summary>查看实际数据（{{ getSimulatedSlots(mapResultApiName).map[slot.key].previewSummary }}）</summary>
                  <pre class="dl-preview-pre">{{ getSimulatedSlots(mapResultApiName).map[slot.key].previewText }}</pre>
                </details>
              </div>
            </div>
            <div v-else class="dl-section-empty">该接口尚未映射任何标准域</div>
          </div>

          <!-- 未映射的标准域 -->
          <div v-if="emptySlotsOf(mapResultApiName).length" class="dl-section dl-section-muted">
            <div class="dl-empty-label">未映射：</div>
            <div class="dl-empty-row">
              <span v-for="slot in emptySlotsOf(mapResultApiName)" :key="'me-'+slot.key" class="dl-empty-chip">{{ slot.label }}</span>
            </div>
          </div>
        </div>
        <div v-else class="empty-tip">正在加载接口映射详情…</div>
        <template #footer>
          <el-button @click="mapResultVisible=false">关闭</el-button>
          <el-button type="primary" @click="editMappingFromMapResult">编辑出参映射</el-button>
        </template>
      </el-dialog>

      <!-- 新建/编辑弹窗 -->
      <el-dialog v-model="ifcEditVisible"
        :title="ifcEditIsNew ? '新建接口' : `编辑接口：${ifcEditForm.api_name}`"
        width="900px" :close-on-click-modal="false" destroy-on-close>
        <div class="tpl-dialog-body">
          <!-- 顶部步骤导航（朴素三段）-->
          <div class="ifc-steps">
            <button type="button" class="ifc-step" :class="{cur:ifcEditTab==='basic', done:isIfcStepDone('basic')}" @click="ifcEditTab='basic'">
              <span class="ifc-step-no">①</span> {{ isDirectMode ? '基本信息' : '配置请求' }}
            </button>
            <!-- 直传模式：样例已并入第③步，隐藏冗余的第②步；接口模式保留（承载「开启模拟」开关）-->
            <template v-if="!isDirectMode">
              <span class="ifc-step-line"></span>
              <button type="button" class="ifc-step" :class="{cur:ifcEditTab==='mock', done:isIfcStepDone('mock')}" @click="ifcEditTab='mock'">
                <span class="ifc-step-no">②</span> {{ ifcStepMockLabel }}
              </button>
            </template>
            <span class="ifc-step-line"></span>
            <button type="button" class="ifc-step" :class="{cur:ifcEditTab==='outparam', done:isIfcStepDone('outparam')}" @click="ifcEditTab='outparam'">
              <span class="ifc-step-no">{{ isDirectMode ? '②' : '③' }}</span> {{ ifcStepOutLabel }}
            </button>
          </div>

          <!-- 优化1：产出标准域已移至「智能分析」结果提示中（见 outparam 步骤），不再于顶部常驻。 -->
          <div v-show="ifcEditTab === 'basic'">
            <!-- 数据来源模式切换 -->
            <el-form-item label="数据来源" label-width="90px">
              <el-radio-group v-model="ifcEditForm.source_type">
                <el-radio-button value="direct">透传模式（extra_info）· 推荐</el-radio-button>
                <el-radio-button value="api">接口查询模式</el-radio-button>
              </el-radio-group>
            </el-form-item>

            <div v-if="!isDirectMode" class="ifc-hint">
              服务内部调外部数据接口获取数据。定义<b>调谁、怎么调、传什么</b>。请求模板可用占位符
              <code>&#123;&#123;PHONE&#125;&#125;</code>
              <code>&#123;&#123;INTENT&#125;&#125;</code>
              <code>&#123;&#123;PROVINCE&#125;&#125;</code>
              <code>&#123;&#123;extra_data.xxx&#125;&#125;</code>，主服务运行时自动注入。
            </div>
            <div v-else class="ifc-hint">
              <b>不调外部接口</b>：调用方（CTI/坐席系统）在请求的 <code>extra_info</code> 字段直接传入用户/产品信息。
            </div>

            <!-- 直传子模式：智能映射到标准域 / 直接透传字段 -->
            <el-form-item v-if="isDirectMode" label="映射方式" label-width="90px">
              <el-radio-group v-model="ifcEditForm.direct_mode">
                <el-radio-button value="passthrough">直接透传字段 · 推荐</el-radio-button>
                <el-radio-button value="mapping">智能映射到标准域</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <div v-if="isPassthrough" class="ifc-hint" style="margin-top:4px;">
              <b>直接透传</b>：<code>extra_info</code> 的入参字段<b>按原字段名</b>直接作为话术上下文，
              话术模板里用 <code>&#123;字段名&#125;</code> 即可引用（无需映射到 7 大标准域）。
              在第② <a class="ifc-link" @click="ifcEditTab='outparam'">透传字段</a> 粘贴样例并勾选要暴露的字段。
            </div>
            <div v-else-if="isDirectMode" class="ifc-hint" style="margin-top:4px;">
              <b>智能映射</b>：把 <code>extra_info</code> 按映射规则写入 <b>7 大标准域</b>。
              若不配置映射规则，顶层与标准域<b>同名</b>的 key（如 <code>current_package</code>）会自动透传。
            </div>


            <el-row :gutter="14">
              <el-col :span="12">
                <el-form-item :label="isDirectMode ? '节点名称' : '接口名称'" label-width="90px" required>
                  <el-input v-model="ifcEditForm.api_name" :disabled="!ifcEditIsNew"
                    :placeholder="isDirectMode ? '如 direct_extra_info' : '如 marketing_recommend_api'" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item :label="isDirectMode ? '节点描述' : '接口描述'" label-width="90px">
                  <el-input v-model="ifcEditForm.description"
                    :placeholder="isDirectMode ? '简述直传数据用途' : '简述接口用途'" />
                </el-form-item>
              </el-col>
            </el-row>
            <template v-if="!isDirectMode">
              <el-form-item label="接口 URL" label-width="90px">
                <el-input v-model="ifcEditForm.url" placeholder="http://host/path" />
              </el-form-item>
              <el-row :gutter="14">
                <el-col :span="8">
                  <el-form-item label="请求方法" label-width="90px">
                    <el-select v-model="ifcEditForm.method" style="width:100%">
                      <el-option label="POST" value="POST" />
                      <el-option label="GET" value="GET" />
                      <el-option label="PUT" value="PUT" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item label="接口状态" label-width="90px">
                    <el-radio-group v-model="ifcEditForm.enabled">
                      <el-radio :value="true">启用</el-radio>
                      <el-radio :value="false">禁用</el-radio>
                    </el-radio-group>
                  </el-form-item>
                </el-col>
              </el-row>
              <el-form-item label="请求模板" label-width="90px">
                <el-input v-model="ifcEditForm.request_template" type="textarea"
                  :autosize="{ minRows: 5, maxRows: 14 }" spellcheck="false"
                  placeholder='{"phone":"{{PHONE}}","intent":"{{INTENT}}"}' class="code-textarea" />
              </el-form-item>
              <el-form-item label="请求头" label-width="90px">
                <div style="width:100%;">
                  <div v-for="(h, i) in ifcEditForm.headers_pairs" :key="i"
                    style="display:flex;gap:8px;margin-bottom:8px;align-items:center;">
                    <el-input v-model="h.k" placeholder="Header 名，如 x-Channel-ID" style="flex:0 0 42%;" />
                    <el-input v-model="h.v" placeholder="Header 值，如 ngbusi" style="flex:1;" />
                    <el-button size="small" text type="danger" @click="removeHeaderRow(i)">删除</el-button>
                  </div>
                  <el-button size="small" type="primary" plain @click="addHeaderRow">+ 添加请求头</el-button>
                  <div class="ifc-hint" style="margin-top:6px;">
                    运行时在默认 <code>Content-Type</code> / <code>Accept</code> 之上叠加这些头（同名以此处为准）。
                    如省侧接口要求渠道标识，例如北京查询接口需带 <code>x-Channel-ID: ngbusi</code>。
                  </div>
                </div>
              </el-form-item>
            </template>
            <template v-else>
              <el-form-item label="节点状态" label-width="90px">
                <el-radio-group v-model="ifcEditForm.enabled">
                  <el-radio :value="true">启用</el-radio>
                  <el-radio :value="false">禁用</el-radio>
                </el-radio-group>
              </el-form-item>
              <div class="ifc-hint" style="margin-top:4px;">
                下一步：在 <a class="ifc-link" @click="ifcEditTab='outparam'">② {{ ifcStepOutLabel }}</a>
                粘贴一份调用方将传入的 <code>extra_info</code> JSON 样例，并配置映射到 7 大标准域。
              </div>
            </template>
          </div>
          <div v-show="ifcEditTab === 'outparam'">
            <div v-if="!isPassthrough" class="om-hint om-hint-lead">
              <span class="om-hint-step">第 1 步</span>
              粘贴一份{{ isDirectMode ? ' extra_info 入参' : '接口成功响应' }} JSON 样例
              <span class="om-hint-arrow">→</span>
              <span class="om-hint-step">第 2 步</span>
              点击「智能分析」，系统自动生成映射规则并把数据写入 <b>7 大标准域</b>
              <span class="om-hint-arrow">→</span>
              <span class="om-hint-step">第 3 步</span>
              核对/微调映射，点「保存」。
            </div>
            <div v-else class="om-hint om-hint-lead">
              <span class="om-hint-step">第 1 步</span>
              粘贴一份 <code>extra_info</code> 入参 JSON 样例
              <span class="om-hint-arrow">→</span>
              <span class="om-hint-step">第 2 步</span>
              勾选要作为话术上下文的<b>透传字段</b>（不勾则默认全部顶层字段）
              <span class="om-hint-arrow">→</span>
              <span class="om-hint-step">第 3 步</span>
              点「保存」。话术模板里用 <code>&#123;字段名&#125;</code> 引用。
            </div>

            <!-- 三段式：① 输入 ② 提取 ③ 转换 -->
            <div class="om2">
              <!-- ① 输入 -->
              <section class="om2-sec">
                <header class="om2-sec-head">
                  <span class="om2-no">①</span>
                  <span class="om2-title">{{ isDirectMode ? 'extra_info 样例 JSON' : '响应样例 JSON' }}</span>
                  <span v-if="!isPassthrough" class="om2-aux">
                    <el-button size="small" type="primary" :loading="ifcAutoMapLoading" @click="runIfcAutoMap">智能分析</el-button>
                  </span>
                </header>
                <!-- 与「模拟数据」共用同一份样例（单一来源，无需再从第②步复制） -->
                <el-input v-model="ifcEditForm.mock_response" type="textarea"
                  :autosize="{ minRows: 3, maxRows: 8 }" spellcheck="false"
                  placeholder='{"bean":{"mainoffer":{...},"tags":{...}}}' class="code-textarea" />
                <div v-if="ifcAutoMapAnalysis && !isPassthrough" class="om2-msg">{{ ifcAutoMapAnalysis }}</div>

                <!-- 优化1：智能分析结果提示 —— 产出标准域覆盖情况（分析后 / 已有映射时展示）-->
                <div v-if="showMappingResult && !isPassthrough" class="ifc-analysis-result">
                  <div class="iar-head">
                    <span class="iar-badge">分析结果</span>
                    <span class="iar-title">产出标准域</span>
                    <span class="iar-count" :class="{ full: ifcEditProducedSlots.size >= 7 }">
                      已覆盖 {{ ifcEditProducedSlots.size }}/7
                    </span>
                  </div>
                  <div class="iar-chips">
                    <span v-for="slot in STANDARD_SLOT_LIST" :key="slot.key"
                      class="ipp-chip" :class="{ on: ifcEditProducedSlots.has(slot.key) }">
                      <span class="ipp-mark">{{ ifcEditProducedSlots.has(slot.key) ? '✓' : '○' }}</span>{{ slot.label }}
                    </span>
                  </div>
                </div>
                <div v-else-if="!isPassthrough" class="ifc-analysis-empty">
                  <span class="iae-icon">🔍</span>
                  填好上方样例后点击「智能分析」，系统将自动生成映射规则，并在此展示<b>产出标准域</b>与
                  <b>response_extract / field_transform / 真实数据流</b>等映射详情。
                </div>
              </section>

              <!-- 透传字段选择（isPassthrough）：从样例解析顶层字段，勾选要暴露的入参字段 -->
              <section v-if="isPassthrough" class="om2-sec">
                <header class="om2-sec-head">
                  <span class="om2-no">②</span>
                  <span class="om2-title">透传字段</span>
                  <span class="om2-aux om2-aux-text">
                    {{ ifcEditForm.passthrough_fields.length
                        ? `已选 ${ifcEditForm.passthrough_fields.length} 个`
                        : `全部字段（${passthroughSampleFields.length}）` }}
                  </span>
                </header>
                <div class="om2-tip">
                  勾选后仅暴露选中字段作为话术上下文；<b>不勾选任何字段则默认暴露全部顶层字段</b>。
                  话术模板里用 <code>&#123;字段名&#125;</code> 直接引用（如 <code>&#123;recommend_actual_price&#125;</code>）。
                  与标准域同名的字段（如 <code>current_package</code>）仍会自动写入对应标准域。
                </div>
                <div v-if="passthroughSampleFields.length" class="pt-field-grid">
                  <label v-for="f in passthroughSampleFields" :key="f.key" class="pt-field-item"
                    :class="{ checked: ifcEditForm.passthrough_fields.includes(f.key) }">
                    <input type="checkbox" :value="f.key" v-model="ifcEditForm.passthrough_fields" />
                    <code class="pt-field-key">{{ f.key }}</code>
                    <span v-if="f.isStd" class="pt-field-tag">标准域</span>
                    <span class="pt-field-preview">{{ f.preview }}</span>
                  </label>
                </div>
                <div v-else class="ifc-analysis-empty">
                  <span class="iae-icon">🔍</span>
                  在上方 ① 粘贴 <code>extra_info</code> JSON 样例后，这里会列出可勾选的顶层字段。
                </div>
              </section>

              <!-- ② response_extract（仅开发环境 + 已智能分析/已有映射时显示）-->
              <section v-if="isDev && showMappingResult && !isPassthrough" class="om2-sec">
                <header class="om2-sec-head">
                  <span class="om2-no">②</span>
                  <span class="om2-title">响应提取 <code>response_extract</code></span>
                  <span class="om2-aux om2-aux-text">
                    取数集 {{ extractKeysPreview.length }} 个
                  </span>
                </header>
                <div class="om2-tip">
                  从响应里按 JSON 路径整块取数据。<b>只写直取到标准域的项</b>——键名就是 7 大域之一
                  （如 <code>current_package</code>），直接写入、无需转换。
                  需要拆分/筛选的混合对象<b>不必在这里预先提取</b>：第③步 <code>from</code> 直接填响应路径即可。
                </div>
                <el-input v-model="ifcEditForm.response_extract" type="textarea"
                  :autosize="{ minRows: 4, maxRows: 10 }" spellcheck="false" class="code-textarea" />
                <!-- 优化1：直取到标准域 -->
                <div v-if="extractDirectKeys.length" class="om2-chips">
                  <span class="om2-chips-label">直取到标准域：</span>
                  <code v-for="k in extractDirectKeys" :key="k" class="om2-chip om2-chip-ok">{{ k }}</code>
                  <span class="om2-chips-note">直接写入标准域，无需在③配置</span>
                </div>
                <!-- 优化1：中间数据集 -->
                <div v-if="extractIntermediateKeys.length" class="om2-chips">
                  <span class="om2-chips-label">中间数据集：</span>
                  <code v-for="k in extractIntermediateKeys" :key="k"
                    class="om2-chip" :class="{ 'om2-chip-bad': deadIntermediateKeys.includes(k) }">{{ k }}</code>
                  <span class="om2-chips-note">需在③用 <code>from</code> 引用后写入标准域</span>
                </div>
                <!-- 优化2：死中间集告警 -->
                <div v-if="deadIntermediateKeys.length" class="om2-warn">
                  ⚠ 未被使用的中间集：
                  <code v-for="k in deadIntermediateKeys" :key="k" class="om2-chip om2-chip-bad">{{ k }}</code>
                  —— 取了数但没有任何 <code>field_transform</code> 用 <code>from</code> 引用，数据到不了标准域。请在第③步引用，或删除该提取项。
                </div>
                <!-- 优化2：可内联提示 -->
                <div v-if="inlinableIntermediateKeys.length" class="om2-hint-inline">
                  💡 可简化：
                  <code v-for="k in inlinableIntermediateKeys" :key="k" class="om2-chip">{{ k }}</code>
                  仅被 1 条「整块透传」规则引用，可把提取键直接命名为目标标准域，省掉这层中间集。
                </div>
              </section>

              <!-- ③ field_transform（智能分析后 / 已有映射时显示）-->
              <section v-if="showMappingResult && !isPassthrough" class="om2-sec">
                <header class="om2-sec-head">
                  <span class="om2-no">③</span>
                  <span class="om2-title">字段转换 <code>field_transform</code></span>
                  <span class="om2-aux om2-aux-text">
                    标准域 {{ transformSlotsPreview.length }} / 7
                  </span>
                </header>
                <div class="om2-tip">
                  作用：把「来源」数据筛选/转换后写入标准域。三种映射方式：
                  <b>整块透传</b>（passthrough）· <b>只保留字段</b>（filter_include）· <b>排除字段</b>（filter_exclude）。
                  「来源 from」<b>推荐直接填响应 JSON 路径</b>（如 <code>bean.tags</code>），无需在②预先提取；
                  存量的中间集写法（引用 ② 的 <code>raw_xxx</code>）保存时会自动转成直连，行为不变。
                  推荐用下方「表格配置」勾选，无需手写 JSON；复杂场景可切「高级 JSON」。
                </div>

                <!-- 优化3：表格配置 / 高级 JSON 模式切换 -->
                <div class="ft-mode-switch">
                  <el-radio-group v-model="ftVisualMode" size="small">
                    <el-radio-button :value="true">表格配置</el-radio-button>
                    <el-radio-button :value="false">高级 JSON</el-radio-button>
                  </el-radio-group>
                  <span class="ft-mode-hint">
                    {{ ftVisualMode ? '勾选字段即可，保存的仍是标准 field_transform 结构' : '直接编辑 JSON，切回表格自动同步' }}
                  </span>
                </div>

                <!-- 表格配置模式 -->
                <div v-if="ftVisualMode" class="ft-table">
                  <!-- 直接提取的标准域（response_extract key=标准域名，整块透传优先的结果）：
                       无需 field_transform 规则，只读展示避免误以为映射丢失 -->
                  <div v-if="ftDirectDomains.length" class="ft-direct-block">
                    <div v-for="d in ftDirectDomains" :key="d.slot" class="ft-row ft-direct-row">
                      <span class="ft-c-slot">
                        <b>{{ STANDARD_SLOT_LABELS[d.slot] || d.slot }}（{{ d.slot }}）</b>
                      </span>
                      <span class="ft-c-from"><code>{{ d.path }}</code></span>
                      <span class="ft-c-type"><el-tag size="small" type="success">直接提取</el-tag></span>
                      <span class="ft-c-keys ft-dash">已在②响应提取中整块透传，无需转换规则</span>
                      <span class="ft-c-op"></span>
                    </div>
                  </div>
                  <div class="ft-row ft-head">
                    <span class="ft-c-slot">写入标准域</span>
                    <span class="ft-c-from">来源 from（响应 JSON 路径）</span>
                    <span class="ft-c-type">映射方式</span>
                    <span class="ft-c-keys">包含 / 排除字段</span>
                    <span class="ft-c-op"></span>
                  </div>
                  <div v-for="(row, i) in ftRows" :key="i" class="ft-row">
                    <div class="ft-c-slot">
                      <el-select v-model="row.slotKey" size="small" placeholder="标准域" @change="commitFtRows">
                        <el-option v-for="s in STANDARD_SLOT_LIST" :key="s.key"
                          :label="`${s.label}（${s.key}）`" :value="s.key" />
                      </el-select>
                      <el-input v-model="row.subKey" size="small" placeholder="子键(可选)"
                        class="ft-subkey" @input="commitFtRows" />
                    </div>
                    <div class="ft-c-from">
                      <el-select v-model="row.from" size="small" filterable allow-create default-first-option
                        placeholder="响应 JSON 路径，如 bean.tags" @change="commitFtRows">
                        <el-option v-for="k in extractKeysPreview" :key="k" :label="k" :value="k" />
                      </el-select>
                    </div>
                    <div class="ft-c-type">
                      <el-select v-model="row.type" size="small" @change="commitFtRows">
                        <el-option label="整块透传" value="passthrough" />
                        <el-option label="只保留字段" value="filter_include" />
                        <el-option label="排除字段" value="filter_exclude" />
                      </el-select>
                    </div>
                    <div class="ft-c-keys">
                      <el-select v-if="row.type !== 'passthrough'" v-model="row.keys" size="small"
                        multiple filterable allow-create default-first-option collapse-tags collapse-tags-tooltip
                        :placeholder="row.type === 'filter_include' ? '保留哪些字段' : '排除哪些字段'"
                        @change="commitFtRows">
                        <el-option v-for="f in ftFieldCandidates(row)" :key="f" :label="f" :value="f" />
                      </el-select>
                      <span v-else class="ft-dash">整块写入，无需选字段</span>
                    </div>
                    <div class="ft-c-op">
                      <el-button link type="danger" size="small" @click="removeFtRow(i)">删除</el-button>
                    </div>
                  </div>
                  <div v-if="!ftRows.length && !ftDirectDomains.length" class="ft-empty">还没有映射规则，点击下方「添加映射规则」，或在①填样例后点「智能分析」自动生成。</div>
                  <div v-else-if="!ftRows.length" class="ft-empty">其余标准域暂无转换规则；上方「直接提取」的域已整块写入，无需配置。</div>
                  <el-button size="small" plain class="ft-add" @click="addFtRow">+ 添加映射规则</el-button>
                </div>

                <!-- 高级 JSON 模式 -->
                <el-input v-else v-model="ifcEditForm.field_transform" type="textarea"
                  :autosize="{ minRows: 5, maxRows: 12 }" spellcheck="false" class="code-textarea" />
                <div v-if="transformInvalidFroms.length" class="om2-chips">
                  <div class="om2-warn">
                    ⚠ 未定义的数据集：
                    <code v-for="f in transformInvalidFroms" :key="f" class="om2-chip om2-chip-bad">{{ f }}</code>
                  </div>
                </div>

                <!-- 优化3 + 713_3：_unit_conversions 单位换算规则 —— 表格化编辑
                     增删/修改会写穿透到对应映射规则的 unit_convert / field_rename，后端运行时据此换算；
                     不改变现网数据格式，向后兼容。 -->
                <div v-if="ftUnitRows.length || ftRuleKeys.length" class="ft-unit-box">
                  <div class="ft-unit-head">
                    <span class="ft-unit-title">单位换算规则 <code>_unit_conversions</code></span>
                    <el-tooltip placement="top">
                      <template #content>
                        <div style="max-width:320px;line-height:1.7;">
                          对映射结果中的数值字段做<b>单位换算</b>（如流量 MB→GB、费用 分/角→元），并可选<b>重命名</b>字段。
                          在此表增删/修改后，会同步写入对应映射域的 <code>unit_convert</code> / <code>field_rename</code>，
                          后端运行时据此换算；不改变现网数据格式，向后兼容。
                        </div>
                      </template>
                      <span class="ft-unit-help">ⓘ 是什么？</span>
                    </el-tooltip>
                    <span style="flex:1;"></span>
                    <el-button link type="primary" size="small" @click="addUnitRow">+ 添加换算规则</el-button>
                    <el-button v-if="ftUnitRows.length" link type="danger" size="small" class="ft-unit-del" @click="clearAllUnitRows">清空全部</el-button>
                  </div>

                  <el-table v-if="ftUnitRows.length" :data="ftUnitRows" border size="small" class="ft-unit-table">
                    <el-table-column type="index" width="40" align="center" label="#" />
                    <el-table-column label="目标映射域 target_path" min-width="150">
                      <template #default="{ row }">
                        <el-select v-model="row.target_path" size="small" filterable allow-create default-first-option
                          placeholder="选择映射域" style="width:100%" @change="commitUnitRows">
                          <el-option v-for="k in ftRuleKeys" :key="k" :label="k" :value="k" />
                        </el-select>
                      </template>
                    </el-table-column>
                    <el-table-column label="原字段 field" min-width="140">
                      <template #default="{ row }">
                        <el-select v-model="row.field" size="small" filterable allow-create default-first-option
                          placeholder="字段名" style="width:100%" @change="commitUnitRows">
                          <el-option v-for="f in ftUnitFieldOptions(row.target_path)" :key="f" :label="f" :value="f" />
                        </el-select>
                      </template>
                    </el-table-column>
                    <el-table-column label="换算器 converter" width="190">
                      <template #default="{ row }">
                        <el-select v-model="row.converter" size="small" style="width:100%" @change="onUnitConverterChange(row)">
                          <el-option v-for="c in UNIT_CONVERTERS" :key="c.value" :label="c.label" :value="c.value" />
                        </el-select>
                      </template>
                    </el-table-column>
                    <el-table-column label="新字段名 new_field" min-width="140">
                      <template #default="{ row }">
                        <el-input v-model="row.new_field" size="small" placeholder="留空=不重命名" @change="commitUnitRows" />
                      </template>
                    </el-table-column>
                    <el-table-column label="说明 desc" min-width="120">
                      <template #default="{ row }">
                        <el-input v-model="row.desc" size="small" placeholder="可选" @change="commitUnitRows" />
                      </template>
                    </el-table-column>
                    <el-table-column label="" width="56" align="center">
                      <template #default="{ $index }">
                        <el-button link type="danger" size="small" @click="removeUnitRow($index)">删除</el-button>
                      </template>
                    </el-table-column>
                  </el-table>
                  <div v-else class="ft-unit-empty">
                    暂无单位换算规则。若映射结果里含 MB/分 等需换算的数值字段，点「+ 添加换算规则」。
                  </div>
                </div>

                <!-- 优化4：全部映射域一览 —— 体现 7 大标准域各自由哪条规则产出 -->
                <div class="ft-alldomain">
                  <div class="ft-alldomain-hd">
                    全部映射域一览
                    <span class="ft-alldomain-count">已映射 {{ ftAllDomainRows.filter(r => r.mapped).length }}/7</span>
                  </div>
                  <div class="ft-alldomain-grid">
                    <div v-for="r in ftAllDomainRows" :key="r.key"
                      class="ft-ad-item" :class="{ on: r.mapped }">
                      <span class="ft-ad-mark">{{ r.mapped ? '✓' : '○' }}</span>
                      <span class="ft-ad-label">{{ r.label }}</span>
                      <span class="ft-ad-src" :class="{ muted: !r.mapped }">{{ r.source }}</span>
                    </div>
                  </div>
                </div>
              </section>

              <!-- ④ 数据流真实结果（智能分析后 / 已有映射时显示，折叠默认展开） -->
              <section v-if="showMappingResult && !isPassthrough" class="om2-sec">
                <header class="om2-sec-head">
                  <span class="om2-no">④</span>
                  <span class="om2-title">真实数据流（基于上方样例计算）</span>
                  <span class="om2-aux om2-aux-text">点击展开样例值</span>
                </header>
                <div class="om2-flow-block">
                  <div class="om2-flow-label">
                    响应取数 response_extract
                    <span class="om2-flow-label-hint">（按 JSON 路径整块取出；键名命中标准域的会直接写入，见下一段）</span>
                  </div>
                  <details v-for="row in omExtractFlowRows" :key="'ext-'+row.key" class="om2-flow-row">
                    <summary>
                      <code class="om2-flow-key">{{ row.key }}</code>
                      <span class="om2-flow-sep">←</span>
                      <span class="om2-flow-path">{{ row.path }}</span>
                      <span class="om2-flow-meta">{{ row.summary }}</span>
                    </summary>
                    <pre class="om2-pre om2-pre-light">{{ row.preview }}</pre>
                  </details>
                  <div v-if="!omExtractFlowRows.length" class="om2-flow-empty">无</div>
                </div>
                <!-- 2-A 直接匹配：response_extract 的 key 命中 7 大标准域，无 field_transform 规则 -->
                <div class="om2-flow-block">
                  <div class="om2-flow-label">
                    标准数据域 · 直接匹配
                    <span class="om2-flow-label-hint">（response_extract 的 key 直接命中 7 大标准域，无需转换）</span>
                  </div>
                  <details v-for="row in omDirectMatchedRows" :key="'dm-'+row.slot" class="om2-flow-row">
                    <summary>
                      <span class="om2-flow-key">{{ row.slot }}</span>
                      <span class="om2-flow-sep">←</span>
                      <span class="om2-flow-type">直接提取</span>
                      <span class="om2-flow-path">{{ row.path }}</span>
                      <span class="om2-flow-meta">· {{ row.summary }}</span>
                    </summary>
                    <pre class="om2-pre om2-pre-light">{{ row.preview }}</pre>
                  </details>
                  <div v-if="!omDirectMatchedRows.length" class="om2-flow-empty">无（response_extract 中没有命中 7 大标准域同名 key）</div>
                </div>

                <!-- 2-B 字段转换：field_transform 规则产出的标准域 -->
                <div class="om2-flow-block">
                  <div class="om2-flow-label">
                    标准数据域 · 字段转换 field_transform
                    <span class="om2-flow-label-hint">（按规则筛选/转换后写入标准域；来源可以是响应路径直连，也可以是上方取出的数据集）</span>
                  </div>
                  <details v-for="row in omTransformFlowRows" :key="'tr-'+row.slot" class="om2-flow-row" :class="{ 'is-bad': row.invalid }">
                    <summary>
                      <span class="om2-flow-key">{{ row.slot }}</span>
                      <span class="om2-flow-sep">←</span>
                      <span class="om2-flow-type">{{ row.typeLabel }}</span>
                      <span class="om2-flow-path">{{ row.from || '（同名）' }}</span>
                      <span v-if="row.direct" class="om2-flow-ok">直连响应路径</span>
                      <span v-if="row.fieldDesc" class="om2-flow-meta">· {{ row.fieldDesc }}</span>
                      <span v-if="row.invalid" class="om2-flow-warn">
                        ⚠ 取不到数据：<code>{{ row.from }}</code> 既不是已声明的数据集，样例出参里也没有这条路径
                      </span>
                    </summary>
                    <pre class="om2-pre om2-pre-light">{{ row.preview }}</pre>
                  </details>
                  <div v-if="!omTransformFlowRows.length" class="om2-flow-empty">无 field_transform 规则</div>
                </div>
              </section>
            </div>
          </div>

          <div v-show="ifcEditTab === 'mock'">
            <div v-if="!isDirectMode" class="ifc-hint">
              录入接口<b>成功响应</b>的真实 / 模拟 JSON，作为第③步出参映射的依据。
              开启<b>模拟数据</b>后运行时不会真的调下游接口，直接用这份 JSON 作为响应，便于联调。
            </div>
            <div v-else class="ifc-hint">
              粘贴一份调用方将在请求中传入的 <code>extra_info</code> JSON 样例，
              作为第③步域映射配置与预览的依据（仅用于配置期，运行时以实际请求传入的 extra_info 为准）。
            </div>
            <el-form-item v-if="!isDirectMode" label="开启模拟" label-width="90px">
              <el-switch v-model="ifcEditForm.mock_mode" />
            </el-form-item>
            <el-form-item :label="isDirectMode ? '直传样例' : '响应 JSON'" label-width="90px">
              <el-input v-model="ifcEditForm.mock_response" type="textarea"
                :autosize="{ minRows: 8, maxRows: 20 }" spellcheck="false"
                :placeholder="isDirectMode
                  ? '{&quot;current_package&quot;:{&quot;offerName&quot;:&quot;128元套餐&quot;},&quot;usage&quot;:{...},&quot;tags&quot;:{...}}'
                  : '{&quot;rtnCode&quot;:&quot;0&quot;,&quot;bean&quot;:{...}}'"
                class="code-textarea" />
            </el-form-item>
            <div class="ifc-hint" style="margin-top:8px;">
              录好后请进入 <a class="ifc-link" @click="ifcEditTab='outparam'">③ {{ ifcStepOutLabel }}</a>。
            </div>
          </div>

        </div>
        <template #footer>
          <el-button @click="ifcEditVisible=false">取消</el-button>
          <el-button type="primary" :loading="ifcSaving" @click="saveIfcEdit">保存</el-button>
        </template>
      </el-dialog>

      <!-- 智能映射弹窗（与 MappingConfig 页面保持一致：双栏 + 重新执行/重新生成 + 应用保存） -->
      <el-dialog
        v-model="ifcAutoMapVisible"
        :title="`🤖 智能自动映射${ifcMapItem ? ' — ' + ifcMapItem.api_name : ''}`"
        width="min(1100px, 96vw)" :close-on-click-modal="false" destroy-on-close>
        <div class="tpl-dialog-body">
          <!-- Step 1: 粘贴样例 + 操作栏 -->
          <div class="automap-step-bar">
            <div class="automap-hint">
              粘贴接口<strong>成功响应</strong> JSON 样例，点击「智能分析」自动生成映射规则并预览数据域结果。
              支持多轮修改后重新生成，直到满意再「应用并保存」。
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
              <el-button type="primary" :loading="ifcMapLoading"
                :disabled="ifcMapRefineLoading" @click="runIfcMap">
                🔍 {{ ifcMapLoading ? '分析中...' : '智能分析' }}
          </el-button>
              <template v-if="autoMapDomainResultStr">
                <el-button :loading="ifcMapRefineLoading && ifcMapRefineMode==='preview'"
                  :disabled="ifcMapLoading || ifcMapRefineLoading" @click="runAutoMapPreview">
                  ▶ 重新执行映射
                </el-button>
                <el-button :loading="ifcMapRefineLoading && ifcMapRefineMode==='refine'"
                  :disabled="ifcMapLoading || ifcMapRefineLoading" @click="runAutoMapRefine">
                  🤖 重新生成规则
                </el-button>
              </template>
            </div>
        </div>

          <div v-if="autoMapAnalysis" class="ifc-analysis-box" style="margin-top:10px;">{{ autoMapAnalysis }}</div>

          <!-- 双栏：左侧出参样例 / 右侧数据域映射结果 -->
          <div class="automap-cols">
            <div class="automap-col">
              <div class="automap-col-label">出参成功示例 JSON</div>
              <el-input v-model="ifcMapSample" type="textarea"
                :autosize="{ minRows: 14, maxRows: 22 }" spellcheck="false"
                placeholder='{"rtnCode":200,"bean":{"mainoffer":{...},"tags":{...}}}'
                class="code-textarea" />
            </div>
            <div class="automap-col">
              <div class="automap-col-label">
                数据域映射结果
                <span v-if="autoMapDomainResultStr" class="automap-col-hint">
                  （可直接修改作为期望值，点击「重新生成规则」迭代）
                </span>
              </div>
              <el-input v-if="autoMapDomainResultStr !== null && (ifcMapResult || autoMapDomainResultStr)"
                v-model="autoMapDomainResultStr" type="textarea"
                :autosize="{ minRows: 14, maxRows: 22 }" spellcheck="false"
                placeholder="智能分析后，数据域映射结果将显示在此处，可直接修改…"
                class="code-textarea" />
              <div v-else class="automap-empty-hint">
                粘贴出参样例后点击「智能分析」，映射结果将显示在此处
              </div>
            </div>
          </div>

          <!-- 高级：response_extract / field_transform JSON -->
          <details class="agent-json-advanced" v-if="ifcMapResult" :open="!autoMapDomainResultStr"
            style="margin-top:12px;">
            <summary>高级：出参映射 JSON（response_extract / field_transform）</summary>
            <div style="margin-top:10px;">
              <div class="review-label">response_extract（路径提取）</div>
              <el-input v-model="autoMapExtractStr" type="textarea" :autosize="{minRows:5, maxRows:12}"
                spellcheck="false" class="code-textarea" style="margin-bottom:10px;" />
              <div class="review-label">field_transform（字段转换）</div>
              <el-input v-model="autoMapTransformStr" type="textarea" :autosize="{minRows:5, maxRows:12}"
                spellcheck="false" class="code-textarea" />
            </div>
          </details>
        </div>
        <template #footer>
          <el-button @click="ifcAutoMapVisible=false">取消</el-button>
          <el-button v-if="ifcMapResult" type="success"
            :loading="ifcSaving"
            :disabled="ifcMapLoading || ifcMapRefineLoading" @click="applyIfcMap">
            ✅ 应用并保存
          </el-button>
            </template>
      </el-dialog>

      <!-- 删除确认弹窗 -->
      <el-dialog v-model="ifcDelVisible" title="确认删除" width="400px" destroy-on-close>
        <p style="font-size:14px;line-height:1.7;">
          确认删除接口 <strong>{{ ifcDelItem?.api_name }}</strong>？<br/>
          <span style="color:var(--danger);font-size:13px;">此操作不可恢复。</span>
        </p>
        <template #footer>
          <el-button @click="ifcDelVisible=false">取消</el-button>
          <el-button type="danger" :loading="ifcDeleting" @click="confirmIfcDel">确认删除</el-button>
        </template>
      </el-dialog>

      <!-- 「上传文档自动解析生成接口」功能已按需求删除，新建接口仅保留手动填写（见 openIfcCreateMode）。 -->

      <!-- ── Agent 文档解析弹窗（已废弃，永不触发；ifcAgentVisible 不再被置为 true）── -->
      <el-dialog v-model="ifcAgentVisible"
        title="上传接口文档 — Agent 自动解析配置"
        width="min(1000px, 96vw)" :close-on-click-modal="ifcAgentStep==='upload' && !ifcAgentParsing"
        :close-on-press-escape="false" destroy-on-close class="agent-dialog">
        <!-- 步骤指示 -->
        <div class="agent-flow-steps">
          <div class="agent-flow-step" :class="{active:ifcAgentStep==='upload',done:ifcAgentStep!=='upload'}">
            <span class="num">1</span><span>上传文档</span>
          </div>
          <div class="agent-flow-line" :class="{done:ifcAgentStep!=='upload'}"></div>
          <div class="agent-flow-step" :class="{active:ifcAgentStep==='parsing',done:ifcAgentStep==='review'}">
            <span class="num">2</span><span>智能解析</span>
          </div>
          <div class="agent-flow-line" :class="{done:ifcAgentStep==='review'}"></div>
          <div class="agent-flow-step" :class="{active:ifcAgentStep==='review'}">
            <span class="num">3</span><span>确认保存</span>
          </div>
        </div>

        <!-- 步骤 1：上传 -->
        <div v-if="ifcAgentStep==='upload'">
          <p v-if="props.province && props.intent" class="agent-sub">
            省份：<strong>{{ props.province }}</strong>　意图：<strong>{{ props.intent }}</strong>
          </p>
          <p v-else class="agent-sub">解析结果将写入当前创建中的配置</p>
          <details class="agent-specs">
            <summary>接口文档规范要求（展开查看）</summary>
            <ul>
              <li>文档需包含：接口名称、描述、URL、请求方式、请求/响应说明及示例。</li>
              <li>请尽量使用与模板一致的结构，便于解析出 api_nodes 与映射规则。</li>
              <li>仅支持 <strong>.docx</strong> 格式。</li>
            </ul>
          </details>
          <div class="agent-drop-zone"
            :class="{dragging:ifcAgentDropHighlight}"
            @click="triggerAgentFilePick"
            @dragenter.prevent="ifcAgentDropHighlight=true"
            @dragleave.prevent="e => { if(!e.currentTarget.contains(e.relatedTarget)) ifcAgentDropHighlight=false }"
            @dragover.prevent="ifcAgentDropHighlight=true"
            @drop.prevent="e => { ifcAgentDropHighlight=false; setAgentFile(e.dataTransfer?.files?.[0]) }">
            <input ref="ifcAgentFileInputRef" type="file" accept=".docx" class="sr-only"
              @change="e => setAgentFile(e.target.files?.[0])">
            <div class="agent-drop-icon">📁</div>
            <div>点击选择或拖拽 .docx 文件到此处</div>
            <div v-if="ifcAgentFile" class="agent-file-name">{{ ifcAgentFile.name }}</div>
          </div>
        </div>

        <!-- 步骤 2：解析中 -->
        <div v-else-if="ifcAgentStep==='parsing'" class="agent-parsing-panel">
          <p class="agent-parsing-title">🤖 Interface Mapper Agent 执行中…</p>
          <div class="agent-progress-track">
            <div class="agent-progress-fill" :style="{width: agentProgressPct + '%'}"></div>
          </div>
          <ul class="agent-pipeline-list">
            <li v-for="(step,idx) in AGENT_PIPELINE" :key="step.id" class="agent-pipeline-item"
              :class="{done:idx<ifcAgentActivePipelineStep,active:idx===ifcAgentActivePipelineStep,pending:idx>ifcAgentActivePipelineStep}">
              <span class="agent-pipeline-icon">{{ idx<ifcAgentActivePipelineStep ? '✓' : idx===ifcAgentActivePipelineStep ? '⚙️' : '⏳' }}</span>
              <div class="agent-pipeline-body">
                <div class="agent-pipeline-title">Step {{ idx+1 }} — {{ step.title }}</div>
                <div class="agent-pipeline-desc">{{ step.desc }}</div>
              </div>
            </li>
          </ul>
          <p class="agent-parsing-hint">请保持本窗口打开；若耗时较久多为 LLM 分析出参，请耐心等待。</p>
        </div>

        <!-- 步骤 3：Review -->
        <div v-else-if="ifcAgentStep==='review'" class="tpl-dialog-body">
          <div class="agent-review-done-bar">
            <span class="agent-review-done-text">✅ Agent 解析完成，请确认以下结果</span>
            <el-button size="small" @click="agentBackToUpload">重新上传</el-button>
          </div>

          <div class="review-section-title">② 接口基础信息（可修改）</div>
          <el-row :gutter="14">
                <el-col :span="12">
              <el-form-item label="配置键名（api_name）" label-width="160px">
                <el-input v-model="ifcReviewBasic.api_name" placeholder="如 marketing_recommend_api" />
                  </el-form-item>
                </el-col>
            <el-col :span="12">
              <el-form-item label="接口描述" label-width="90px">
                <el-input v-model="ifcReviewBasic.description" />
                  </el-form-item>
                </el-col>
          </el-row>
          <el-row :gutter="14">
            <el-col :span="16">
              <el-form-item label="接口 URL" label-width="90px">
                <el-input v-model="ifcReviewBasic.url" placeholder="http://..." />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="请求方法" label-width="90px">
                <el-select v-model="ifcReviewBasic.method" style="width:100%">
                  <el-option label="POST" value="POST" /><el-option label="GET" value="GET" /><el-option label="PUT" value="PUT" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>

          <!-- 入参匹配 -->
          <div v-if="ifcReviewParamMatches.length">
            <div class="review-section-title">③ 入参解析（入参映射）</div>
            <div class="agent-inparam-hint-bar">
              <span class="agent-hint-tag agent-hint-green">主服务入参</span>
              <span class="agent-hint-text">phone / intent / callId / province / topN 等</span>
              <span class="agent-hint-tag agent-hint-blue">extra_data</span>
              <span class="agent-hint-text">extra_data.currentMainOffer.* 等</span>
              <span class="agent-hint-tag agent-hint-warn">未匹配</span>
              <span class="agent-hint-text">需手动指定占位符</span>
            </div>
            <div class="agent-param-table-wrap">
              <table class="agent-param-table">
                <thead><tr><th>入参字段</th><th>匹配来源</th><th>取值（可修改）</th></tr></thead>
                <tbody>
                  <tr v-for="(row,ri) in ifcReviewParamMatches" :key="ri">
                    <td class="mono">{{ row.api_param }}</td>
                    <td>
                      <select v-model="row.match_type" class="review-param-select"
                        @change="onReviewParamMatchTypeChange(row)">
                        <option value="direct">主服务入参</option>
                        <option value="extra_data">extra_data</option>
                        <option value="unmatched">未匹配</option>
                      </select>
                    </td>
                    <td><input v-model="row.placeholder" class="review-param-input" type="text"
                      :placeholder="'如 {{PHONE}} 或 {{extra_data.xx}}'"></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- 出参映射 -->
          <div class="review-section-title">④ 出参映射 &amp; 数据域映射结果</div>
          <div class="agent-outmap-toolbar">
            <el-button size="small" :loading="ifcOutMapLoading" @click="runRegenMappingRules">🤖 重新生成规则</el-button>
          </div>
          <div v-if="ifcReviewAnalysis" class="agent-llm-green-box">{{ ifcReviewAnalysis }}</div>
          <div class="agent-outmap-cols">
            <el-form-item label="出参成功示例 JSON" label-width="0">
              <el-input v-model="ifcReviewMockStr" type="textarea" :autosize="{minRows:8,maxRows:14}"
                spellcheck="false" class="code-textarea" />
                  </el-form-item>
            <el-form-item label="数据域映射结果（可修改）" label-width="0">
              <el-input v-model="ifcReviewDomainResultStr" type="textarea" :autosize="{minRows:8,maxRows:14}"
                spellcheck="false" placeholder="解析或重新生成后显示…" class="code-textarea" />
            </el-form-item>
          </div>

          <!-- 高级 JSON -->
          <details class="agent-json-advanced">
            <summary>高级：请求模板与出参映射（JSON 可编辑）</summary>
            <div style="margin-top:10px;">
              <div class="review-label">请求体模板（request_template）</div>
              <el-input v-model="ifcReviewReqStr" type="textarea" :autosize="{minRows:5,maxRows:12}"
                spellcheck="false" class="code-textarea" style="margin-bottom:10px;" />
              <el-row :gutter="12">
                <el-col :span="12">
                  <div class="review-label">响应提取规则（response_extract）</div>
                  <el-input v-model="ifcReviewExtStr" type="textarea" :autosize="{minRows:6,maxRows:14}"
                    spellcheck="false" class="code-textarea" />
                </el-col>
                <el-col :span="12">
                  <div class="review-label">字段转换规则（field_transform）</div>
                  <el-input v-model="ifcReviewTrStr" type="textarea" :autosize="{minRows:6,maxRows:14}"
                    spellcheck="false" class="code-textarea" />
                </el-col>
              </el-row>
            </div>
          </details>
        </div>

        <template #footer>
          <el-button v-if="ifcAgentStep==='upload'" :disabled="ifcAgentParsing"
            @click="ifcAgentVisible=false">取消</el-button>
          <el-button v-if="ifcAgentStep==='upload'" type="primary"
            :disabled="!ifcAgentFile" :loading="ifcAgentParsing"
            @click="runAgentDocPreview">
            {{ ifcAgentParsing ? '🤖 解析中...' : '🤖 启动智能解析' }}
          </el-button>
          <el-button v-if="ifcAgentStep==='parsing'" @click="cancelAgentParse">取消</el-button>
          <template v-if="ifcAgentStep==='review'">
            <el-button @click="ifcAgentVisible=false">取消</el-button>
            <el-button type="success" :loading="ifcSaving" @click="confirmAgentApply">
              确认信息并写入接口配置
            </el-button>
          </template>
        </template>
      </el-dialog>

      <!-- 「标准数据关联」tab 已下线：改为接口表「操作」列的「映射结果」按钮，单接口只读展示（见上方映射结果弹窗）。 -->

      <!-- ═══════════ Tab 2：话术模板 ═══════════ -->
      <el-tab-pane name="templates">

        <template #label>
          话术模板
          <el-badge :value="templateList.length" type="info" style="margin-left:6px;" />
        </template>

        <!-- 模板匹配与填槽设置（接口查询模式）-->
        <details class="tpl-match-panel" open @toggle="e => e.target.open && ensureIfcDetails()">
          <summary class="tpl-match-summary">
            ▶ 模板匹配与填槽设置（接口查询模式）
            <span class="tpl-match-summary-hint">
              指定查询/推荐结果中哪个字段用于匹配话术模板；配置标准域为空时的入参兜底
            </span>
          </summary>
          <div class="tpl-match-body">
            <div class="tpl-match-toolbar">
              <span class="tpl-match-hint" style="margin:0;">
                打开时从已保存的 <code>biz_config.template_match</code> /
                <code>api_nodes._domain_fallbacks</code> 自动回填；修改后请点「保存设置」写入生效配置
              </span>
              <el-button
                type="primary" size="small"
                :loading="matchSettingsSaving"
                :disabled="matchSettingsSaving"
                @click="saveMatchSettings"
              >保存设置</el-button>
            </div>
            <div class="tpl-match-row">
              <span class="tpl-match-label">产品ID取值字段</span>
              <el-select
                v-model="tmProductIdArr" size="small" multiple filterable allow-create
                default-first-option clearable collapse-tags collapse-tags-tooltip
                placeholder="从接口出参映射结果中选择，或手动输入字段名/点路径；多选时按序取第一个非空值"
                style="width:380px;" @change="onMatchSettingsChange"
              >
                <el-option
                  v-for="c in matchFieldCandidates" :key="c.field"
                  :value="c.field" :label="c.field"
                >
                  <span style="font-family:monospace;">{{ c.field }}</span>
                  <el-tag v-if="c.score >= 2" size="small" type="success" style="margin-left:6px;">推荐</el-tag>
                  <span class="tpl-match-opt-src">{{ c.source }}<template v-if="c.sample"> · 样例: {{ c.sample }}</template></span>
                </el-option>
              </el-select>
              <el-button size="small" plain @click="autoRecommendMatchField">智能推荐</el-button>
              <span class="tpl-match-hint">
                推荐结果中该字段的值用于匹配话术模板「产品 ID」；留空按默认字段
                （offerId / product_id / package_id / offer_id）取值
              </span>
            </div>
            <div class="tpl-match-row">
              <span class="tpl-match-label">环节取值字段</span>
              <el-select
                v-model="tmStageArr" size="small" multiple filterable allow-create
                default-first-option clearable collapse-tags collapse-tags-tooltip
                placeholder="可选：入参未传环节时，从推荐结果该字段取值匹配「环节」"
                style="width:380px;" @change="onMatchSettingsChange"
              >
                <el-option
                  v-for="c in matchFieldCandidates" :key="c.field"
                  :value="c.field" :label="c.field"
                >
                  <span style="font-family:monospace;">{{ c.field }}</span>
                  <span class="tpl-match-opt-src">{{ c.source }}<template v-if="c.sample"> · 样例: {{ c.sample }}</template></span>
                </el-option>
              </el-select>
            </div>
            <div class="tpl-match-row">
              <span class="tpl-match-label">意图取值字段</span>
              <el-select
                v-model="tmSceneArr" size="small" multiple filterable allow-create
                default-first-option clearable collapse-tags collapse-tags-tooltip
                placeholder="可选：入参未传意图时，从推荐结果该字段取值匹配「意图」"
                style="width:380px;" @change="onMatchSettingsChange"
              >
                <el-option
                  v-for="c in matchFieldCandidates" :key="c.field"
                  :value="c.field" :label="c.field"
                >
                  <span style="font-family:monospace;">{{ c.field }}</span>
                  <span class="tpl-match-opt-src">{{ c.source }}<template v-if="c.sample"> · 样例: {{ c.sample }}</template></span>
                </el-option>
              </el-select>
            </div>
            <el-divider style="margin:8px 0;" />
            <div class="tpl-match-row" style="align-items:flex-start;">
              <span class="tpl-match-label" style="margin-top:4px;">空域入参兜底</span>
              <div style="flex:1;">
                <div
                  v-for="(row, i) in domainFallbacks" :key="i"
                  class="tpl-match-row" style="margin-bottom:4px;"
                >
                  <el-select v-model="row.domain" size="small" style="width:210px;" @change="onMatchSettingsChange">
                    <el-option
                      v-for="s in STANDARD_SLOT_LIST" :key="s.key"
                      :label="`${s.label}（${s.key}）`" :value="s.key"
                    />
                  </el-select>
                  <span class="tpl-match-arrow">←&nbsp;extra_data.</span>
                  <el-input
                    v-model="row.path" size="small" clearable
                    placeholder="入参路径，如 currentMainOffer"
                    style="width:220px;" @change="onMatchSettingsChange"
                  />
                  <el-button
                    size="small" link type="danger"
                    @click="domainFallbacks.splice(i, 1); onMatchSettingsChange()"
                  >删除</el-button>
                </div>
                <el-button
                  size="small" plain
                  @click="domainFallbacks.push({ domain: 'current_package', path: '' })"
                >+ 添加兜底</el-button>
                <div class="tpl-match-hint" style="margin-top:4px;">
                  接口映射后标准域仍为空时，用主服务入参 extra_data 对应字段回填，
                  保证话术槽位有事实可填（如：当前套餐 ← currentMainOffer）；接口有数据时不生效
                </div>
              </div>
            </div>
          </div>
        </details>

        <!-- 工具栏 -->

        <div class="tpl-toolbar">
          <span class="tpl-toolbar-title">
            话术模板（{{ templateList.length }} 组<template v-if="templateTotalCount !== templateList.length"> · 共 {{ templateTotalCount }} 条</template>）
          </span>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <!-- 筛选：产品 ID / 场景分类名称 / 环节（省份由当前技能包固定，无需筛选）-->
            <el-select
              v-model="tplFilterProductId"
              size="small"
              placeholder="产品 ID"
              clearable
              filterable
              style="width:180px;"
              @change="tplPage = 1"
              @clear="tplPage = 1"
            >
              <el-option v-for="p in tplProductIdOptions" :key="p" :label="p" :value="p" />
            </el-select>
            <el-input
              v-model="tplFilterName"
              size="small"
              placeholder="场景分类名称"
              clearable
              style="width:170px;"
              @input="tplPage = 1"
            />
            <el-select
              v-model="tplFilterStage"
              size="small"
              placeholder="环节"
              clearable
              style="width:130px;"
              @change="tplPage = 1"
              @clear="tplPage = 1"
            >
              <el-option v-for="s in tplStageOptions" :key="s" :label="s" :value="s" />
            </el-select>
            <el-select
              v-model="tplFilterScene"
              size="small"
              placeholder="意图"
              clearable
              style="width:130px;"
              @change="tplPage = 1"
              @clear="tplPage = 1"
            >
              <el-option v-for="s in tplSceneOptions" :key="s" :label="s" :value="s" />
            </el-select>
            <el-button
              v-if="selectedTemplates.length"
              size="small" type="danger" plain
              :loading="batchDeleting"
              @click="batchRemoveTemplates"
            >
              <el-icon><Delete /></el-icon>&nbsp;批量删除（{{ selectedTemplates.length }}）
            </el-button>
            <el-button size="small" plain @click="openImportCsv">
              <el-icon><Upload /></el-icon>&nbsp;导入 CSV
            </el-button>
            <el-button size="small" type="primary" plain @click="openAddTemplate">
              <el-icon><Plus /></el-icon>&nbsp;添加模板
            </el-button>
          </div>
        </div>

        <!-- 模板表格 -->
        <el-table
          ref="tplTableRef"
          :data="pagedTemplates"
          border
          stripe
                    size="small"
          style="width:100%;margin-top:8px;"
          :row-key="tplRowKey"
          :row-class-name="({row}) => row.status === 'offline' ? 'tpl-row-offline' : ''"
          @selection-change="onTplSelectionChange"
        >
          <el-table-column type="selection" width="40" align="center" reserve-selection />
          <el-table-column type="index" :index="(i) => (tplPage-1)*TBL_PAGE_SIZE + i + 1"
            width="48" align="center" label="#" />
          <el-table-column label="场景分类名称" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <span style="font-weight:600;">{{ row.template_name || '（未命名）' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="stage" label="环节" width="100" show-overflow-tooltip />
          <el-table-column prop="scene" label="意图" min-width="100" show-overflow-tooltip />
          <el-table-column label="产品 ID" min-width="200">
            <template #default="{ row }">
              <template v-if="rowProductIds(row).length">
                <div style="display:flex;flex-wrap:wrap;gap:2px;align-items:center;"
                  :title="rowProductIds(row).join('、')">
                  <el-tag
                    v-for="p in rowProductIds(row).slice(0, 3)" :key="p"
                    size="small" style="font-family:monospace;max-width:150px;overflow:hidden;text-overflow:ellipsis;"
                  >{{ p }}</el-tag>
                  <el-tag
                    v-if="rowProductIds(row).length > 3"
                    size="small" type="info" effect="plain"
                  >等 {{ rowProductIds(row).length }} 个</el-tag>
                </div>
              </template>
              <span v-else style="font-size:11px;color:var(--muted);">（兜底 · 全产品通用）</span>
            </template>
          </el-table-column>
          <el-table-column label="话术内容" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span style="color:var(--muted);font-size:12px;">
                {{ (row.template_content || '').slice(0, 60) }}{{ row.template_content?.length > 60 ? '…' : '' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="关联接口" width="160">
            <template #default="{ row }">
              <template v-if="(row.linked_apis || []).length">
                <el-tag
                  v-for="a in (row.linked_apis || []).slice(0, 2)" :key="a"
                  size="small" type="primary" style="margin:1px;font-family:monospace;"
                >{{ a }}</el-tag>
                <span v-if="(row.linked_apis || []).length > 2" style="font-size:11px;color:var(--muted);">
                  +{{ row.linked_apis.length - 2 }}
                </span>
              </template>
              <span v-else style="font-size:11px;color:#b45309;" title="未指定时调用全部启用接口">全部接口</span>
            </template>
          </el-table-column>
          <el-table-column label="关联变量" width="130">
            <template #default="{ row }">
              <el-tag
                v-for="v in (row.linked_vars || []).slice(0, 2)" :key="v"
                size="small" type="warning" style="margin:1px;"
                  >{{ v }}</el-tag>
              <span v-if="(row.linked_vars || []).length > 2" style="font-size:11px;color:var(--muted);">
                +{{ row.linked_vars.length - 2 }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="72" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'online' ? 'success' : 'info'">
                {{ row.status === 'online' ? '上线' : '下线' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" align="center" fixed="right">
            <template #default="{ row, $index }">
              <el-button size="small" link @click="openEditTemplate(row)">编辑</el-button>
              <el-button size="small" link type="danger"
                @click="removeTemplate((tplPage-1)*TBL_PAGE_SIZE + $index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="!filteredTemplates.length" class="empty-tip">
          {{ (tplFilterName || tplFilterStage || tplFilterScene) ? '无匹配结果' : '暂无话术模板，点击「添加模板」' }}
        </div>

        <!-- 分页 -->
        <div v-if="filteredTemplates.length > TBL_PAGE_SIZE" class="tpl-pagination">
          <el-pagination
            v-model:current-page="tplPage"
            :page-size="TBL_PAGE_SIZE"
            :total="filteredTemplates.length"
            layout="prev, pager, next, jumper, total"
            small
          />
        </div>
      </el-tab-pane>

      <!-- Tab 3「数据流映射」已按首页优化要求删除（该 tab 不易理解）。 -->

      <!-- ═══════════ 话术模板 编辑弹窗（复用 TemplateEditDialog · 多产品模式）═══════════ -->
      <TemplateEditDialog
        v-model="tplDialogVisible"
        :template="tplEditing"
        :is-new="tplDialogIsNew"
        mode="skill"
        :multi-product="true"
        :available-apis="availableApisForTpl"
        @save="handleTemplateSave"
      />

      <!-- ═══════════ 导入 CSV 弹窗 ═══════════ -->
      <el-dialog v-model="importVisible" title="📥 导入话术模板" width="600px"
        destroy-on-close class="csv-import-dialog">
        <div class="csv-import-body">
          <!-- 步骤 1：下载模板并填写 -->
          <section class="csv-step">
            <div class="csv-step-head">
              <span class="csv-step-no">1</span>
              <span class="csv-step-title">下载模板并按列填写</span>
            </div>
            <div class="csv-step-body">
              <el-button type="primary" plain @click="downloadCsvTemplate">
                <el-icon><Download /></el-icon>&nbsp;下载 CSV 模板
              </el-button>
              <div class="csv-cols">
                <span class="csv-col-tag">场景分类名称</span>
                <span class="csv-col-tag">环节</span>
                <span class="csv-col-tag">意图</span>
                <span class="csv-col-tag">产品ID</span>
                <span class="csv-col-tag req">话术内容 *</span>
                <span class="csv-col-tag">关联变量</span>
                <span class="csv-col-tag">状态</span>
              </div>
              <ul class="csv-tips">
                <li>仅<strong>「话术内容」</strong>必填；<strong>「关联变量」</strong>留空即可，系统按接口自动关联、占位符运行时自动匹配。</li>
                <li>编码自动识别：UTF-8 与 Excel 另存的 GBK / GB2312 均<strong>不会乱码</strong>。</li>
                <li v-if="props.province && props.intent">导入即写入配置（不覆盖已有模板），再点<strong>「发布上线」</strong>热重载生效。</li>
                <li v-else>导入后追加到列表，<strong>保存配置</strong>后生效。</li>
              </ul>
            </div>
          </section>

          <!-- 步骤 2：上传文件 -->
          <section class="csv-step">
            <div class="csv-step-head">
              <span class="csv-step-no">2</span>
              <span class="csv-step-title">上传填好的 CSV 文件</span>
            </div>
            <div class="csv-step-body">
              <el-upload
                v-if="!csvFile"
                drag
                action="#"
                :auto-upload="false"
                :show-file-list="false"
                accept=".csv,.txt"
                :on-change="onCsvUploadChange"
              >
                <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
                <div class="el-upload__text">将 CSV 文件拖到此处，或<em>点击选择</em></div>
                <template #tip>
                  <div class="csv-upload-tip">支持 .csv / .txt，单个文件</div>
                </template>
              </el-upload>
              <div v-else class="csv-file-card">
                <el-icon class="csv-file-icon"><Document /></el-icon>
                <div class="csv-file-meta">
                  <div class="csv-file-name" :title="csvFile.name">{{ csvFile.name }}</div>
                  <div class="csv-file-size">{{ _fmtFileSize(csvFile.size) }}</div>
                </div>
                <el-button link type="danger" :disabled="csvImporting" @click="clearCsvFile">
                  <el-icon><Delete /></el-icon>&nbsp;移除
                </el-button>
              </div>
            </div>
          </section>

          <!-- 结果反馈 -->
          <el-alert
            v-if="importMsg"
            :type="importOk ? 'success' : 'error'"
            :closable="false"
            show-icon
            :title="importMsg"
            class="csv-result"
          />
        </div>
        <template #footer>
          <el-button @click="importVisible = false">关闭</el-button>
          <el-button
            type="primary"
            :loading="csvImporting"
            :disabled="!csvFile"
            @click="doImportCsv"
          >{{ (props.province && props.intent) ? '导入并保存' : '确认导入' }}</el-button>
        </template>
      </el-dialog>

    </el-tabs>
  </div>
</template>

<script setup>
import { ref, reactive, watch, computed } from 'vue'
import { useRouter } from 'vue-router'
import { $msg, useLock } from '@/utils/msg'

// 统一消息反馈（交互规范 2）：本组件历史上大量直接调用 ElMessage，
// 这里以同名本地对象桥接到全局 $msg 工具，成功/警告/错误三类反馈行为全局一致。
const ElMessage = {
  success: (m) => $msg.ok(m),
  warning: (m) => $msg.warn(m),
  error:   (m) => $msg.err(m),
  info:    (m) => $msg.info(m),
}
import http from '@/api/index.js'
import { apiFetch } from '@/utils/apiUrl'
import { useEnv } from '@/composables/useEnv'
import TemplateEditDialog from '@/components/TemplateEditDialog.vue'

// 运行环境：响应提取 / 标准数据关联仅开发环境显示（方便测试），规范 1/2
const { isDev } = useEnv()

const _stdRouter = useRouter()
function openStdDomainsHelp() {
  const url = _stdRouter.resolve({ path: '/StandardDomains' }).href
  window.open(url, '_blank')
}


const props = defineProps({
  /** { api_nodes: {...}, biz_config: {...} } */
  modelValue: {
    type: Object,
    default: () => ({ api_nodes: {}, biz_config: {} }),
  },
  /** 当前 Skill 的省份（用于 LLM 智能解析上下文） */
  province: { type: String, default: '' },
  /** 当前 Skill 的意图（用于 LLM 智能解析上下文） */
  intent:   { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

// ── 内部工作状态 ──────────────────────────────────────
const activeTab = ref('api')

// ══════════════════════════════════════════════════════
// ── 模式 A：直连后端 /api/interfaces（有 province+intent）
// ══════════════════════════════════════════════════════
const IFC_PAGE_SIZE = 20
const ifcItems   = ref([])
const ifcSearch  = ref('')
const ifcPage    = ref(1)
const ifcLoading = ref(false)

const filteredIfcItems = computed(() => {
  const q = ifcSearch.value.trim().toLowerCase()
  if (!q) return ifcItems.value
  return ifcItems.value.filter(x =>
    (x.api_name    || '').toLowerCase().includes(q) ||
    (x.description || '').toLowerCase().includes(q) ||
    (x.url         || '').toLowerCase().includes(q)
  )
})
const pagedIfcItems = computed(() => {
  const s = (ifcPage.value - 1) * IFC_PAGE_SIZE
  return filteredIfcItems.value.slice(s, s + IFC_PAGE_SIZE)
})

async function loadIfcItems() {
  if (!props.province || !props.intent) return
  ifcLoading.value = true
  try {
    const res  = await apiFetch(`/api/interfaces?province=${encodeURIComponent(props.province)}`)
    const json = await res.json()
    ifcItems.value = (json.data || []).filter(x => x.intent === props.intent)
  } catch (e) {
    console.warn('loadIfcItems failed:', e)
  } finally {
    ifcLoading.value = false
  }
}

// 当 province/intent props 变化时自动加载
watch(() => [props.province, props.intent], ([p, i]) => {
  if (p && i) loadIfcItems()
}, { immediate: true })

// ── 映射结果弹窗（原「标准数据关联」tab 内容，收敛为单接口只读展示）──────────
const mapResultVisible = ref(false)
const mapResultApiName = ref('')

/** 从接口表「操作」列打开该接口的标准域映射结果 */
function openMapResult(row) {
  mapResultApiName.value = row.api_name || row._key || ''
  mapResultVisible.value = true
  // 模式 A：拉取接口详情后 getSimulatedSlots 才能算出真实结果（reactive，加载完成自动刷新）
  ensureIfcDetails()
}

/** 当前查看的接口对象（含 source_type / description，用于弹窗头部）*/
const mapResultApi = computed(() =>
  availableApisForTpl.value.find(a => a.api_name === mapResultApiName.value) || null
)

/** 弹窗内「编辑出参映射」：复用既有跳转逻辑，打开该接口的编辑弹窗并定位到出参映射 */
async function editMappingFromMapResult() {
  const name = mapResultApiName.value
  mapResultVisible.value = false
  await editMappingFromDomainLink({ api_name: name })
}

// ── 新建/编辑弹窗 ────────────────────────────────────────
const ifcEditVisible = ref(false)
const ifcEditIsNew   = ref(false)
const ifcEditTab     = ref('basic')
const ifcSaving      = ref(false)
const ifcEditForm    = reactive({
  api_name: '', description: '', url: '', method: 'POST',
  enabled: true, request_template: '', response_extract: '{}',
  field_transform: '{}', mock_mode: false, mock_response: '{}',
  source_type: 'api',   // 'api' 接口查询模式 | 'direct' 直传模式（extra_info）
  direct_mode: 'mapping', // 直传子模式：'mapping' 智能映射到标准域 | 'passthrough' 直接透传字段
  passthrough_fields: [], // 透传子模式下选定的入参字段（空=全部顶层字段）
  headers_pairs: [],    // 接口查询模式请求头（键值对，保存时转 headers 对象）
})

// ── 请求头（接口查询模式）键值对 ↔ 对象互转 ────────────────────
// 运行时 api_client 会在默认 Content-Type/Accept 之上叠加/覆盖这里的头（同名以此处为准）。
// 例：北京查询接口需带渠道标识 { "x-Channel-ID": "ngbusi" }。
function headersObjToPairs(obj) {
  const o = obj && typeof obj === 'object' ? obj : {}
  const pairs = Object.entries(o).map(([k, v]) => ({ k: String(k), v: v == null ? '' : String(v) }))
  return pairs.length ? pairs : [{ k: 'Content-Type', v: 'application/json' }]
}
function headersPairsToObj(pairs) {
  const out = {}
  for (const p of (pairs || [])) {
    const k = (p.k || '').trim()
    if (!k) continue
    out[k] = p.v == null ? '' : String(p.v)
  }
  return out
}
function addHeaderRow() { ifcEditForm.headers_pairs.push({ k: '', v: '' }) }
function removeHeaderRow(i) { ifcEditForm.headers_pairs.splice(i, 1) }

// ── 直传模式辅助 ──────────────────────────────────────
const isDirectMode = computed(() => ifcEditForm.source_type === 'direct')
// 直接透传子模式：入参字段按原字段名直接作为 context，不强制映射到 7 标准域
const isPassthrough = computed(() => isDirectMode.value && ifcEditForm.direct_mode === 'passthrough')
const ifcStepMockLabel = computed(() => isDirectMode.value ? '直传样例' : '响应样例')
const ifcStepOutLabel  = computed(() => isPassthrough.value ? '透传字段' : (isDirectMode.value ? '域映射' : '出参映射'))

// 透传子模式：从 extra_info 样例解析出的顶层字段（供勾选 passthrough_fields）
const passthroughSampleFields = computed(() => {
  try {
    const obj = JSON.parse(ifcEditForm.mock_response || '{}')
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return []
    const STD = new Set(['current_package','usage','tags','user_info','recommended_packages','user_profile','domain_ext'])
    return Object.keys(obj).filter(k => !k.startsWith('_'))
      .map(k => ({ key: k, isStd: STD.has(k), preview: _shortPreview(obj[k]) }))
  } catch { return [] }
})
function _shortPreview(v) {
  let s
  if (v === null || v === undefined) s = ''
  else if (typeof v === 'object') { try { s = JSON.stringify(v) } catch { s = String(v) } }
  else s = String(v)
  return s.length > 40 ? s.slice(0, 40) + '…' : s
}

// 出参映射样例与「模拟数据/响应样例」共用 ifcEditForm.mock_response（单一来源）
const ifcAutoMapLoading  = ref(false)
const ifcAutoMapAnalysis = ref('')
// 编辑前的原始配置快照（用于"无修改不更新"判断）
const ifcEditOriginalSnapshot = ref(null)

// 可视化数据流预览（智能分析返回的真实运行结果）
const ifcAutoMapPreview  = ref(null)   // { 中间数据集名 / 标准域名: 真实值 }
const ifcAutoMapHasRun   = ref(false)  // 是否点过「智能分析」


// ── 弹窗 wizard 辅助：步骤完成状态 + 产出预览 ───────────────────
function isIfcStepDone(step) {
  if (step === 'basic') {
    if (isDirectMode.value) return !!ifcEditForm.api_name
    return !!(ifcEditForm.api_name && ifcEditForm.url)
  }
  if (step === 'mock') {
    if (ifcEditForm.mock_mode) return true
    try {
      const o = JSON.parse(ifcEditForm.mock_response || '{}')
      return o && Object.keys(o).length > 0
    } catch { return false }
  }
  if (step === 'outparam') {
    try {
      const ext = JSON.parse(ifcEditForm.response_extract || '{}')
      return ext && Object.keys(ext).length > 0
    } catch { return false }
  }
  return false
}

const previewExtractKeys = computed(() => {
  try {
    const ext = JSON.parse(ifcEditForm.response_extract || '{}')
    return Object.keys(ext || {})
  } catch { return [] }
})

// 出参映射 step ③ 三框预览 ----------------------------------------
const extractKeysPreview = computed(() => previewExtractKeys.value)

// 7 大标准域键集合（延迟求值，避免与后置声明的 STANDARD_SLOT_LIST 形成 TDZ）
const standardSlotKeySet = computed(() => new Set(STANDARD_SLOT_LIST.map(s => s.key)))

// 优化1：response_extract 键分两类
//  · 直取到标准域：键名 ∈ 7 大域，直接写入，无需 field_transform
//  · 中间数据集：键名 ∉ 7 大域（如 raw_tags），供 field_transform 拆分/筛选
const extractDirectKeys = computed(() =>
  extractKeysPreview.value.filter(k => standardSlotKeySet.value.has(k)))
const extractIntermediateKeys = computed(() =>
  extractKeysPreview.value.filter(k => !standardSlotKeySet.value.has(k)))

// field_transform 各规则的 from 引用（from 省略时默认取 target 首段）
const ftFromRefs = computed(() => {
  const refs = {}   // fromName -> [{ target, type }]
  try {
    const ft = JSON.parse(ifcEditForm.field_transform || '{}')
    for (const [target, rule] of Object.entries(ft || {})) {
      if (String(target).startsWith('_')) continue
      const r = (rule && typeof rule === 'object') ? rule : {}
      const from = (typeof r.from === 'string' && r.from) ? r.from : target
      ;(refs[from] = refs[from] || []).push({ target, type: r.type || 'passthrough' })
    }
  } catch { /* ignore */ }
  return refs
})

// 优化2-①：死中间集 —— 中间集取了数却没有任何 field_transform 用 from 引用，数据到不了标准域
const deadIntermediateKeys = computed(() =>
  extractIntermediateKeys.value.filter(k => !(ftFromRefs.value[k] && ftFromRefs.value[k].length)))

// 优化2-②：可内联的中间集 —— 仅被 1 条「整块透传」规则引用，可直接命名为目标标准域
const inlinableIntermediateKeys = computed(() =>
  extractIntermediateKeys.value.filter(k => {
    const rs = ftFromRefs.value[k] || []
    return rs.length === 1 && rs[0].type === 'passthrough'
  }))

const transformSlotsPreview = computed(() => {
  try {
    const ft = JSON.parse(ifcEditForm.field_transform || '{}')
    const slots = new Set()
    for (const k of Object.keys(ft || {})) slots.add(String(k).split('.')[0])
    return [...slots]
  } catch { return [] }
})

// 优化1：接口编辑弹窗「产出预览」——当前配置最终会写入哪些 7 大标准域
const ifcEditProducedSlots = computed(() => {
  const cfg = {
    response_extract: _safeParse(ifcEditForm.response_extract),
    field_transform:  _safeParse(ifcEditForm.field_transform),
    source_type:      ifcEditForm.source_type,
    mock_response:    _safeParse(ifcEditForm.mock_response),
  }
  return new Set(inferProducedSlots(cfg))
})

// 优化5：只有点过「智能分析」（或编辑已有映射的接口）后，才展示
// response_extract / field_transform / 真实数据流 / 验证结果，保持新建时界面简洁。
const showMappingResult = computed(() => {
  if (ifcAutoMapHasRun.value) return true
  const nonEmpty = (s) => { const t = String(s || '').trim(); return t && t !== '{}' && t !== '[]' }
  return nonEmpty(ifcEditForm.response_extract) || nonEmpty(ifcEditForm.field_transform)
})

// ── 优化3：field_transform 表格化可视配置（与高级 JSON 模式等价，数据格式不变）──
const ftVisualMode = ref(true)          // true 表格模式 / false 高级 JSON
const ftRows = ref([])                  // 可视表格行模型

// 标准域 key → 中文标签（供直接提取只读行展示）
const STANDARD_SLOT_LABELS = {
  current_package: '当前套餐', usage: '历史用量', tags: '用户标签',
  user_info: '用户基础信息', recommended_packages: '推荐产品',
  user_profile: '用户画像', domain_ext: '扩展域',
}

// 直接提取的标准域：response_extract 的 key 命中 7 大标准域且未被 field_transform 覆盖。
// 这类域走「整块透传优先」路径（②整块提取即写入标准域），无需转换规则；
// 在表格配置中只读展示，避免用户误以为 current_package / recommended_packages 映射丢失。
const ftDirectDomains = computed(() => {
  const ext = _safeParse(ifcEditForm.response_extract)
  const tr = _safeParse(ifcEditForm.field_transform)
  const ftSlots = new Set(Object.keys(tr || {}).map(k => String(k).split('.')[0]))
  const out = []
  for (const [key, path] of Object.entries(ext || {})) {
    if (String(key).startsWith('_')) continue
    if (!STANDARD_SLOTS.has(key)) continue
    if (ftSlots.has(key)) continue
    out.push({ slot: key, path: String(path || '') })
  }
  return out
})
const ftSpecial = ref({})               // 下划线前缀的特殊配置（如 _unit_conversions），表格不展示为域行但需原样保留
let ftInternalWrite = false             // 防止表格↔JSON 循环同步

/** 把 field_transform JSON 解析为表格行（_ 开头的特殊配置抽到 ftSpecial，忠实保留其余未识别字段到 _rest）*/
function parseFtToRows() {
  const ft = _safeParse(ifcEditForm.field_transform)
  const rows = []
  const special = {}
  for (const [k, v] of Object.entries(ft || {})) {
    // _unit_conversions 等下划线前缀键是特殊配置，不作为标准域映射行展示
    if (String(k).startsWith('_')) { special[k] = v; continue }
    const [slotKey, ...sub] = String(k).split('.')
    const val = (v && typeof v === 'object') ? v : {}
    const type = val.type || 'passthrough'
    const keys = type === 'filter_include'
      ? (val.include_keys || [])
      : type === 'filter_exclude' ? (val.exclude_keys || []) : []
    const rest = { ...val }
    delete rest.from; delete rest.type; delete rest.include_keys; delete rest.exclude_keys
    rows.push({
      slotKey, subKey: sub.join('.'),
      from: typeof val.from === 'string' ? val.from : '',
      type, keys: [...keys], _rest: rest,
    })
  }
  ftSpecial.value = special
  ftRows.value = rows
  parseUnitRows()   // 同步刷新单位换算表格（713_3）
}

/** 表格行序列化回 field_transform JSON 字符串（合并回 ftSpecial 中保留的特殊配置）*/
function commitFtRows() {
  const ft = {}
  for (const r of ftRows.value) {
    if (!r.slotKey) continue
    const key = r.subKey ? `${r.slotKey}.${r.subKey}` : r.slotKey
    const val = { ...(r._rest || {}) }
    if (r.from) val.from = r.from
    val.type = r.type || 'passthrough'
    if (r.type === 'filter_include') val.include_keys = [...(r.keys || [])]
    else if (r.type === 'filter_exclude') val.exclude_keys = [...(r.keys || [])]
    ft[key] = val
  }
  for (const [k, v] of Object.entries(ftSpecial.value || {})) ft[k] = v
  ftInternalWrite = true
  ifcEditForm.field_transform = JSON.stringify(ft, null, 2)
}

// ── 优化3 + 713_3：_unit_conversions 单位换算规则表格化 ──────────────────
// 可选换算器（与 plugins/unit_converter.py 的 UnitConverterRegistry 注册表保持一致）
const UNIT_CONVERTERS = [
  { value: 'mb_to_gb',        label: 'MB→GB（数值）' },
  { value: 'mb_to_gb_str',    label: 'MB→GB（带单位 GB）' },
  { value: 'fen_to_yuan',     label: '分→元（数值）' },
  { value: 'fen_to_yuan_str', label: '分→元（带单位 元）' },
  { value: 'passthrough',     label: '不换算（透传）' },
]

// 单位换算表格行模型：{ target_path, field, new_field, converter, desc }
const ftUnitRows = ref([])

// 当前 field_transform 表格里可作为 target_path 的映射域键（含 slot.subKey 形式）
const ftRuleKeys = computed(() =>
  ftRows.value
    .map(r => (r.subKey ? `${r.slotKey}.${r.subKey}` : r.slotKey))
    .filter(Boolean)
)

/** 某 target_path 对应规则的候选字段（该规则的 include/exclude 字段），供「原字段」下拉 */
function ftUnitFieldOptions(targetPath) {
  const r = ftRows.value.find(x => (x.subKey ? `${x.slotKey}.${x.subKey}` : x.slotKey) === targetPath)
  return r ? [...(r.keys || [])] : []
}

/** 解析单位换算表格：合并 _unit_conversions 数组 与 各规则已有的 unit_convert/field_rename（并集，去重），
 *  保证老配置（无 _unit_conversions 数组、仅规则内含 unit_convert）也能被完整展示与编辑。*/
function parseUnitRows() {
  const map = new Map()   // key: `${target_path}||${field}`
  // 1) 先从各规则的 unit_convert/field_rename 建立基线（运行时真源）
  for (const r of ftRows.value) {
    const key = r.subKey ? `${r.slotKey}.${r.subKey}` : r.slotKey
    const uc = (r._rest && r._rest.unit_convert) || {}
    const fr = (r._rest && r._rest.field_rename) || {}
    for (const [field, converter] of Object.entries(uc)) {
      map.set(`${key}||${field}`, {
        target_path: key, field,
        new_field: fr[field] || field,
        converter: String(converter || 'passthrough'),
        desc: '',
      })
    }
  }
  // 2) 覆盖叠加 _unit_conversions 数组（携带 desc / 显式 new_field，为展示优先来源）
  const arr = ftSpecial.value && ftSpecial.value._unit_conversions
  if (Array.isArray(arr)) {
    for (const u of arr) {
      const tp = String(u.target_path || ''), fld = String(u.field || '')
      if (!tp || !fld) continue
      map.set(`${tp}||${fld}`, {
        target_path: tp, field: fld,
        new_field: String(u.new_field || u.field || fld),
        converter: String(u.converter || 'passthrough'),
        desc: String(u.desc || ''),
      })
    }
  }
  ftUnitRows.value = [...map.values()]
}

/** 规范化重命名字段名：折叠历史脏数据中的重复括号（如 "近3月平均流量((GB)）" → "近3月平均流量(GB)"）*/
function _cleanRenameField(name) {
  return String(name || '')
    .replace(/[(（]{2,}/g, '(')                 // 连续左括号折叠为一个半角 (
    .replace(/[)）]{2,}/g, ')')                 // 连续右括号折叠为一个半角 )
    .replace(/\(([^()（）]*)）/g, '($1)')        // 半角开 + 全角闭 → 统一半角
}

/** 单位换算表格 → 写回 _unit_conversions（展示镜像）+ 写穿透到各规则 unit_convert/field_rename（运行时真源）*/
function commitUnitRows() {
  const norm = ftUnitRows.value
    .filter(u => u.target_path && u.field && u.converter)
    .map(u => ({
      target_path: u.target_path,
      field: u.field,
      new_field: _cleanRenameField(u.new_field || u.field),
      converter: u.converter,
      desc: u.desc || '',
    }))

  // 1) _unit_conversions 展示数组
  const s = { ...ftSpecial.value }
  if (norm.length) s._unit_conversions = norm
  else delete s._unit_conversions
  ftSpecial.value = s

  // 2) 按 target_path 聚合，穿透写入每行 _rest.unit_convert / field_rename
  const byPath = {}
  for (const u of norm) {
    if (!byPath[u.target_path]) byPath[u.target_path] = { unit_convert: {}, field_rename: {} }
    byPath[u.target_path].unit_convert[u.field] = u.converter
    if (u.new_field && u.new_field !== u.field) byPath[u.target_path].field_rename[u.field] = u.new_field
  }
  for (const r of ftRows.value) {
    const key = r.subKey ? `${r.slotKey}.${r.subKey}` : r.slotKey
    const rest = { ...(r._rest || {}) }
    const grp = byPath[key]
    if (grp && Object.keys(grp.unit_convert).length) {
      rest.unit_convert = grp.unit_convert
      if (Object.keys(grp.field_rename).length) rest.field_rename = grp.field_rename
      else delete rest.field_rename
    } else {
      // 该映射域已无任何换算规则 → 清除历史 unit_convert/field_rename（删除生效）
      delete rest.unit_convert
      delete rest.field_rename
    }
    r._rest = rest
  }
  commitFtRows()
}

function addUnitRow() {
  const firstKey = ftRuleKeys.value[0] || ''
  const opts = ftUnitFieldOptions(firstKey)
  ftUnitRows.value.push({
    target_path: firstKey,
    field: opts[0] || '',
    new_field: '',
    converter: 'mb_to_gb',
    desc: '',
  })
  commitUnitRows()
}

function removeUnitRow(i) {
  ftUnitRows.value.splice(i, 1)
  commitUnitRows()
}

/** 切换换算器时，若字段名含单位标识则自动建议新字段名（MB→GB / 分→元），提升可用性 */
function onUnitConverterChange(row) {
  if (!row.new_field && row.field) {
    if (row.converter === 'mb_to_gb' || row.converter === 'mb_to_gb_str') {
      if (/MB/i.test(row.field)) row.new_field = row.field.replace(/MB/gi, 'GB')
    } else if (row.converter === 'fen_to_yuan' || row.converter === 'fen_to_yuan_str') {
      if (/分/.test(row.field)) row.new_field = row.field.replace(/分/g, '元')
    }
  }
  commitUnitRows()
}

function clearAllUnitRows() {
  ftUnitRows.value = []
  commitUnitRows()
  ElMessage.success('已清空全部单位换算规则')
}

// 优化4：字段转换表格体现「所有映射域」——列出 7 大标准域各自由哪条规则产出
const ftAllDomainRows = computed(() => {
  const ext = _safeParse(ifcEditForm.response_extract)
  const extKeys = new Set(Object.keys(ext || {}))
  const tfSlots = new Set(transformSlotsPreview.value)
  const produced = ifcEditProducedSlots.value
  return STANDARD_SLOT_LIST.map(s => {
    let source
    if (tfSlots.has(s.key)) source = 'field_transform 转换'
    else if (extKeys.has(s.key)) source = 'response_extract 直取'
    else if (produced.has(s.key)) source = isDirectMode.value ? 'extra_info 同名透传' : '直取'
    else source = '未映射'
    return { key: s.key, label: s.label, mapped: produced.has(s.key), source }
  })
})

function addFtRow() {
  const used = new Set(ftRows.value.map(r => r.slotKey))
  const next = STANDARD_SLOT_LIST.find(s => !used.has(s.key))
  ftRows.value.push({
    slotKey: next ? next.key : 'domain_ext',
    subKey: '', from: '', type: 'passthrough', keys: [], _rest: {},
  })
  commitFtRows()
}

function removeFtRow(i) {
  ftRows.value.splice(i, 1)
  commitFtRows()
}

/** 某行「来源数据块」对应的候选字段（从样例 JSON 取该数据块的字段名，用于勾选 include/exclude）*/
function ftFieldCandidates(row) {
  try {
    const ext = _safeParse(ifcEditForm.response_extract)
    const sample = _safeParse(ifcEditForm.mock_response)
    let block
    if (row.from && ext[row.from] !== undefined) block = _getByPath(sample, ext[row.from])
    else if (row.from) block = _getByPath(sample, row.from)
    if (Array.isArray(block)) block = block[0]
    if (block && typeof block === 'object') return Object.keys(block)
  } catch { /* ignore */ }
  return []
}

// 表格与 JSON 双向同步：外部（智能分析 / 高级模式）改动 field_transform 时刷新表格
watch(() => ifcEditForm.field_transform, () => {
  if (ftInternalWrite) { ftInternalWrite = false; return }
  if (ftVisualMode.value) parseFtToRows()
})
// 切回表格模式时，拾取用户在高级 JSON 里的改动
watch(ftVisualMode, (v) => { if (v) parseFtToRows() })
// 打开接口编辑弹窗时初始化表格
watch(ifcEditVisible, (v) => {
  if (v) {
    ftVisualMode.value = true; parseFtToRows()
    ifcAutoMapHasRun.value = false   // 每次打开重置，未分析且无已有映射时隐藏映射详情
  }
})

// === 数据流可视化辅助函数 ===
function _safeParse(s) { try { return JSON.parse(s || '{}') } catch { return {} } }
function _previewVal(v) {
  if (v === undefined) return '（未取到，可能样例 JSON 中没有该路径）'
  try {
    const s = JSON.stringify(v, null, 2)
    return s.length > 800 ? s.slice(0, 800) + '\n... (已截断)' : s
  } catch { return String(v) }
}
function _summarize(v) {
  if (v == null) return '空'
  if (Array.isArray(v)) return `数组（${v.length} 项）`
  if (typeof v === 'object') return `对象（${Object.keys(v).length} 个字段）`
  return String(typeof v)
}

// B 列：中间数据集（response_extract）每条规则一行
const omExtractFlowRows = computed(() => {
  const ext = _safeParse(ifcEditForm.response_extract)
  const preview = ifcAutoMapPreview.value || {}
  return Object.entries(ext)
    .filter(([key]) => !key.startsWith('_'))  // 过滤 _unit_conversions 等内部键
    .map(([key, path]) => ({
      key,
      path: String(path || ''),
      value: preview[key],
      summary: _summarize(preview[key]),
      preview: _previewVal(preview[key]),
    }))
})

// C 列：标准域（field_transform）每条规则一行
const TYPE_LABEL_OM = {
  passthrough:    '整块透传',
  filter_include: '只保留',
  filter_exclude: '排除字段',
}
/**
 * 基于一条 field_transform 规则 + 中间数据集，本地模拟出该标准域的实际值。
 * preview 来自后端 LLM 智能映射的 dry-run 结果（包含 response_extract 取出的中间数据集），
 * 也可能包含已经计算好的 slot 值；优先用规则本地计算，回退到 preview[slot]。
 */
function _applyTransformRule(slot, rule, preview, mockResp) {
  const r = rule || {}
  const type = r.type || 'passthrough'
  const fromName = r.from || slot
  // 取源数据集：优先 preview（已按 response_extract 取过）；若为空再从 mock_response 同路径取
  let src = preview && preview[fromName]
  if (src === undefined && mockResp) {
    const ext = _safeParse(ifcEditForm.response_extract)
    const path = ext[fromName]
    if (path) src = _getByPath(mockResp, path)
    // 向后兼容增强：from 直接是响应 JSON 路径（如 bean.tags）时，直接从原始响应取
    if (src === undefined) src = _getByPath(mockResp, fromName)
  }
  if (src === undefined) return undefined
  if (type === 'passthrough' || type === 'extract_only') return src
  if (type === 'filter_include' && Array.isArray(r.include_keys)) {
    if (!src || typeof src !== 'object' || Array.isArray(src)) return src
    const out = {}
    for (const k of r.include_keys) {
      if (src[k] !== undefined && src[k] !== null && src[k] !== '') out[k] = src[k]
    }
    return out
  }
  if (type === 'filter_exclude' && Array.isArray(r.exclude_keys)) {
    if (!src || typeof src !== 'object' || Array.isArray(src)) return src
    const exSet = new Set(r.exclude_keys)
    const out = {}
    for (const [k, v] of Object.entries(src)) {
      if (!exSet.has(k) && v !== null && v !== undefined && v !== '') out[k] = v
    }
    return out
  }
  return src
}

/**
 * 判定一条 field_transform 的 `from` 能否取到数据（与后端 DataStep._transform_fields
 * 及 lint E201 同一套规则，三种合法形态）：
 *   'declared' —— 命中 response_extract 已声明的数据集名（存量中间集写法）
 *   'direct'   —— 直接是响应 JSON 路径（如 bean.tags），运行时走路径回退取值
 *   'unknown'  —— 没有样例出参，无从判定，一律不报警（避免误伤）
 * 返回 'invalid' 才是真正取不到数据。
 *
 * 抽成公共函数是因为此前面板与保存前校验各写了一套且规则不一致：面板只认
 * response_extract 声明，直连写法被误报成「未声明」，而它其实能正常取到数据。
 */
function _classifyFrom(from, declared, sample) {
  if (!from || typeof from !== 'string') return 'declared'   // 省略 from = 同名，另有逻辑
  if (declared.has(from)) return 'declared'
  if (!sample || !Object.keys(sample).length) return 'unknown'
  return _getByPath(sample, from) !== undefined ? 'direct' : 'invalid'
}

// 直接匹配：response_extract 中 key 命中 7 大标准域，且没有 field_transform 规则覆盖
const omDirectMatchedRows = computed(() => {
  const ext = _safeParse(ifcEditForm.response_extract)
  const tr = _safeParse(ifcEditForm.field_transform)
  const preview = ifcAutoMapPreview.value || {}
  let mockResp = null
  try { mockResp = JSON.parse(ifcEditForm.mock_response || '{}') } catch { mockResp = null }
  const ftSlots = new Set(Object.keys(tr).map(k => String(k).split('.')[0]))
  const out = []
  for (const [key, path] of Object.entries(ext)) {
    if (key.startsWith('_')) continue  // 过滤 _unit_conversions 等内部键
    if (!STANDARD_SLOTS.has(key)) continue
    if (ftSlots.has(key)) continue  // 已被 field_transform 覆盖，归到下方
    let value = preview[key]
    if (value === undefined && mockResp) value = _getByPath(mockResp, path)
    out.push({
      slot: key,
      path: String(path || ''),
      summary: _summarize(value),
      preview: _previewVal(value),
    })
  }
  return out
})

const omTransformFlowRows = computed(() => {
  const tr = _safeParse(ifcEditForm.field_transform)
  const ext = _safeParse(ifcEditForm.response_extract)
  const declared = new Set(Object.keys(ext))
  const preview = ifcAutoMapPreview.value || {}
  // mock_response 作为兜底数据源（preview 为空时用）
  let mockResp = null
  try { mockResp = JSON.parse(ifcEditForm.mock_response || '{}') } catch { mockResp = null }
  // 多条 slot.sub 规则合并到顶层 slot
  const grouped = {}
  for (const [k, v] of Object.entries(tr)) {
    const top = String(k).split('.')[0]
    if (top.startsWith('_')) continue  // 过滤 _unit_conversions 等内部键
    const sub = k.includes('.') ? k.split('.').slice(1).join('.') : ''
    if (!grouped[top]) grouped[top] = []
    grouped[top].push({ sub_key: sub, ...(v || {}) })
  }
  return Object.entries(grouped).map(([slot, rules]) => {
    const main = rules[0]
    const type = main.type || 'passthrough'
    const from = main.from || slot
    // 省略 from 时运行时不走路径回退（语义是同名数据集），仍按声明与否判定
    const kind = main.from ? _classifyFrom(from, declared, mockResp) : (declared.has(from) ? 'declared' : 'unknown')
    const invalid = kind === 'invalid'
    let fieldDesc = ''
    if (type === 'filter_include' && Array.isArray(main.include_keys)) {
      fieldDesc = `只保留 ${main.include_keys.length} 个字段：${main.include_keys.slice(0,3).join(' / ')}${main.include_keys.length>3?' …':''}`
    } else if (type === 'filter_exclude' && Array.isArray(main.exclude_keys)) {
      fieldDesc = `排除 ${main.exclude_keys.length} 个字段：${main.exclude_keys.slice(0,3).join(' / ')}${main.exclude_keys.length>3?' …':''}`
    } else if (type === 'passthrough') {
      fieldDesc = '整块原样写入'
    }
    // 计算实际预览值
    let result
    if (rules.length === 1 && !main.sub_key) {
      result = _applyTransformRule(slot, main, preview, mockResp)
    } else {
      // 多子键规则：合成对象 { sub_key: 计算结果 }
      const merged = {}
      for (const r of rules) {
        const sub = r.sub_key || '_root'
        merged[sub] = _applyTransformRule(slot, r, preview, mockResp)
      }
      result = merged
    }
    if (result === undefined && preview[slot] !== undefined) result = preview[slot]
    return {
      slot,
      type,
      typeLabel: TYPE_LABEL_OM[type] || type,
      from,
      fieldDesc,
      invalid,
      direct: kind === 'direct',
      preview: _previewVal(result),
    }
  })
})

const transformInvalidFroms = computed(() => {

  try {
    const ft = JSON.parse(ifcEditForm.field_transform || '{}')
    const declared = new Set(extractKeysPreview.value)
    const sample = _safeParse(ifcEditForm.mock_response)
    const hasSample = sample && Object.keys(sample).length > 0
    const invalid = new Set()
    for (const v of Object.values(ft || {})) {
      const f = v?.from
      if (!f || typeof f !== 'string') continue
      // 与数据流面板共用判定，避免两套规则漂移（直连写法曾在面板被误报为「未声明」）
      if (_classifyFrom(f, declared, hasSample ? sample : null) === 'invalid') invalid.add(f)
    }
    return [...invalid]
  } catch { return [] }
})

// ── 新建接口 ─────────────────────────────────────────
// 已按需求删除「上传文档自动解析生成接口」功能，新建接口只保留手动填写，
// 「新建接口」按钮直接进入手动填写弹窗。
const ifcCreateModeVisible = ref(false)  // 已废弃（保留声明避免遗留引用报错）

function openIfcCreateMode() {
  // 模式 B（Import 创建页）→ 本地新建；模式 A → 后端新建
  if (!props.province || !props.intent) openLocalIfcCreate()
  else openIfcCreate()
}

function openIfcCreate() {
  ifcEditIsNew.value = true
  ifcEditTab.value   = 'basic'
  Object.assign(ifcEditForm, {
    api_name: '', description: '', url: '', method: 'POST',
    enabled: true, request_template: '', response_extract: '{}',
    field_transform: '{}', mock_mode: false, mock_response: '{}',
    // 需求：透传模式作为第一选择（默认），接口查询模式为第二选择
    source_type: 'direct',
    headers_pairs: [{ k: 'Content-Type', v: 'application/json' }],
  })
  ifcAutoMapAnalysis.value = ''
  ifcEditVisible.value = true
}

// ── Agent 文档解析 pipeline ───────────────────────────────
const AGENT_PIPELINE = [
  { id: 'parse_docx', title: 'parse_docx', desc: '解析 docx 文档，提取接口描述、URL、请求头、入参与出参表格结构' },
  { id: 'match_params', title: 'match_params', desc: '入参与主服务 FlowContext 占位符自动匹配（phone / extra_data.* 等）' },
  { id: 'map_output', title: 'map_output（规则引擎）', desc: '基于出参成功示例，按照预设规则生成 response_extract 与 field_transform 映射' },
  { id: 'detect_units', title: 'detect_units', desc: '扫描字段名/单位说明，注入 unit_convert 规则（MB→GB，分/角→元）' },
  { id: 'llm_map', title: 'LLM 智能辅助映射', desc: '由 LLM 分析出参结构并生成映射规则' },
]

const ifcAgentVisible          = ref(false)
const ifcAgentStep             = ref('upload')   // 'upload' | 'parsing' | 'review'
const ifcAgentFile             = ref(null)
const ifcAgentParsing          = ref(false)
const ifcAgentDropHighlight    = ref(false)
const ifcAgentFileInputRef     = ref(null)
const ifcAgentActivePipelineStep = ref(0)
const ifcAgentPreviewData      = ref(null)
const ifcOutMapLoading         = ref(false)
let   ifcAgentPipelineTimer    = null
let   ifcAgentFetchAbort       = null

const agentProgressPct = computed(() => {
  if (ifcAgentStep.value !== 'parsing') return 100
  return Math.min(92, Math.round(((ifcAgentActivePipelineStep.value + 1) / AGENT_PIPELINE.length) * 92))
})

// Review form state
const ifcReviewBasic         = reactive({ api_name: '', description: '', url: '', method: 'POST' })
const ifcReviewReqStr        = ref('{}')
const ifcReviewExtStr        = ref('{}')
const ifcReviewTrStr         = ref('{}')
const ifcReviewMockStr       = ref('{}')
const ifcReviewAnalysis      = ref('')
const ifcReviewParamMatches  = ref([])
const ifcReviewDomainResultStr = ref('')

function openIfcAgentModal() {
  ifcAgentStep.value = 'upload'
  ifcAgentFile.value = null
  ifcAgentParsing.value = false
  ifcAgentDropHighlight.value = false
  ifcAgentActivePipelineStep.value = 0
  ifcAgentPreviewData.value = null
  resetIfcReview()
  ifcAgentVisible.value = true
}

function resetIfcReview() {
  Object.assign(ifcReviewBasic, { api_name: '', description: '', url: '', method: 'POST' })
  ifcReviewReqStr.value = '{}'
  ifcReviewExtStr.value = '{}'
  ifcReviewTrStr.value  = '{}'
  ifcReviewMockStr.value = '{}'
  ifcReviewAnalysis.value = ''
  ifcReviewParamMatches.value = []
  ifcReviewDomainResultStr.value = ''
}

function triggerAgentFilePick() { ifcAgentFileInputRef.value?.click() }

function setAgentFile(file) {
  if (!file) { ifcAgentFile.value = null; return }
  if (!file.name.toLowerCase().endsWith('.docx')) { ElMessage.error('请上传 .docx 文件'); return }
  ifcAgentFile.value = file
}

function clearAgentPipelineTimer() {
  if (ifcAgentPipelineTimer) { clearInterval(ifcAgentPipelineTimer); ifcAgentPipelineTimer = null }
}

function startAgentPipelineTimer() {
  clearAgentPipelineTimer()
  ifcAgentActivePipelineStep.value = 0
  ifcAgentPipelineTimer = window.setInterval(() => {
    if (ifcAgentActivePipelineStep.value < AGENT_PIPELINE.length - 1)
      ifcAgentActivePipelineStep.value++
  }, 2600)
}

function agentBackToUpload() {
  ifcAgentStep.value = 'upload'
  ifcAgentFile.value = null
  resetIfcReview()
  if (ifcAgentFileInputRef.value) ifcAgentFileInputRef.value.value = ''
}

function cancelAgentParse() { ifcAgentFetchAbort?.abort() }

function fileToBase64Agent(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => {
      const s = r.result
      if (typeof s !== 'string') { reject(new Error('读取文件失败')); return }
      const i = s.indexOf(',')
      resolve(i >= 0 ? s.slice(i + 1) : s)
    }
    r.onerror = () => reject(r.error || new Error('读取文件失败'))
    r.readAsDataURL(file)
  })
}

function populateReviewFromPreview(d) {
  const b = d.basic_info || {}
  const rawName = (b.api_name || '').trim()
  const slug = rawName.replace(/\s+/g,'_').replace(/[^a-zA-Z0-9_]/g,'_')
    .replace(/_+/g,'_').replace(/^_|_$/g,'').toLowerCase()
  ifcReviewBasic.api_name    = slug || 'parsed_api'
  ifcReviewBasic.description = b.description || rawName || ''
  ifcReviewBasic.url         = b.url || ''
  ifcReviewBasic.method      = b.method || 'POST'
  ifcReviewReqStr.value  = JSON.stringify(d.request_template || {}, null, 2)
  ifcReviewExtStr.value  = JSON.stringify(d.response_extract || {}, null, 2)
  ifcReviewTrStr.value   = JSON.stringify(d.field_transform  || {}, null, 2)
  ifcReviewMockStr.value = JSON.stringify(d.success_example  || {}, null, 2)
  ifcReviewAnalysis.value = d.analysis || ''
  const dm = d.domain_mapping_preview
  ifcReviewDomainResultStr.value = (dm && typeof dm === 'object' && !Array.isArray(dm) && Object.keys(dm).length)
    ? JSON.stringify(dm, null, 2) : ''
  ifcReviewParamMatches.value = Array.isArray(d.param_matches)
    ? d.param_matches.map(x => {
        const mt = x.match_type
        return {
          ...x,
          match_type: (mt === 'direct' || mt === 'extra_data' || mt === 'unmatched') ? mt : 'unmatched',
          placeholder: x.placeholder != null ? String(x.placeholder) : '',
        }
      })
    : []
}

async function runAgentDocPreview() {
  if (!ifcAgentFile.value) return
  ifcAgentFetchAbort?.abort()
  ifcAgentFetchAbort = new AbortController()
  ifcAgentParsing.value = true
  ifcAgentStep.value = 'parsing'
  startAgentPipelineTimer()
  try {
    const docx_content_b64 = await fileToBase64Agent(ifcAgentFile.value)
    const res = await apiFetch('/api/parse_docx_preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ province: props.province, intent: props.intent, docx_content_b64 }),
      signal: ifcAgentFetchAbort.signal,
    })
    let json = {}
    try { json = await res.json() } catch { json = {} }
    if (res.ok && json.code === 0 && json.data) {
      ifcAgentPreviewData.value = json.data
      populateReviewFromPreview(json.data)
      ifcAgentActivePipelineStep.value = AGENT_PIPELINE.length - 1
      ifcAgentStep.value = 'review'
    } else {
      const errText = json.message || (typeof json.detail === 'string' ? json.detail : '') || '解析失败'
      ifcAgentStep.value = 'upload'
      ElMessage.error('❌ ' + errText)
    }
  } catch (e) {
    if (e?.name !== 'AbortError') ElMessage.error('❌ ' + (e.message || '解析失败'))
    ifcAgentStep.value = 'upload'
  } finally {
    clearAgentPipelineTimer()
    ifcAgentParsing.value = false
    ifcAgentFetchAbort = null
  }
}

function onReviewParamMatchTypeChange(row) {
  const path = row.api_param || ''
  const leaf = (path.split('.').pop() || path).toLowerCase().replace(/_/g, '')
  if (row.match_type === 'direct') {
    const m = { phone:'{{PHONE}}', intent:'{{INTENT}}', callid:'{{CALL_ID}}', province:'{{PROVINCE}}',
                topn:'{{TOP_N}}', ioid:'{{CALL_ID}}', taskid:'{{TASK_ID}}', sessionid:'{{CALL_ID}}' }
    row.placeholder = m[leaf] || row.placeholder
  } else if (row.match_type === 'extra_data') {
    row.placeholder = '{{extra_data.' + path + '}}'
  }
}

function syncReqTemplateFromParamRows() {
  try {
    const tpl = JSON.parse(ifcReviewReqStr.value || '{}')
    const setByPath = (obj, path, value) => {
      const keys = String(path || '').split('.').filter(Boolean)
      if (!keys.length) return
      let cur = obj
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]] || typeof cur[keys[i]] !== 'object') cur[keys[i]] = {}
        cur = cur[keys[i]]
      }
      cur[keys[keys.length - 1]] = value
    }
    for (const row of ifcReviewParamMatches.value) {
      if (row.api_param) setByPath(tpl, row.api_param, row.placeholder)
    }
    ifcReviewReqStr.value = JSON.stringify(tpl, null, 2)
  } catch { /* ignore */ }
}

async function runRegenMappingRules() {
  let mockRes, userDomain
  try { mockRes = JSON.parse(ifcReviewMockStr.value.trim() || '{}') } catch { ElMessage.error('出参示例 JSON 格式错误'); return }
  try { userDomain = JSON.parse(ifcReviewDomainResultStr.value.trim() || '{}') } catch { ElMessage.error('数据域结果 JSON 格式错误'); return }
  ifcOutMapLoading.value = true
  try {
    const res  = await apiFetch('/api/skills/refine_mapping_preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mock_response: mockRes, user_domain_result: userDomain,
        response_extract: JSON.parse(ifcReviewExtStr.value || '{}'),
        field_transform:  JSON.parse(ifcReviewTrStr.value  || '{}'),
      })
    })
    const json = await res.json()
    const ok = json.code === 0 || json.code === '0'
    if (res.ok && ok && json.data) {
      const d = json.data
      ifcReviewExtStr.value = JSON.stringify(d.response_extract ?? {}, null, 2)
      ifcReviewTrStr.value  = JSON.stringify(d.field_transform  ?? {}, null, 2)
      if (d.analysis) ifcReviewAnalysis.value = d.analysis
      const dr = d.domain_result
      ifcReviewDomainResultStr.value = JSON.stringify(dr != null ? dr : {}, null, 2)
      ElMessage.success('✅ 已根据期望结果重新生成规则')
    } else {
      ElMessage.error(json.detail || json.message || '生成失败')
    }
  } catch (e) { ElMessage.error(e.message || '请求失败') }
  finally { ifcOutMapLoading.value = false }
}

async function confirmAgentApply() {
  if (!ifcReviewBasic.api_name.trim()) { ElMessage.error('接口名称必填'); return }
  let reqTpl, resExt, fldTr, mockRes
  if (ifcReviewParamMatches.value.length) syncReqTemplateFromParamRows()
  try { reqTpl = JSON.parse(ifcReviewReqStr.value || '{}') } catch { ElMessage.error('request_template JSON 格式错误'); return }
  try { resExt = JSON.parse(ifcReviewExtStr.value  || '{}') } catch { ElMessage.error('response_extract JSON 格式错误'); return }
  try { fldTr  = JSON.parse(ifcReviewTrStr.value   || '{}') } catch { ElMessage.error('field_transform JSON 格式错误'); return }
  try { mockRes = JSON.parse(ifcReviewMockStr.value || '{}') } catch { ElMessage.error('Mock 示例 JSON 格式错误'); return }

  // 模式 B（Import 创建页）→ 解析结果写入本地 api_nodes
  if (!props.province || !props.intent) {
    const name = ifcReviewBasic.api_name.trim()
    if (apiNodeList.value.some(n => n._key === name)) {
      ElMessage.error(`接口名称「${name}」已存在`)
      return
    }
    apiNodeList.value.push({
      _key:                 name,
      _comment:             ifcReviewBasic.description.trim(),
      enabled:              true,
      url:                  ifcReviewBasic.url,
      method:               ifcReviewBasic.method || 'POST',
      headers:              reactive({ 'Content-Type': 'application/json' }),
      timeout:              30,
      max_retries:          2,
      mock_mode:            false,
      request_body_wrapper: '',
      request_template:     reqTpl,
      response_extract:     reactive({ ...resExt }),
      field_transform:      fldTr,
      mock_response:        mockRes,
      _extra:               {},
    })
    apiPage.value = Math.ceil(apiNodeList.value.length / API_PAGE_SIZE)
    ifcAgentVisible.value = false
    emitChange()
    ElMessage.success(`✅ 接口「${name}」已写入当前配置`)
    return
  }

  ifcSaving.value = true
  try {
    const body = {
      province: props.province, intent: props.intent,
      api_name: ifcReviewBasic.api_name.trim(),
      description: ifcReviewBasic.description.trim(),
      url: ifcReviewBasic.url, method: ifcReviewBasic.method,
      request_template: reqTpl, response_extract: resExt,
      field_transform: fldTr, mock_response: mockRes,
      success_example: ifcAgentPreviewData.value?.success_example || undefined,
    }
    const res  = await apiFetch('/api/interfaces/apply_parsed', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    })
    let json = {}
    try { json = await res.json() } catch { json = {} }
    const ok = res.ok && (json.code === 0 || json.code === 200)
    if (ok) {
      ifcAgentVisible.value = false
      ElMessage.success(json.message || '✅ 接口配置已写入')
      await loadIfcItems()
    } else {
      ElMessage.error(json.message || (typeof json.detail === 'string' ? json.detail : '') || '保存失败')
    }
  } catch (e) { ElMessage.error(e.message || '保存失败') }
  finally { ifcSaving.value = false }
}

async function openIfcEdit(item) {
  ifcEditIsNew.value = false
  ifcEditTab.value   = 'basic'
  try {
    const res  = await apiFetch(`/api/interfaces/${item.province}/${item.intent}/${item.api_name}`)
    const json = await res.json()
    const cfg  = json.data || {}
    Object.assign(ifcEditForm, {
      api_name:         item.api_name,
      description:      cfg._comment || cfg.description || '',
      url:              cfg.url || '',
      method:           cfg.method || 'POST',
      enabled:          cfg.enabled !== false,
      request_template: JSON.stringify(cfg.request_template || {}, null, 2),
      response_extract: JSON.stringify(cfg.response_extract || {}, null, 2),
      field_transform:  JSON.stringify(cfg.field_transform  || {}, null, 2),
      mock_mode:        cfg.mock_mode || false,
      mock_response:    JSON.stringify(cfg.mock_response    || {}, null, 2),
      source_type:      cfg.source_type === 'direct' ? 'direct' : 'api',
      direct_mode:      cfg.direct_mode === 'passthrough' ? 'passthrough' : 'mapping',
      passthrough_fields: Array.isArray(cfg.passthrough_fields) ? [...cfg.passthrough_fields] : [],
      headers_pairs:    headersObjToPairs(cfg.headers),
    })
  } catch { /* 允许打开空表单 */ }
  // 缓存原始快照（用于保存时判断是否有修改）
  ifcEditOriginalSnapshot.value = {
    description:      ifcEditForm.description,
    url:              ifcEditForm.url,
    method:           ifcEditForm.method,
    enabled:          ifcEditForm.enabled,
    request_template: ifcEditForm.request_template,
    response_extract: ifcEditForm.response_extract,
    field_transform:  ifcEditForm.field_transform,
    mock_mode:        ifcEditForm.mock_mode,
    mock_response:    ifcEditForm.mock_response,
    source_type:      ifcEditForm.source_type,
    direct_mode:      ifcEditForm.direct_mode,
    passthrough_fields: [...ifcEditForm.passthrough_fields],
    headers:          JSON.stringify(headersPairsToObj(ifcEditForm.headers_pairs)),
  }
  rebuildIfcEditPreview()
  ifcEditVisible.value = true
}

/** 用已有 mock_response 作为分析样例，基于已保存的 response_extract / field_transform
 *  本地重建"中间数据集"和"标准域"预览（模式 A / 模式 B 共用）*/
function rebuildIfcEditPreview() {
  ifcAutoMapAnalysis.value = ''
  try {
    const mock = JSON.parse(ifcEditForm.mock_response || '{}')
    const ext  = JSON.parse(ifcEditForm.response_extract || '{}')
    const ft   = JSON.parse(ifcEditForm.field_transform  || '{}')
    if ((ext && Object.keys(ext).length) || (ft && Object.keys(ft).length)) {
      const preview = {}
      // B：中间数据集
      const datasets = {}
      for (const [k, p] of Object.entries(ext || {})) {
        const v = _getByPath(mock, p)
        datasets[k] = v
        preview[k] = v
      }
      // C：标准域
      for (const [slot, rule] of Object.entries(ft || {})) {
        const top = String(slot).split('.')[0]
        const fromName = rule?.from || top
        const src = datasets[fromName]
        let val = src
        if (rule?.type === 'filter_include' && Array.isArray(rule.include_keys) && src && typeof src === 'object' && !Array.isArray(src)) {
          val = {}
          for (const k of rule.include_keys) {
            if (src[k] !== undefined && src[k] !== null && src[k] !== '') val[k] = src[k]
          }
        } else if (rule?.type === 'filter_exclude' && Array.isArray(rule.exclude_keys) && src && typeof src === 'object' && !Array.isArray(src)) {
          val = {}
          const ex = new Set(rule.exclude_keys)
          for (const [k, v] of Object.entries(src)) {
            if (!ex.has(k) && v !== null && v !== undefined && v !== '') val[k] = v
          }
        }
        preview[top] = val
      }
      ifcAutoMapPreview.value = preview
      ifcAutoMapHasRun.value = true
    } else {
      ifcAutoMapPreview.value = null
      ifcAutoMapHasRun.value = false
    }
  } catch {
    ifcAutoMapPreview.value = null
    ifcAutoMapHasRun.value = false
  }
}


// 比较两个 JSON 字符串是否语义相等（忽略空格、键顺序）
function _jsonEqual(a, b) {
  try { return JSON.stringify(JSON.parse(a || '{}')) === JSON.stringify(JSON.parse(b || '{}')) }
  catch { return (a || '') === (b || '') }
}

async function saveIfcEdit() {
  if (!ifcEditForm.api_name.trim()) {
    ElMessage.error(isDirectMode.value ? '节点名称必填' : '接口名称必填')
    return
  }
  let req = {}, ext = {}, tr = {}, mock = {}
  try { req  = JSON.parse(ifcEditForm.request_template || '{}') } catch { ElMessage.error('请求模板 JSON 格式错误'); return }
  try { ext  = JSON.parse(ifcEditForm.response_extract || '{}') } catch { ElMessage.error('response_extract JSON 格式错误'); return }
  try { tr   = JSON.parse(ifcEditForm.field_transform  || '{}') } catch { ElMessage.error('field_transform JSON 格式错误'); return }
  try { mock = JSON.parse(ifcEditForm.mock_response    || '{}') } catch { ElMessage.error('mock_response JSON 格式错误'); return }

  // 编辑模式下：判断是否有修改，无修改则跳过 PUT
  const snap = ifcEditOriginalSnapshot.value
  if (!ifcEditIsNew.value && snap) {
    const unchanged =
      snap.description === ifcEditForm.description &&
      snap.url === ifcEditForm.url &&
      snap.method === ifcEditForm.method &&
      snap.enabled === ifcEditForm.enabled &&
      snap.mock_mode === ifcEditForm.mock_mode &&
      (snap.source_type || 'api') === ifcEditForm.source_type &&
      (snap.direct_mode || 'mapping') === ifcEditForm.direct_mode &&
      JSON.stringify(snap.passthrough_fields || []) === JSON.stringify(ifcEditForm.passthrough_fields || []) &&
      (snap.headers || '{}') === JSON.stringify(headersPairsToObj(ifcEditForm.headers_pairs)) &&
      _jsonEqual(snap.request_template, ifcEditForm.request_template) &&
      _jsonEqual(snap.response_extract, ifcEditForm.response_extract) &&
      _jsonEqual(snap.field_transform,  ifcEditForm.field_transform) &&
      _jsonEqual(snap.mock_response,    ifcEditForm.mock_response)
    if (unchanged) {
      ifcEditVisible.value = false
      ElMessage.info('未检测到修改，已关闭')
      return
    }
  }

  // 模式 B（无 province/intent，Import 创建页）→ 保存到本地 api_nodes
  if (!props.province || !props.intent) {
    saveLocalIfcEdit(req, ext, tr, mock)
    return
  }

  const isDirect = isDirectMode.value
  const isPass = isPassthrough.value
  const body = {
    _comment: ifcEditForm.description,
    url: isDirect ? '' : ifcEditForm.url,
    method: ifcEditForm.method, enabled: ifcEditForm.enabled,
    source_type: ifcEditForm.source_type,
    // 透传子模式不写 7 域映射规则，改用 passthrough_fields 暴露入参字段
    request_template: isDirect ? {} : req,
    response_extract: isPass ? {} : ext,
    field_transform: isPass ? {} : tr,
    mock_mode: isDirect ? false : ifcEditForm.mock_mode,
    mock_response: mock,
  }
  // 接口查询模式：写入请求头（直传模式不发 HTTP，headers 不参与，交由后端合并保留原值）
  if (!isDirect) body.headers = headersPairsToObj(ifcEditForm.headers_pairs)
  if (isDirect) {
    body.direct_mode = ifcEditForm.direct_mode
    body.passthrough_fields = isPass ? [...ifcEditForm.passthrough_fields] : []
  }
  ifcSaving.value = true
  try {
    const res  = await apiFetch(
      `/api/interfaces/${props.province}/${props.intent}/${ifcEditForm.api_name}`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    )
    const json = await res.json()
    if (json.code === 200) {
      ifcEditVisible.value = false
      // 后端保存时会补齐残缺映射、把 raw_xxx 中间集转成直连写法，改了什么必须让配置人员看见
      const filled = json.autofilled || []
      if (filled.length) {
        ElMessage.success({ message: `✅ 保存成功，配置已自动修正：${filled.join('；')}`, duration: 6000 })
      } else {
        ElMessage.success('✅ 保存成功')
      }
      if ((json.unfixed || []).length) {
        ElMessage.warning({ message: `⚠️ 以下问题需人工处理：${json.unfixed.join('；')}`, duration: 8000 })
      }
      await loadIfcItems()
    } else {
      ElMessage.error(json.detail || json.message || '保存失败')
    }
  } catch (e) { ElMessage.error(e.message) }
  finally { ifcSaving.value = false }
}

async function runIfcAutoMap() {
  let sample
  try { sample = JSON.parse((ifcEditForm.mock_response || '').trim() || '{}') } catch { ElMessage.error('样例 JSON 格式错误'); return }
  ifcAutoMapLoading.value  = true
  ifcAutoMapAnalysis.value = ''
  try {
    const res  = await apiFetch('/api/interfaces/auto_map', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sample_response: sample })
    })
    const json = await res.json()
    if (json.code === 200 && json.data) {
      const ext = json.data.response_extract ?? {}
      const ft  = json.data.field_transform  ?? {}
      ifcEditForm.response_extract = JSON.stringify(ext, null, 2)
      ifcEditForm.field_transform  = JSON.stringify(ft, null, 2)
      ifcAutoMapAnalysis.value = json.data.analysis || ''
      // 后端 preview 可能只含标准域结果，缺失中间数据集；此处用样例 JSON 本地补齐，
      // 保证「中间数据集」block 立即能看到 raw_tags 等真实值
      const preview = { ...(json.data.preview || {}) }
      for (const [k, p] of Object.entries(ext)) {
        if (preview[k] === undefined) preview[k] = _getByPath(sample, p)
      }
      ifcAutoMapPreview.value  = preview
      ifcAutoMapHasRun.value   = true
      ElMessage.success('✅ 已生成映射规则，下方"中间数据集"和"标准域"已自动填充')
    } else {
      ElMessage.error(json.detail || json.message || '分析失败')
    }

  } catch (e) { ElMessage.error(e.message) }
  finally { ifcAutoMapLoading.value = false }
}

async function toggleIfcEnabled(item, checked) {
  try {
    const res  = await apiFetch(
      `/api/interfaces/${item.province}/${item.intent}/${item.api_name}/status`,
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: checked }) }
    )
    const json = await res.json()
    if (json.code === 200) { ElMessage.success('状态已更新'); await loadIfcItems() }
    else ElMessage.error(json.detail || json.message || '更新失败')
  } catch (e) { ElMessage.error(e.message); await loadIfcItems() }
}

// ── 智能映射弹窗（与 MappingConfig 对齐：双栏样例 + 重新执行/重新生成 + 保存）─
const ifcAutoMapVisible    = ref(false)
const ifcMapItem           = ref(null)
const ifcMapSample         = ref('')
const ifcMapLoading        = ref(false)
const ifcMapResult         = ref(null)
// 与 MappingConfig 对齐的状态
const autoMapDomainResultStr = ref('')
const autoMapAnalysis        = ref('')
const autoMapExtractStr      = ref('{}')
const autoMapTransformStr    = ref('{}')
const ifcMapRefineLoading    = ref(false)
const ifcMapRefineMode       = ref('')   // 'preview' | 'refine'

function openIfcAutoMap(item) {
  ifcMapItem.value          = item
  ifcMapSample.value        = ''
  ifcMapResult.value        = null
  autoMapDomainResultStr.value = ''
  autoMapAnalysis.value     = ''
  autoMapExtractStr.value   = '{}'
  autoMapTransformStr.value = '{}'
  ifcMapRefineLoading.value = false
  ifcMapRefineMode.value    = ''
  ifcAutoMapVisible.value   = true
}

async function runIfcMap() {
  let sample
  try { sample = JSON.parse(ifcMapSample.value.trim()) }
  catch { ElMessage.error('样例 JSON 格式错误'); return }
  if (!sample || typeof sample !== 'object' || Array.isArray(sample)) {
    ElMessage.error('请粘贴 JSON 对象作为响应样例'); return
  }
  ifcMapLoading.value = true
  ifcMapResult.value  = null
  autoMapDomainResultStr.value = ''
  autoMapAnalysis.value = ''
  try {
    const res  = await apiFetch('/api/interfaces/auto_map', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sample_response: sample })
    })
    const json = await res.json()
    if (json.code === 200 && json.data) {
      ifcMapResult.value        = json.data
      autoMapExtractStr.value   = JSON.stringify(json.data.response_extract ?? {}, null, 2)
      autoMapTransformStr.value = JSON.stringify(json.data.field_transform  ?? {}, null, 2)
      autoMapAnalysis.value     = json.data.analysis || ''
      ElMessage.success('✅ 已生成映射规则，正在预览数据域结果…')
      await runAutoMapPreview()
    } else {
      ElMessage.error(json.detail || json.message || '分析失败')
    }
  } catch (e) { ElMessage.error(e.message) }
  finally { ifcMapLoading.value = false }
}

/** 用当前 extract+transform 对样例执行映射，预览 7 大域结果 */
async function runAutoMapPreview() {
  if (!ifcMapItem.value) { ElMessage.error('缺少接口信息'); return }
  let mockRes, resExt, fldTr
  try { mockRes = JSON.parse(ifcMapSample.value.trim() || '{}') }
  catch { ElMessage.error('出参样例 JSON 格式错误'); return }
  try { resExt = JSON.parse(autoMapExtractStr.value || '{}') }
  catch { ElMessage.error('response_extract JSON 格式错误'); return }
  try { fldTr = JSON.parse(autoMapTransformStr.value || '{}') }
  catch { ElMessage.error('field_transform JSON 格式错误'); return }
  const { province, intent } = ifcMapItem.value
  ifcMapRefineLoading.value = true
  ifcMapRefineMode.value    = 'preview'
  try {
    const res = await apiFetch('/api/skills/preview_mapping', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ province, intent, mock_response: mockRes,
        response_extract: resExt, field_transform: fldTr }),
    })
    let json = {}; try { json = await res.json() } catch {}
    const ok = json.code === 0 || json.code === '0'
    if (res.ok && ok && json.data) {
      const dr = json.data.domain_result
      autoMapDomainResultStr.value = JSON.stringify(dr != null ? dr : {}, null, 2)
      ElMessage.success('✅ 数据域映射结果已更新')
    } else {
      ElMessage.error(json.detail || json.message || '预览失败')
    }
  } catch (e) { ElMessage.error(e.message || String(e)) }
  finally { ifcMapRefineLoading.value = false; ifcMapRefineMode.value = '' }
}

/** 根据用户修改后的期望结果，反向重新生成 extract+transform */
async function runAutoMapRefine() {
  let mockRes, userDomain, resExt, fldTr
  try { mockRes = JSON.parse(ifcMapSample.value.trim() || '') }
  catch { ElMessage.error('出参样例 JSON 格式错误'); return }
  if (!mockRes || typeof mockRes !== 'object' || Array.isArray(mockRes)) {
    ElMessage.error('出参样例请粘贴 JSON 对象'); return
  }
  try { userDomain = JSON.parse(autoMapDomainResultStr.value.trim() || '{}') }
  catch { ElMessage.error('数据域映射结果 JSON 格式错误'); return }
  if (!userDomain || typeof userDomain !== 'object' || Array.isArray(userDomain)) {
    ElMessage.error('数据域结果应为 JSON 对象'); return
  }
  try { resExt = JSON.parse(autoMapExtractStr.value || '{}') } catch { resExt = {} }
  try { fldTr = JSON.parse(autoMapTransformStr.value || '{}') } catch { fldTr = {} }
  ifcMapRefineLoading.value = true
  ifcMapRefineMode.value    = 'refine'
  try {
    const res = await apiFetch('/api/skills/refine_mapping_preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mock_response: mockRes, user_domain_result: userDomain,
        response_extract: resExt, field_transform: fldTr }),
    })
    let json = {}; try { json = await res.json() } catch {}
    const ok = json.code === 0 || json.code === '0'
    if (res.ok && ok && json.data) {
      const d = json.data
      autoMapExtractStr.value   = JSON.stringify(d.response_extract ?? {}, null, 2)
      autoMapTransformStr.value = JSON.stringify(d.field_transform  ?? {}, null, 2)
      if (d.analysis) autoMapAnalysis.value = d.analysis
      const dr = d.domain_result
      autoMapDomainResultStr.value = JSON.stringify(dr != null ? dr : {}, null, 2)
      // 同步到 ifcMapResult，触发"应用并保存"按钮显示
      ifcMapResult.value = {
        response_extract: d.response_extract ?? {},
        field_transform:  d.field_transform  ?? {},
        analysis: d.analysis || autoMapAnalysis.value,
      }
      ElMessage.success('✅ 已重新生成规则，请确认数据域结果后保存')
    } else {
      ElMessage.error(json.detail || json.message || '生成失败')
    }
  } catch (e) { ElMessage.error(e.message || String(e)) }
  finally { ifcMapRefineLoading.value = false; ifcMapRefineMode.value = '' }
}

async function applyIfcMap() {
  if (!ifcMapResult.value || !ifcMapItem.value) return
  const { province, intent, api_name } = ifcMapItem.value
  // 优先用文本框中（用户可能编辑过）的最新规则
  let resExt, fldTr
  try { resExt = JSON.parse(autoMapExtractStr.value || '{}') }
  catch { ElMessage.error('response_extract JSON 格式错误'); return }
  try { fldTr = JSON.parse(autoMapTransformStr.value || '{}') }
  catch { ElMessage.error('field_transform JSON 格式错误'); return }
  ifcSaving.value = true
  try {
    const res  = await apiFetch(`/api/interfaces/${province}/${intent}/${api_name}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response_extract: resExt, field_transform: fldTr })
    })
    const json = await res.json()
    if (json.code === 200) {
      ifcAutoMapVisible.value = false
      ElMessage.success('✅ 映射规则已应用')
      await loadIfcItems()
    } else {
      ElMessage.error(json.detail || json.message || '保存失败')
    }
  } catch (e) { ElMessage.error(e.message) }
  finally { ifcSaving.value = false }
}

// ── 删除弹窗 ─────────────────────────────────────────────
const ifcDelVisible = ref(false)
const ifcDelItem    = ref(null)
const ifcDeleting   = ref(false)

function openIfcDel(item)  { ifcDelItem.value = item; ifcDelVisible.value = true }

async function confirmIfcDel() {
  if (!ifcDelItem.value) return
  const { province, intent, api_name } = ifcDelItem.value
  ifcDeleting.value = true
  try {
    const res  = await apiFetch(`/api/interfaces/${province}/${intent}/${api_name}`, { method: 'DELETE' })
    const json = await res.json()
    if (json.code === 200) {
      ifcDelVisible.value = false
      ElMessage.success('✅ 删除成功')
      await loadIfcItems()
    } else {
      ElMessage.error(json.detail || json.message || '删除失败')
    }
  } catch (e) { ElMessage.error(e.message) }
  finally { ifcDeleting.value = false }
}

// ── 接口节点分页 & 搜索（模式 B 使用）────────────────────
const API_PAGE_SIZE = 20
const apiSearch = ref('')
const apiPage   = ref(1)

const filteredApiNodes = computed(() => {
  const q = apiSearch.value.trim().toLowerCase()
  if (!q) return apiNodeList.value
  return apiNodeList.value.filter(n =>
    (n._key || '').toLowerCase().includes(q) ||
    (n.url   || '').toLowerCase().includes(q)
  )
})

const pagedApiNodes = computed(() => {
  const start = (apiPage.value - 1) * API_PAGE_SIZE
  return filteredApiNodes.value.slice(start, start + API_PAGE_SIZE)
})

// ── 接口节点编辑弹窗 ───────────────────────────────────
// ── 模式 B：本地接口 新增/编辑（复用模式 A 三步弹窗，保存写入本地 api_nodes）──
let _localIfcEditingIdx = -1

function openLocalIfcCreate() {
  ifcEditIsNew.value = true
  ifcEditTab.value   = 'basic'
  _localIfcEditingIdx = -1
  Object.assign(ifcEditForm, {
    api_name: '', description: '', url: '', method: 'POST',
    enabled: true,
    request_template: '{\n  "phone": "{{PHONE}}",\n  "province": "{{PROVINCE}}"\n}',
    response_extract: '{}',
    field_transform: '{}', mock_mode: false, mock_response: '{}',
    // 需求：透传模式作为第一选择（默认），接口查询模式为第二选择
    source_type: 'direct',
    direct_mode: 'passthrough', passthrough_fields: [],
    headers_pairs: [{ k: 'Content-Type', v: 'application/json' }],
  })
  ifcEditOriginalSnapshot.value = null
  ifcAutoMapAnalysis.value = ''
  ifcAutoMapPreview.value  = null
  ifcAutoMapHasRun.value   = false
  ifcEditVisible.value = true
}

function openLocalIfcEdit(row) {
  ifcEditIsNew.value = false
  ifcEditTab.value   = 'basic'
  _localIfcEditingIdx = apiNodeList.value.indexOf(row)
  Object.assign(ifcEditForm, {
    api_name:         row._key || '',
    description:      row._comment || row.description || '',
    url:              row.url || '',
    method:           row.method || 'POST',
    enabled:          row.enabled !== false,
    request_template: JSON.stringify(row.request_template || {}, null, 2),
    response_extract: JSON.stringify(row.response_extract || {}, null, 2),
    field_transform:  JSON.stringify(row.field_transform  || {}, null, 2),
    mock_mode:        !!row.mock_mode,
    mock_response:    JSON.stringify(row.mock_response    || {}, null, 2),
    source_type:      row.source_type === 'direct' ? 'direct' : 'api',
    direct_mode:      row.direct_mode === 'passthrough' ? 'passthrough' : 'mapping',
    passthrough_fields: Array.isArray(row.passthrough_fields) ? [...row.passthrough_fields] : [],
    headers_pairs:    headersObjToPairs(row.headers),
  })
  ifcEditOriginalSnapshot.value = {
    description:      ifcEditForm.description,
    url:              ifcEditForm.url,
    method:           ifcEditForm.method,
    enabled:          ifcEditForm.enabled,
    request_template: ifcEditForm.request_template,
    response_extract: ifcEditForm.response_extract,
    field_transform:  ifcEditForm.field_transform,
    mock_mode:        ifcEditForm.mock_mode,
    mock_response:    ifcEditForm.mock_response,
    headers:          JSON.stringify(headersPairsToObj(ifcEditForm.headers_pairs)),
  }
  rebuildIfcEditPreview()
  ifcEditVisible.value = true
}

/** 本地模式保存：写回 apiNodeList 并 emitChange（被 saveIfcEdit 调用） */
function saveLocalIfcEdit(req, ext, tr, mock) {
  const name = ifcEditForm.api_name.trim()
  const old  = _localIfcEditingIdx >= 0 ? apiNodeList.value[_localIfcEditingIdx] : null
  if (ifcEditIsNew.value && apiNodeList.value.some(n => n._key === name)) {
    ElMessage.error(`接口名称「${name}」已存在`)
    return
  }
  const isDirect = ifcEditForm.source_type === 'direct'
  const isPass = isDirect && ifcEditForm.direct_mode === 'passthrough'
  const saved = {
    _key:                 name,
    _comment:             ifcEditForm.description,
    enabled:              ifcEditForm.enabled,
    url:                  isDirect ? '' : ifcEditForm.url,
    method:               ifcEditForm.method,
    source_type:          ifcEditForm.source_type,
    // 接口查询模式用表单编辑的请求头；直传模式不发 HTTP，沿用原值/默认
    headers:              reactive(isDirect
                            ? { ...(old?.headers || { 'Content-Type': 'application/json' }) }
                            : headersPairsToObj(ifcEditForm.headers_pairs)),
    timeout:              old?.timeout ?? 30,
    max_retries:          old?.max_retries ?? 2,
    mock_mode:            isDirect ? false : ifcEditForm.mock_mode,
    request_body_wrapper: old?.request_body_wrapper || '',
    request_template:     isDirect ? {} : req,
    response_extract:     reactive({ ...(isPass ? {} : ext) }),
    field_transform:      isPass ? {} : tr,
    mock_response:        mock,
    _extra:               old?._extra || {},
  }
  if (isDirect) {
    saved.direct_mode = ifcEditForm.direct_mode
    if (isPass) saved.passthrough_fields = [...ifcEditForm.passthrough_fields]
  }
  if (ifcEditIsNew.value) {
    apiNodeList.value.push(saved)
    apiPage.value = Math.ceil(apiNodeList.value.length / API_PAGE_SIZE)
  } else if (_localIfcEditingIdx >= 0) {
    apiNodeList.value[_localIfcEditingIdx] = saved
  }
  ifcEditVisible.value = false
  emitChange()
  ElMessage.success('✅ 已保存到当前配置')
}

function removeNode(idx) {
  apiNodeList.value.splice(idx, 1)
  emitChange()
}

// ── 模板分页 & 搜索 ────────────────────────────────────
const TBL_PAGE_SIZE = 20
// 筛选条件：产品 ID（精确，可下拉/输入过滤）/ 场景分类名称（模糊）/ 环节（精确）/ 意图（场景，精确）
const tplFilterProductId = ref('')
const tplFilterName  = ref('')
const tplFilterStage = ref('')
const tplFilterScene = ref('')
const tplPage    = ref(1)

// 「产品 ID」下拉可选项：来自当前模板列表全部产品 ID 去重（兼容分组行的多产品）
const tplProductIdOptions = computed(() => {
  const set = new Set()
  for (const t of templateList.value) {
    for (const p of rowProductIds(t)) set.add(p)
  }
  return [...set].sort()
})

// 「环节」下拉可选项：来自当前模板列表去重
const tplStageOptions = computed(() => {
  const set = new Set()
  for (const t of templateList.value) {
    if (t.stage) set.add(t.stage)
  }
  return [...set]
})
// 「意图」（scene 字段）下拉可选项：来自当前模板列表去重
const tplSceneOptions = computed(() => {
  const set = new Set()
  for (const t of templateList.value) {
    if (t.scene) set.add(t.scene)
  }
  return [...set]
})

const filteredTemplates = computed(() => {
  // 过滤空行（template_name / template_content / stage 全空时跳过）
  const nonEmpty = templateList.value.filter(t => !!(t.template_name || t.template_content || t.stage))
  const pid   = tplFilterProductId.value.trim()
  const name  = tplFilterName.value.trim().toLowerCase()
  const stage = tplFilterStage.value
  const scene = tplFilterScene.value
  return nonEmpty.filter(t =>
    (!pid   || rowProductIds(t).includes(pid)) &&
    (!name  || (t.template_name || '').toLowerCase().includes(name)) &&
    (!stage || (t.stage || '') === stage) &&
    (!scene || (t.scene || '') === scene)
  )
})

const pagedTemplates = computed(() => {
  const start = (tplPage.value - 1) * TBL_PAGE_SIZE
  return filteredTemplates.value.slice(start, start + TBL_PAGE_SIZE)
})

// ── 模式A：从后端加载话术模板 ─────────────────────────────
const tplLoading = ref(false)

/**
 * 按 (template_name, stage, scene, template_content) 分组合并同语义模板，
 * 将不同 product_id 合并到一个分组行。
 * 与 TemplateConfig 的 mergeByGroup 行为保持一致。
 */
function _mergeTplByGroup(items) {
  const map = new Map()
  for (const t of items) {
    const tname = t.template_name || t.intent || ''
    const key = [tname, t.stage || '', t.scene || '', t.template_content || ''].join('\x00')
    if (!map.has(key)) {
      map.set(key, {
        _uid:               t.template_id || String(_uid++),
        template_id:        t.template_id || '',
        template_name:      tname,
        stage:              t.stage || '',
        scene:              t.scene || '',
        product_id:         t.product_id || '',
        template_content:   t.template_content || '',
        prompt_template:    t.prompt_template || '',
        script_requirement: t.script_requirement || '',
        linked_vars:        Array.isArray(t.linked_vars) ? [...t.linked_vars] : [],
        linked_apis:        Array.isArray(t.linked_apis) ? [...t.linked_apis] : [],
        status:             t.status || 'online',
        created_by:         t.created_by || '',
        _product_ids:       t.product_id ? [t.product_id] : [''],
        _template_ids:      [t.template_id],
        _pid_to_tid:        { [t.product_id || '']: t.template_id },
      })
    } else {
      const g = map.get(key)
      const pid = t.product_id || ''
      if (!g._product_ids.includes(pid)) g._product_ids.push(pid)
      g._template_ids.push(t.template_id)
      g._pid_to_tid[pid] = t.template_id
      // 关联接口取并集（任一记录若声明了关联接口，则保留）
      for (const a of (t.linked_apis || [])) {
        if (!g.linked_apis.includes(a)) g.linked_apis.push(a)
      }
    }
  }
  return [...map.values()]
}

async function loadTplItems() {
  if (!props.province || !props.intent) return
  tplLoading.value = true
  try {
    // 分页拉全量后再合并：单意图下模板可达数千条（每产品一条），
    // 固定 page_size 会截断导致「合并归类」不完整，这里循环取到 total 为止
    const PAGE = 500
    const base = `/api/templates?province=${encodeURIComponent(props.province)}&intent=${encodeURIComponent(props.intent)}`
    let page = 1
    let items = []
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const res  = await apiFetch(`${base}&page=${page}&page_size=${PAGE}`)
      const json = await res.json()
      const data = json.data || {}
      const batch = Array.isArray(data.items) ? data.items : (Array.isArray(data) ? data : [])
      items = items.concat(batch)
      const total = typeof data.total === 'number' ? data.total : items.length
      if (batch.length === 0 || items.length >= total || page > 50) break
      page++
    }
    templateList.value = _mergeTplByGroup(items)
  } catch (e) {
    console.warn('loadTplItems failed:', e)
  } finally {
    tplLoading.value = false
  }
}

watch(() => [props.province, props.intent], ([p, i]) => {
  if (p && i) loadTplItems()
}, { immediate: true })

// ── 模板编辑弹窗 ───────────────────────────────────────
const tplDialogVisible = ref(false)
const tplDialogIsNew   = ref(false)
const tplEditing       = ref(null)
let   _editingIdx      = -1
const tplSaving        = ref(false)

function openAddTemplate() {
  ensureIfcDetails()
  const uid = String(_uid++)
  tplEditing.value = {
    _uid: uid, template_id: '', template_name: props.intent || '',
    province: props.province || '', intent: props.intent || '',
    stage: '', scene: '', product_id: '',
    template_content: '', prompt_template: '',
    script_requirement: DEFAULT_SCRIPT_REQ,
    linked_vars: ['pkg_brief', 'diff_str'],
    linked_apis: [], status: 'online', created_by: '',
    _product_ids: [], _template_ids: [], _pid_to_tid: {},
  }
  tplDialogIsNew.value = true
  _editingIdx = -1
  tplDialogVisible.value = true
}

function openEditTemplate(row) {
  ensureIfcDetails()
  _editingIdx = templateList.value.indexOf(row)
  // 深拷贝并补齐多产品上下文（兼容 Mode B 单产品行）
  const cloned = JSON.parse(JSON.stringify(row))
  cloned.province = cloned.province || props.province || ''
  cloned.intent   = cloned.intent   || props.intent   || cloned.template_name || ''
  if (!Array.isArray(cloned._product_ids)) {
    cloned._product_ids = cloned.product_id ? [cloned.product_id] : ['']
  }
  if (!Array.isArray(cloned._template_ids)) {
    cloned._template_ids = cloned.template_id ? [cloned.template_id] : []
  }
  if (!cloned._pid_to_tid) {
    cloned._pid_to_tid = cloned.template_id ? { [cloned.product_id || '']: cloned.template_id } : {}
  }
  tplEditing.value = cloned
  tplDialogIsNew.value = false
  tplDialogVisible.value = true
}

async function handleTemplateSave(formData) {
  // Mode A（有 province + intent）→ 直接调用后端 API（多产品分组保存）
  if (props.province && props.intent) {
    tplSaving.value = true
    try {
      // 解析新产品 ID 列表：空数组表示一个空字符串（兜底模板）
      let newPids = Array.isArray(formData.product_ids) && formData.product_ids.length
        ? formData.product_ids
        : ['']
      // 去重
      newPids = newPids.filter((p, i, arr) => arr.indexOf(p) === i)

      const oldGroup = tplEditing.value || {}
      const oldPids  = Array.isArray(oldGroup._product_ids) ? oldGroup._product_ids : []
      const pidToTid = oldGroup._pid_to_tid || {}

      const baseBody = {
        template_name:      props.intent,
        stage:              formData.stage || '',
        scene:              formData.scene || '',
        template_content:   formData.template_content || '',
        script_requirement: formData.script_requirement || '',
        linked_vars:        formData.linked_vars || [],
        linked_apis:        formData.linked_apis || tplEditing.value?.linked_apis || [],
        status:             formData.status || 'online',
        created_by:         formData.created_by || '',
      }

      // 单次批量保存：多产品 upsert + 删除去掉的产品合并为一次请求，
      // 后端只做一次 biz_config 版本化写入 + 热重载 + 广播，
      // 避免逐产品 POST/PUT/DELETE 造成的 N 次 ES 往返（25 个产品 = 25 轮）。
      const templates = newPids.map(pid => {
        const item = { ...baseBody, product_id: pid }
        // 编辑且该产品已有模板 → 带上 template_id 让后端原地更新（保留创建信息）
        const existTid = !tplDialogIsNew.value ? pidToTid[pid] : ''
        if (existTid) item.template_id = existTid
        return item
      })

      // 编辑时：老产品里被去掉的，收集其 template_id 一并删除
      const deleteTemplateIds = tplDialogIsNew.value
        ? []
        : oldPids
            .filter(pid => !newPids.includes(pid))
            .map(pid => pidToTid[pid])
            .filter(Boolean)

      const res = await apiFetch('/api/templates/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          province: props.province,
          intent: props.intent,
          templates,
          delete_template_ids: deleteTemplateIds,
          // 优化三：接口数据域变量「有传参默认全选」——由后端并入 linked_vars
          auto_domain_vars: true,
        }),
      })
      const json = await res.json()
      if (json.code === 200) {
        const okCount = json.data?.imported ?? templates.length
        tplDialogVisible.value = false
        ElMessage.success(tplDialogIsNew.value
          ? `✅ 创建成功（共 ${okCount} 条）`
          : `✅ 更新成功（共 ${okCount} 条）`)
      } else {
        ElMessage.error(`❌ ${json.detail || json.message || '保存失败'}`)
      }
      await loadTplItems()
    } catch (e) {
      ElMessage.error(e.message || '请求失败')
    } finally {
      tplSaving.value = false
    }
    return
  }

  // Mode B（无 province/intent）→ 本地操作（多产品模式：拆分为多条本地记录）
  const newPids = Array.isArray(formData.product_ids) && formData.product_ids.length
    ? formData.product_ids
    : (formData.product_id !== undefined ? [formData.product_id] : [''])

  const baseSaved = {
    template_id: tplEditing.value?.template_id || '',
    linked_apis: formData.linked_apis ?? tplEditing.value?.linked_apis ?? [],
    template_name:      formData.template_name      || formData.intent || '',
    stage:              formData.stage              || '',
    scene:              formData.scene              || '',
    template_content:   formData.template_content   || '',
    script_requirement: formData.script_requirement || '',
    linked_vars:        formData.linked_vars        || [],
    status:             formData.status             || 'online',
    created_by:         formData.created_by         || '',
  }

  if (tplDialogIsNew.value) {
    for (const pid of newPids) {
      templateList.value.push({ ...baseSaved, _uid: String(_uid++), product_id: pid })
    }
    tplPage.value = Math.ceil(templateList.value.length / TBL_PAGE_SIZE)
  } else if (_editingIdx >= 0) {
    // 取第一个产品 ID 替换当前行，其余追加为新记录
    templateList.value[_editingIdx] = {
      ...baseSaved,
      _uid: tplEditing.value?._uid ?? String(_uid++),
      product_id: newPids[0],
    }
    for (const pid of newPids.slice(1)) {
      templateList.value.push({ ...baseSaved, _uid: String(_uid++), product_id: pid })
    }
  }
  tplDialogVisible.value = false
  emitChange()
}


// ── 导入 CSV ───────────────────────────────────────────
const importVisible  = ref(false)
const csvFile        = ref(null)
const csvImporting   = ref(false)
const importMsg      = ref('')
const importOk       = ref(false)

function openImportCsv() {
  csvFile.value  = null
  importMsg.value = ''
  importVisible.value = true
}

// 优化六：下载导入用 CSV 模板（含表头，列顺序与解析一致），带 BOM 防止 Excel 中文乱码
function downloadCsvTemplate() {
  const header = '场景分类名称,环节,意图,产品ID,话术内容,关联变量(可留空),状态(online/offline)'
  const sample1 = '套餐推荐话术_5G升档,推荐环节,套餐升级,prod001,"您好，根据您的用量{usage}，推荐办理{pkg_brief}。",,online'
  const sample2 = '套餐推荐话术_兜底,推荐环节,套餐升级,,"您好，为您推荐更合适的套餐{pkg_brief}。",,online'
  const csv = '\uFEFF' + header + '\n' + sample1 + '\n' + sample2 + '\n'
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = '话术模板导入模板.csv'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// el-upload（拖拽/点击）选择文件：取原生 File 供 arrayBuffer 解码
function onCsvUploadChange(uploadFile) {
  csvFile.value = uploadFile?.raw || null
  importMsg.value = ''
}

function clearCsvFile() {
  csvFile.value = null
  importMsg.value = ''
}

function _fmtFileSize(bytes) {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

// 智能解码：UTF-8(带/不带 BOM) → 严格 UTF-8 校验 → 失败回退 GB18030（Excel 中文默认），根治 CSV 乱码
function _decodeCsvBuffer(buf) {
  const bytes = new Uint8Array(buf)
  if (bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) {
    return new TextDecoder('utf-8').decode(bytes.subarray(3))
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch (_) {
    try { return new TextDecoder('gb18030').decode(bytes) }
    catch (_) { return new TextDecoder('utf-8').decode(bytes) }
  }
}

// 完整 CSV 解析：支持双引号包裹、字段内逗号/换行、转义双引号("")，返回二维数组
function parseCsv(text) {
  const rows = []
  let row = [], cur = '', q = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (q) {
      if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++ } else q = false }
      else cur += ch
    } else if (ch === '"') { q = true }
    else if (ch === ',') { row.push(cur); cur = '' }
    else if (ch === '\n') { row.push(cur); rows.push(row); row = []; cur = '' }
    else if (ch === '\r') { /* 忽略，配合 \r\n */ }
    else cur += ch
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row) }
  return rows
}

// 表头识别：首行含「表头专属」关键词即视为表头（兼容新旧命名，且不会误判正文行）
const _CSV_HEADER_MARKERS = ['名称', '状态', '关联', '产品id', 'online', 'offline', 'status', '话术内容']
function _looksLikeHeader(cells) {
  const joined = (cells || []).join('').toLowerCase()
  return _CSV_HEADER_MARKERS.some(k => joined.includes(k.toLowerCase()))
}

async function doImportCsv() {
  if (!csvFile.value) return
  csvImporting.value = true
  importMsg.value = ''
  try {
    const buf = await csvFile.value.arrayBuffer()
    let text = _decodeCsvBuffer(buf)
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1)   // 去除残留 BOM
    const rows = parseCsv(text).filter(r => r.some(c => (c || '').trim() !== ''))
    if (!rows.length) { importMsg.value = 'CSV 文件为空'; importOk.value = false; return }
    // 列顺序：场景分类名称, 环节, 意图, 产品ID, 话术内容, 关联变量(可留空), 状态
    const dataRows = _looksLikeHeader(rows[0]) ? rows.slice(1) : rows

    // 解析为标准记录（话术内容必填）
    const parsed = []
    let skipped = 0
    for (const cols of dataRows) {
      const [name, stage, scene, productId, content, varsRaw, status] = cols
      if (!(content || '').trim()) { skipped++; continue }
      parsed.push({
        template_name:    (name || '').trim(),
        stage:            (stage || '').trim(),
        scene:            (scene || '').trim(),
        product_id:       (productId || '').trim(),
        template_content: (content || '').trim(),
        linked_vars: varsRaw
          ? varsRaw.replace(/[|｜]/g, ',').split(/[,，]/).map(v => v.trim()).filter(Boolean)
          : [],
        status: (status || 'online').trim() === 'offline' ? 'offline' : 'online',
      })
    }
    if (!parsed.length) {
      importMsg.value = `未导入：${skipped} 条话术内容为空`
      importOk.value = false
      return
    }

    // Mode A（Skill 管理：已有 province+intent）：一次性批量落库。
    // 此前逐条 POST /api/templates，每条都触发全量 biz_config 版本化写入 + skill_meta 刷新，
    // 导入几百条时版本号疯狂自增、日志“一直循环”，且 biz_config 逐条累加是 O(N²) 写放大。
    // 改为调用 /api/templates/bulk：后端合并后只做一次 ES 写入 + 热重载 + 广播。
    if (props.province && props.intent) {
      let ok = 0, failMsg = ''
      try {
        const res = await apiFetch('/api/templates/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            province: props.province,
            intent: props.intent,
            auto_domain_vars: true,
            templates: parsed.map(r => ({
              template_name: props.intent,
              stage: r.stage,
              scene: r.scene,
              product_id: r.product_id,
              template_content: r.template_content,
              script_requirement: DEFAULT_SCRIPT_REQ,
              linked_vars: r.linked_vars,
              linked_apis: [],
              status: r.status,
            })),
          }),
        })
        const json = await res.json()
        if (json.code === 200) ok = json.data?.imported ?? parsed.length
        else failMsg = json.detail || json.message || '保存失败'
      } catch (e) {
        failMsg = e.message
      }
      await loadTplItems()   // 重新拉取已落库的合并列表
      importOk.value = ok > 0
      importMsg.value = ok > 0
        ? `已保存 ${ok} 条模板`
          + (skipped ? `，跳过 ${skipped} 条（话术内容为空）` : '')
          + '。已自动发布上线并热重载生效。'
        : `导入失败：${failMsg}`
      if (ok) emitChange()
      return
    }

    // Mode B（创建新配置：尚无 province+intent）：暂存本地列表，随技能创建一起保存
    for (const r of parsed) {
      templateList.value.push({
        _uid: String(_uid++),
        template_id: '',
        template_name: r.template_name,
        stage: r.stage,
        scene: r.scene,
        product_id: r.product_id,
        template_content: r.template_content,
        linked_vars: r.linked_vars,
        prompt_template: '',
        script_requirement: DEFAULT_SCRIPT_REQ,
        linked_apis: [],
        status: r.status,
        created_by: '',
        _showPreview: false,
      })
    }
    importMsg.value = `已添加 ${parsed.length} 条到列表`
      + (skipped ? `，跳过 ${skipped} 条（话术内容为空）` : '')
      + '，保存配置后生效。'
    importOk.value = true
    emitChange()
  } catch (e) {
    importMsg.value = '解析失败：' + e.message
    importOk.value  = false
  } finally {
    csvImporting.value = false
  }
}

// ── 默认话术要求（context 工程版，与 TemplateEditDialog 保持一致）──────────────
const DEFAULT_SCRIPT_REQ = '结合【上下文数据】中的当前套餐、历史用量与用户标签，先点出最突出的用户痛点，再用推荐套餐对应字段的真实值说明如何解决；只讲有数据支撑的卖点，口语化、可直接对客播报，150字以内，结尾自然引导办理。'

// ── 智能映射状态 ────────────────────────────────────────
const smartMapOpen    = ref([])
const smartDocText    = ref('')
const smartParsing    = ref(false)
const smartMsg        = ref('')
const smartOk         = ref(false)
const smartParsedApis = ref([])

async function doSmartParse() {
  if (!smartDocText.value.trim()) return
  smartParsing.value = true
  smartMsg.value     = ''
  smartParsedApis.value = []
  try {
    const res = await http.post('/api/interfaces/parse_doc', {
      doc_text: smartDocText.value,
      province: props.province || '',
      intent:   props.intent   || '',
    })
    // 兼容 code/data 结构或直接数组
    const list = (res.code === 200 ? res.data : res) || []
    if (Array.isArray(list) && list.length) {
      smartParsedApis.value = list.map(api => ({
        ...api,
        _req_str: JSON.stringify(api.request_template || {}, null, 2),
        _ext_str: JSON.stringify(api.response_extract || {}, null, 2),
      }))
      smartMsg.value = `解析成功，${list.length} 个接口节点，请确认后点击「应用」`
      smartOk.value  = true
    } else {
      smartMsg.value = '解析结果为空，请检查文档格式或手动填写'
      smartOk.value  = false
    }
  } catch (e) {
    smartMsg.value = '智能解析服务暂不可用，请手动填写接口配置（' + (e?.message || '') + '）'
    smartOk.value  = false
  } finally {
    smartParsing.value = false
  }
}

/** 手动提取：从文档中提取 URL/Method 基础信息，不依赖 LLM */
function doManualFill() {
  const text = smartDocText.value
  const urlMatch = text.match(/https?:\/\/[^\s"']+/)
  const methodMatch = text.match(/\b(GET|POST|PUT|DELETE|PATCH)\b/i)
  if (!apiNodeList.value.length) {
    // 无节点时自动创建一个
    apiNodeList.value.push({
      _key: 'main_api', enabled: true,
      url: urlMatch?.[0] || '',
      method: methodMatch?.[1]?.toUpperCase() || 'POST',
      headers: reactive({ 'Content-Type': 'application/json' }),
      timeout: 30, max_retries: 2, mock_mode: false,
      request_body_wrapper: '', request_template: {},
      response_extract: reactive({ current_package: '', recommended_packages: '' }),
      field_transform: {}, mock_response: {}, _extra: {},
    })
  } else {
    if (urlMatch)    apiNodeList.value[0].url    = urlMatch[0]
    if (methodMatch) apiNodeList.value[0].method = methodMatch[1].toUpperCase()
  }
  smartMsg.value = urlMatch ? `已提取 URL：${urlMatch[0]}，请继续完善字段映射` : '未识别到 URL，已创建空节点，请手动填写'
  smartOk.value  = !!urlMatch
  emitChange()
}

/** 将解析结果应用到接口节点列表 */
function applySmartParsed() {
  if (!smartParsedApis.value.length) return
  apiNodeList.value = smartParsedApis.value.map(api => {
    let req = {}, ext = {}
    try { req = JSON.parse(api._req_str || '{}') } catch {}
    try { ext = JSON.parse(api._ext_str || '{}') } catch {}
    return {
      _key: api.api_name || 'main_api',
      enabled: true,
      url: api.url || '',
      method: (api.method || 'POST').toUpperCase(),
      headers: reactive({ ...( api.headers || { 'Content-Type': 'application/json' }) }),
      timeout: api.timeout || 30,
      max_retries: api.max_retries || 2,
      mock_mode: api.mock_mode || false,
      request_body_wrapper: api.request_body_wrapper || '',
      request_template: req,
      response_extract: reactive(ext),
      field_transform: api.field_transform || {},
      mock_response: api.mock_response || {},
      _extra: {},
    }
  })
  smartParsedApis.value = []
  smartMsg.value = `✅ 已应用 ${apiNodeList.value.length} 个接口节点，请在下方继续调整`
  smartOk.value  = true
  emitChange()
}

// API 节点列表（数组形式，方便 v-for，序列化时还原回 Object）
const apiNodeList = ref([])
// api_nodes 顶层 `_` 前缀键是配置元数据（如 _domain_fallbacks），不是接口节点：
// 单独保留并在序列化时合并回去，防止保存时被丢弃/误当节点渲染
const apiNodesMeta = ref({})
// 空域兜底行：[{ domain, path }] ↔ api_nodes._domain_fallbacks
const domainFallbacks = ref([])

// ── 标准数据域：7 大 resource_context 域（与 core/context.py 对齐）──
const STANDARD_SLOTS = new Set([
  'current_package', 'usage', 'tags', 'user_info',
  'recommended_packages', 'user_profile', 'domain_ext',
])

const TRANSFORM_TYPE_LABEL = {
  passthrough:    '直接透传',
  filter_include: '保留字段',
  filter_exclude: '排除字段',
}

/** 从一个接口节点配置中推断它产出的标准域，并附带 field_transform 的细节
 *  返回结构：[{ slot, fields:[{name, unit?}], rules:[{ key, type, type_label, from, include_keys, exclude_keys, unit_convert }] }]
 */
function inferProducedSlotDetails(cfg) {
  const map = new Map()
  const ensure = (slot) => {
    if (!map.has(slot)) map.set(slot, { fields: new Map(), rules: [] })
    return map.get(slot)
  }
  const ft = cfg?.field_transform || {}
  for (const [k, v] of Object.entries(ft)) {
    const top = String(k).split('.')[0]
    if (!STANDARD_SLOTS.has(top)) continue
    const item = ensure(top)
    const subKey = k.includes('.') ? k.split('.').slice(1).join('.') : ''
    const rule = {
      key: k,
      sub_key: subKey,
      type: v?.type || 'passthrough',
      type_label: TRANSFORM_TYPE_LABEL[v?.type] || (v?.type || '透传'),
      from: typeof v?.from === 'string' ? v.from : '',
      include_keys: Array.isArray(v?.include_keys) ? v.include_keys : [],
      exclude_keys: Array.isArray(v?.exclude_keys) ? v.exclude_keys : [],
      unit_convert: (v?.unit_convert && typeof v.unit_convert === 'object') ? v.unit_convert : {},
    }
    item.rules.push(rule)
    // include_keys 是该域将吸纳的字段
    rule.include_keys.forEach(name => {
      const unit = rule.unit_convert?.[name] || ''
      const prev = item.fields.get(name) || { name }
      if (unit) prev.unit = unit
      if (subKey) prev.sub = subKey
      item.fields.set(name, prev)
    })
    // 子键写法（usage.data_usage）也作为一个"分组字段"
    if (subKey && !rule.include_keys.length) {
      item.fields.set(subKey, { name: subKey, sub: subKey, group: true })
    }
  }
  // 回退：response_extract 顶层同名 key（仅作为存在性提示）
  const re = cfg?.response_extract || {}
  for (const [k, v] of Object.entries(re)) {
    if (!STANDARD_SLOTS.has(k)) continue
    const item = ensure(k)
    if (typeof v === 'string' && v && !item.rules.length) {
      item.rules.push({ key: k, type: 'extract_only', type_label: '响应提取',
        from: v, include_keys: [], exclude_keys: [], unit_convert: {} })
    }
  }
  // 直传节点零配置兜底：extra_info 样例（mock_response）顶层同名域自动透传
  if (cfg?.source_type === 'direct' && !map.size) {
    const sample = (cfg.mock_response && typeof cfg.mock_response === 'object') ? cfg.mock_response : {}
    for (const k of Object.keys(sample)) {
      if (!STANDARD_SLOTS.has(k)) continue
      const item = ensure(k)
      item.rules.push({ key: k, type: 'direct_passthrough', type_label: '直传透传',
        from: 'extra_info', include_keys: [], exclude_keys: [], unit_convert: {} })
    }
  }
  return [...map.entries()].map(([slot, info]) => ({
    slot,
    fields: [...info.fields.values()],
    rules: info.rules,
  }))
}

function inferProducedSlots(cfg) {
  return inferProducedSlotDetails(cfg).map(x => x.slot)
}

/**
 * 推断透传模式节点「对外提供的入参字段」（供 TemplateEditDialog 的「提供变量」展示）。
 * 透传模式不写 7 域映射规则，其提供的变量就是选定的 passthrough_fields；
 * 未显式选择（空=全部顶层字段）时从 mock_response 顶层非标准域字段推断。
 * 返回 { passthrough, fields }；非透传节点 passthrough=false。
 */
function derivePassthroughInfo(cfg) {
  if (!cfg || cfg.source_type !== 'direct' || cfg.direct_mode !== 'passthrough') {
    return { passthrough: false, fields: [] }
  }
  let fields = Array.isArray(cfg.passthrough_fields)
    ? cfg.passthrough_fields.filter(k => typeof k === 'string' && k) : []
  if (!fields.length) {
    const sample = (cfg.mock_response && typeof cfg.mock_response === 'object') ? cfg.mock_response : {}
    fields = Object.keys(sample).filter(k => k && !k.startsWith('_') && !STANDARD_SLOTS.has(k))
  }
  return { passthrough: true, fields }
}

// 模式 A 接口详情缓存（异步拉取后填入，便于在数据流映射页展示具体字段）
const ifcDetailsCache = reactive({})  // api_name → cfg

async function ensureIfcDetails() {
  if (!props.province || !props.intent || !ifcItems.value.length) return
  const tasks = []
  for (const it of ifcItems.value) {
    if (ifcDetailsCache[it.api_name]) continue
    tasks.push((async () => {
      try {
        const res = await apiFetch(`/api/interfaces/${it.province}/${it.intent}/${it.api_name}`)
        const json = await res.json()
        ifcDetailsCache[it.api_name] = json.data || {}
      } catch { ifcDetailsCache[it.api_name] = {} }
    })())
  }
  await Promise.all(tasks)
}

watch(activeTab, (v) => { if (v === 'dataflow') ensureIfcDetails() })
watch(ifcItems, () => { if (activeTab.value === 'dataflow') ensureIfcDetails() })

/** 给 TemplateEditDialog 用的接口列表（同时兼容 mode A 与 mode B）*/
const availableApisForTpl = computed(() => {
  // 模式 A：以后端列表为准；若已拉到详情，则解析具体字段
  if (props.province && props.intent && ifcItems.value.length) {
    return ifcItems.value.map(it => {
      const cfg = ifcDetailsCache[it.api_name]
      if (cfg) {
        const details = inferProducedSlotDetails(cfg)
        const pt = derivePassthroughInfo(cfg)
        return {
          api_name: it.api_name,
          description: it.description || cfg._comment || '',
          enabled: it.enabled !== false,
          mock_mode: !!(cfg.mock_mode ?? it.mock_mode),
          source_type: (cfg.source_type ?? it.source_type) === 'direct' ? 'direct' : 'api',
          produced_slots: details.map(x => x.slot),
          produced_slot_details: details,
          passthrough: pt.passthrough,
          passthrough_fields: pt.fields,
        }
      }
      return {
        api_name: it.api_name,
        description: it.description || '',
        enabled: it.enabled !== false,
        mock_mode: !!it.mock_mode,
        source_type: it.source_type === 'direct' ? 'direct' : 'api',
        produced_slots: Array.isArray(it.produced_slots) ? it.produced_slots : [],
        // 详情未拉到时无法判定透传字段，先按 source_type 标记（拉到详情后自动细化）
        passthrough: it.source_type === 'direct',
        passthrough_fields: [],
      }
    })
  }
  // 模式 B：从 apiNodeList 直接推断
  return apiNodeList.value.map(n => {
    const details = inferProducedSlotDetails(n)
    const pt = derivePassthroughInfo(n)
    return {
      api_name: n._key,
      description: n._comment || '',
      enabled: n.enabled !== false,
      mock_mode: !!n.mock_mode,
      source_type: n.source_type === 'direct' ? 'direct' : 'api',
      produced_slots: details.map(x => x.slot),
      produced_slot_details: details,
      passthrough: pt.passthrough,
      passthrough_fields: pt.fields,
    }
  })
})

/**
 * 从「标准数据关联」概览直接跳转到该接口的出参映射编辑（融合：概览看结果 → 一键改映射）。
 * 模式 A（已存在 skill）走后端详情；模式 B（Import 本地）从 apiNodeList 按名匹配。
 * 打开后直接定位到「出参映射 / 域映射」步骤。
 */
async function editMappingFromDomainLink(api) {
  if (props.province && props.intent) {
    await openIfcEdit({ province: props.province, intent: props.intent, api_name: api.api_name })
  } else {
    const row = apiNodeList.value.find(n => n._key === api.api_name)
    if (!row) { ElMessage.warning(`未找到接口「${api.api_name}」`); return }
    openLocalIfcEdit(row)
  }
  ifcEditTab.value = 'outparam'
}

/** 给"被引用"列用：api_name → 引用此接口的模板数 */
const apiUsageCount = computed(() => {
  const map = new Map()
  for (const t of templateList.value) {
    for (const a of (t.linked_apis || [])) {
      map.set(a, (map.get(a) || 0) + 1)
    }
  }
  return map
})

// ── 数据流映射 Tab：可视化 接口 → 标准域 → 模板 的全链路 ──
const STANDARD_SLOT_LIST = [
  { key: 'current_package',      label: '当前套餐' },
  { key: 'usage',                label: '历史用量' },
  { key: 'tags',                 label: '用户标签' },
  { key: 'user_info',            label: '用户基础信息' },
  { key: 'recommended_packages', label: '推荐产品' },
  { key: 'user_profile',         label: '用户画像' },
  { key: 'domain_ext',           label: '扩展域' },
]

// linked_var key → 它需要的 slot
const VAR_KEY_TO_SLOT = {
  cur_brief: 'current_package',
  pkg_brief: 'recommended_packages',
  usage_line: 'usage',
  user_tags: 'tags',
  user_info: 'user_info',
  user_profile: 'user_profile',
  domain_ext: 'domain_ext',
}

/** 某模板需要但接口未提供的 slot（按其 linked_apis 限定的接口集判断）*/
function tplUnmetSlots(tpl) {
  const need = new Set()
  for (const v of (tpl.linked_vars || [])) {
    const slot = VAR_KEY_TO_SLOT[v]
    if (slot) need.add(slot)
  }
  if (!need.size) return []
  // 该模板能拿到的接口集合
  const apis = (tpl.linked_apis && tpl.linked_apis.length)
    ? availableApisForTpl.value.filter(a => tpl.linked_apis.includes(a.api_name))
    : availableApisForTpl.value.filter(a => a.enabled)
  const supplied = new Set()
  for (const a of apis) for (const s of a.produced_slots) supplied.add(s)
  return [...need].filter(s => !supplied.has(s))
}

const dataflowStats = computed(() => {
  const apis = availableApisForTpl.value
  // 全局已覆盖标准域（所有启用接口产出的并集）
  const covered = new Set()
  for (const a of apis) {
    if (a.enabled !== false) for (const s of a.produced_slots) covered.add(s)
  }
  // 模板使用到的标准域
  const used = new Set()
  for (const t of templateList.value) {
    for (const v of (t.linked_vars || [])) {
      const slot = VAR_KEY_TO_SLOT[v]
      if (slot) used.add(slot)
    }
  }
  const uncoveredVars = []
  for (const t of templateList.value) {
    for (const v of (t.linked_vars || [])) {
      const slot = VAR_KEY_TO_SLOT[v]
      if (slot && !covered.has(slot)) uncoveredVars.push({ tpl: t.template_name, var: v, slot })
    }
  }
  const unmetTplCount = templateList.value.filter(t => tplUnmetSlots(t).length).length
  return {
    apiCount: apis.length,
    tplCount: templateList.value.length,
    coveredSlots: [...covered],
    usedSlots: [...used],
    uncoveredVars,
    unmetTplCount,
  }
})

const dataflowMatrix = computed(() => {
  const apis = availableApisForTpl.value
  return STANDARD_SLOT_LIST.map(slot => {
    const providerDetails = apis
      .filter(a => a.produced_slots.includes(slot.key))
      .map(a => ({
        api_name: a.api_name,
        fields: ((a.produced_slot_details || []).find(g => g.slot === slot.key)?.fields) || [],
      }))
    const consumerDetails = templateList.value
      .filter(t => (t.linked_vars || []).some(v => VAR_KEY_TO_SLOT[v] === slot.key))
      .map(t => ({
        template_name: t.template_name || '（未命名）',
        vars: (t.linked_vars || []).filter(v => VAR_KEY_TO_SLOT[v] === slot.key),
      }))
    const providers = providerDetails.map(p => p.api_name)
    const consumers = consumerDetails.map(c => c.template_name)
    let status = 'idle'
    if (consumers.length && providers.length) status = 'ok'
    else if (consumers.length && !providers.length) status = 'unmet'
    return {
      slot_key: slot.key, slot_label: slot.label,
      providers, consumers, providerDetails, consumerDetails, status,
    }
  })
})

const dataflowMatrixMap = computed(() => {
  const m = new Map()
  for (const row of dataflowMatrix.value) m.set(row.slot_key, row)
  return m
})

// ── 数据流映射 Tab（简化视图）────────────────────────────
const GENERATED_VAR_LIST = [
  { key: 'pkg_brief', label: '推荐产品信息' },
  { key: 'diff_str',  label: '套餐差异' },
  { key: 'table',     label: '差异表格' },
]

const VAR_LABEL = {
  cur_brief: '当前套餐信息', pkg_brief: '推荐产品信息', diff_str: '套餐差异',
  usage_line: '历史用量', user_tags: '用户标签', user_info: '用户基础信息',
  user_profile: '用户画像', domain_ext: '扩展信息',
  extra_info: '主服务补充信息', extra_context: '模板匹配上下文', table: '差异表格',
}

function varLabel(key) {
  return VAR_LABEL[key] || key
}

function formatTplLinkedVars(t) {
  const vars = (t.linked_vars || []).map(v => varLabel(v))
  return vars.length ? vars.join('、') : '—'
}

/**
 * 汇总消费某数据域/变量的模板：去重名称 + 总数
 * 返回 { uniqueNames: string[], totalCount: number }
 */
function _summarizeTplConsumers(tpls) {
  const totalCount = tpls.length
  const seen = new Set()
  const uniqueNames = []
  for (const t of tpls) {
    const n = t.template_name || '（未命名）'
    if (!seen.has(n)) { seen.add(n); uniqueNames.push(n) }
  }
  return { uniqueNames, totalCount }
}

/** 有接口产出的标准数据域 → 提供接口 + 消费模板 */
const domainSourceRows = computed(() => {
  const apis = availableApisForTpl.value
  const covered = new Set()
  for (const a of apis) {
    for (const s of (a.produced_slots || [])) covered.add(s)
  }
  return STANDARD_SLOT_LIST
    .filter(s => covered.has(s.key))
    .map(slot => {
      const providerApis = apis
        .filter(a => (a.produced_slots || []).includes(slot.key))
        .map(a => a.api_name)
      const tpls = templateList.value
        .filter(t => (t.linked_vars || []).some(v => VAR_KEY_TO_SLOT[v] === slot.key))
      const { uniqueNames, totalCount } = _summarizeTplConsumers(tpls)
      return {
        key: slot.key,
        label: slot.label,
        apis: providerApis,
        templates: uniqueNames,
        totalCount,
        unmet: totalCount > 0 && !providerApis.length,
      }
    })
})

/** 生成变量 → 消费模板 */
const generatedVarRows = computed(() =>
  GENERATED_VAR_LIST.map(v => {
    const tpls = templateList.value.filter(t => (t.linked_vars || []).includes(v.key))
    const { uniqueNames, totalCount } = _summarizeTplConsumers(tpls)
    return { ...v, templates: uniqueNames, totalCount }
  })
)

// ── 数据流映射：左侧按接口分组（可收缩）────────────────────
const dfmApiCollapsed = ref(new Set())
function toggleDfmApi(apiName) {
  const s = new Set(dfmApiCollapsed.value)
  if (s.has(apiName)) s.delete(apiName); else s.add(apiName)
  dfmApiCollapsed.value = s
}

const apiDomainGroups = computed(() => {
  return availableApisForTpl.value
    .filter(api => (api.produced_slots || []).length > 0)
    .map(api => {
      const slots = (api.produced_slot_details || []).map(detail => ({
        slot: detail.slot,
        label: STANDARD_SLOT_LIST.find(s => s.key === detail.slot)?.label || detail.slot,
        fields: (detail.fields || []).filter(f => f),
      }))
      const consumerSet = new Set()
      for (const detail of slots) {
        templateList.value
          .filter(t => (t.linked_vars || []).some(v => VAR_KEY_TO_SLOT[v] === detail.slot))
          .forEach(t => consumerSet.add(t.template_name || '（未命名）'))
      }
      return {
        api_name: api.api_name,
        description: api.description || '',
        enabled: api.enabled !== false,
        mock_mode: api.mock_mode,
        source_type: api.source_type || 'api',
        slots,
        consumer_templates: [...consumerSet],
      }
    })
})

// ── 数据流映射：右侧话术模板按「是否关联数据域变量」分两部分+分页 ──
const DFM_TPL_PAGE_SIZE = 20
const dfmLinkedPage   = ref(1)
const dfmUnlinkedPage = ref(1)

// 已关联：linked_vars 中含有数据域/计算变量（VAR_KEY_TO_SLOT 能映射，或属于 GENERATED_VAR_LIST）
const _dfmVarKeys = computed(() => {
  const domain = new Set(Object.keys(VAR_KEY_TO_SLOT))
  const gen    = new Set(GENERATED_VAR_LIST.map(v => v.key))
  return { domain, gen }
})
const dfmLinkedTpls = computed(() =>
  templateList.value.filter(t =>
    (t.linked_vars || []).some(v =>
      _dfmVarKeys.value.domain.has(v) || _dfmVarKeys.value.gen.has(v)
    )
  )
)
const dfmUnlinkedTpls = computed(() =>
  templateList.value.filter(t =>
    !(t.linked_vars || []).some(v =>
      _dfmVarKeys.value.domain.has(v) || _dfmVarKeys.value.gen.has(v)
    )
  )
)
const dfmLinkedPaged = computed(() => {
  const start = (dfmLinkedPage.value - 1) * DFM_TPL_PAGE_SIZE
  return dfmLinkedTpls.value.slice(start, start + DFM_TPL_PAGE_SIZE)
})
const dfmUnlinkedPaged = computed(() => {
  const start = (dfmUnlinkedPage.value - 1) * DFM_TPL_PAGE_SIZE
  return dfmUnlinkedTpls.value.slice(start, start + DFM_TPL_PAGE_SIZE)
})

function slotLabelOf(key) {
  return STANDARD_SLOT_LIST.find(s => s.key === key)?.label || key
}

function ruleTagType(t) {
  if (t === 'passthrough')    return 'success'
  if (t === 'filter_include') return 'primary'
  if (t === 'filter_exclude') return 'warning'
  if (t === 'extract_only')   return 'info'
  return ''
}

// ── 数据流映射 — 三维度配置编辑 ──
// slot ↔ var 反向映射：slot 默认对应的 linked_var key
const SLOT_DEFAULT_VARS = {
  current_package: ['cur_brief'],
  recommended_packages: ['pkg_brief', 'diff_str'],
  usage: ['usage_line'],
  tags: ['user_tags'],
  user_info: ['user_info'],
  user_profile: ['user_profile'],
  domain_ext: ['domain_ext'],
}

// 跟踪哪些接口（api_name）的 field_transform 被改动（模式 A 用）
const dfmDirtyApis = reactive(new Set())
const dfmSaving = ref(false)

// ── 接口卡片：每行（apiName, slot）的展开状态 ─────────────────
const dfmExpandedRows = reactive(new Set())
function dfmRowKey(apiName, slot) { return `${apiName}::${slot}` }
function dfmToggleRow(apiName, slot) {
  const k = dfmRowKey(apiName, slot)
  if (dfmExpandedRows.has(k)) dfmExpandedRows.delete(k)
  else dfmExpandedRows.add(k)
}
function dfmIsRowExpanded(apiName, slot) {
  return dfmExpandedRows.has(dfmRowKey(apiName, slot))
}

/** 取某接口在某 slot 上的产出明细（字段、规则数、单位转换数）*/
function getApiSlotSummary(api, slotKey) {
  const detail = (api.produced_slot_details || []).find(g => g.slot === slotKey)
  if (!detail) return null
  const ruleSummary = detail.rules.map(r => {
    if (r.type === 'filter_include') return { type: r.type, label: '保留', count: r.include_keys.length, sub: r.sub_key, from: r.from }
    if (r.type === 'filter_exclude') return { type: r.type, label: '排除', count: r.exclude_keys.length, sub: r.sub_key, from: r.from }
    if (r.type === 'extract_only')   return { type: r.type, label: '响应提取', count: 0, from: r.from }
    return { type: r.type, label: '透传', count: 0, sub: r.sub_key, from: r.from }
  })
  return { fields: detail.fields, rules: detail.rules, ruleSummary }
}

/** 接口"覆盖率"百分比：已映射的标准域 / 7 */
function apiCoverage(api) {
  return Math.round(((api.produced_slots || []).length / STANDARD_SLOT_LIST.length) * 100)
}

/** 取某接口的"产出"摘要（用于接口配置表格的"接口产出"列） */
function getApiOutput(apiName) {
  const cfg = props.province && props.intent
    ? ifcDetailsCache[apiName]
    : apiNodeList.value.find(n => n._key === apiName)
  if (!cfg) return { extractKeys: [], slots: [] }
  const extractKeys = Object.keys(cfg.response_extract || {})
  const slots = inferProducedSlots(cfg)
  return { extractKeys, slots }
}

/** 取某 from 名（中间数据集名）对应的 response_extract 路径 */
function getExtractPath(api, fromKey) {

  if (!fromKey) return ''
  // 模式 A：从详情缓存查
  if (props.province && props.intent) {
    const cfg = ifcDetailsCache[api.api_name]
    return cfg?.response_extract?.[fromKey] || ''
  }
  // 模式 B：从 apiNodeList 查
  const node = apiNodeList.value.find(n => n._key === api.api_name)
  return node?.response_extract?.[fromKey] || ''
}

/** 接口的中间数据集列表（response_extract 的 key→path 数组）*/
function apiExtractEntries(api) {
  const cfg = props.province && props.intent
    ? ifcDetailsCache[api.api_name]
    : apiNodeList.value.find(n => n._key === api.api_name)
  const re = cfg?.response_extract || {}
  return Object.entries(re).map(([key, path]) => ({ key, path: String(path || '') }))
}

// ═══════════ 标准数据关联 tab：规则模拟转换 ═══════════
/** 取接口完整 cfg（用于标准数据关联 tab 展示原始配置）*/
function getApiCfg(apiName) {
  if (props.province && props.intent) return ifcDetailsCache[apiName] || null
  return apiNodeList.value.find(n => n._key === apiName) || null
}

function jsonStringify(o) {
  try {
    const s = JSON.stringify(o ?? {}, null, 2)
    return s.length > 4000 ? s.slice(0, 4000) + '\n... (已截断)' : s
  } catch { return String(o) }
}

/** 按 JSON 路径取值（支持 a.b.c）*/
function _getByPath(obj, path) {
  if (!path) return undefined
  const keys = String(path).split('.').filter(Boolean)
  let cur = obj
  for (const k of keys) {
    if (cur == null) return undefined
    cur = cur[k]
  }
  return cur
}

const RULE_LABEL_DL = {
  passthrough:    '整块透传',
  filter_include: '保留字段',
  filter_exclude: '排除字段',
  extract_only:   '响应提取',
}

/**
 * 模拟规则转换：基于接口的 mock_response → response_extract → field_transform 计算 7 大域真实结果。
 * 返回 { filledCount, map: { slot_key: { filled, ruleType, ruleLabel, fromDesc, fields, fieldsType, fieldsLabel, previewText, previewSummary } } }
 */
function getSimulatedSlots(apiName) {
  const cfg = getApiCfg(apiName)
  const map = {}
  let filledCount = 0

  if (!cfg) {
    for (const s of STANDARD_SLOT_LIST) map[s.key] = { filled: false }
    return { filledCount: 0, map }
  }

  // Step 1：执行 response_extract → 中间数据集 datasets
  const mock = cfg.mock_response || {}
  const re = cfg.response_extract || {}
  const datasets = {}
  for (const [name, path] of Object.entries(re)) {
    datasets[name] = _getByPath(mock, path)
  }

  // Step 2：对每个标准域，按 field_transform 规则模拟
  const ft = cfg.field_transform || {}
  // 收集每个 slot 的所有规则（含 slot.sub_key 的子键写法）
  const slotRules = {}
  for (const [k, v] of Object.entries(ft)) {
    const top = String(k).split('.')[0]
    if (!STANDARD_SLOTS.has(top)) continue
    if (!slotRules[top]) slotRules[top] = []
    const sub = k.includes('.') ? k.split('.').slice(1).join('.') : ''
    slotRules[top].push({ sub_key: sub, ...(v || {}) })
  }

  // 回退：response_extract 顶层同名 key（无 ft 也算填充）
  for (const k of Object.keys(re)) {
    if (STANDARD_SLOTS.has(k) && !slotRules[k]) {
      slotRules[k] = [{ type: 'extract_only', from: k }]
    }
  }

  // 直传节点零配置兜底：extra_info 样例顶层同名域自动透传
  if (cfg.source_type === 'direct' && !Object.keys(slotRules).length) {
    for (const k of Object.keys(mock || {})) {
      if (STANDARD_SLOTS.has(k)) {
        datasets[k] = mock[k]
        slotRules[k] = [{ type: 'extract_only', from: k }]
      }
    }
  }

  for (const slot of STANDARD_SLOT_LIST) {
    const rules = slotRules[slot.key]
    if (!rules || !rules.length) {
      map[slot.key] = { status: 'none', filled: false }
      continue
    }
    filledCount++
    // 检测每条规则的来源数据集
    const missingFroms = []
    for (const r of rules) {
      const fromN = r.type === 'extract_only' ? slot.key : (r.from || slot.key)
      if (datasets[fromN] === undefined) missingFroms.push(fromN)
    }



    // 取第一条规则作为主规则展示（多条则合并字段）
    const main = rules[0]
    const ruleType = main.type || 'passthrough'
    let fromDesc = ''
    let resultValue
    let fields = []
    let fieldsType = 'include'
    let fieldsLabel = ''

    if (ruleType === 'extract_only') {
      // 仅提取：直接用 datasets[slot] 或 slot 同名
      fromDesc = `响应路径 ${re[slot.key] || slot.key}`
      resultValue = datasets[slot.key]
    } else {
      const fromName = main.from || slot.key
      const sourceData = datasets[fromName]
      fromDesc = `中间数据集 ${fromName}（路径: ${re[fromName] || '同名'}）`

      if (ruleType === 'passthrough') {
        resultValue = sourceData
      } else if (ruleType === 'filter_include') {
        const keys = Array.isArray(main.include_keys) ? main.include_keys : []
        fieldsType = 'include'
        fieldsLabel = '保留字段'
        fields = keys.map(k => ({
          name: k,
          unit: main.unit_convert?.[k] || '',
        }))
        if (sourceData && typeof sourceData === 'object' && !Array.isArray(sourceData)) {
          resultValue = {}
          for (const k of keys) {
            if (sourceData[k] !== undefined && sourceData[k] !== null && sourceData[k] !== '') {
              resultValue[k] = sourceData[k]
            }
          }
        } else { resultValue = sourceData }
      } else if (ruleType === 'filter_exclude') {
        const keys = Array.isArray(main.exclude_keys) ? main.exclude_keys : []
        fieldsType = 'exclude'
        fieldsLabel = '排除字段'
        fields = keys.map(k => ({ name: k }))
        if (sourceData && typeof sourceData === 'object' && !Array.isArray(sourceData)) {
          resultValue = {}
          const exSet = new Set(keys)
          for (const [k, v] of Object.entries(sourceData)) {
            if (!exSet.has(k) && v !== null && v !== undefined && v !== '') resultValue[k] = v
          }
        } else { resultValue = sourceData }
      } else {
        resultValue = sourceData
      }
    }

    // 多条规则合并到同一 slot：分子键列出
    if (rules.length > 1) {
      const merged = {}
      for (const r of rules) {
        const sub = r.sub_key || '_root'
        const fromN = r.from || slot.key
        const src = datasets[fromN]
        if (r.type === 'filter_include' && Array.isArray(r.include_keys) && src && typeof src === 'object') {
          merged[sub] = {}
          for (const k of r.include_keys) {
            if (src[k] !== undefined && src[k] !== null && src[k] !== '') merged[sub][k] = src[k]
          }
        } else {
          merged[sub] = src
        }
      }
      resultValue = merged
      // 字段汇总（仅 include 系）
      const allFields = []
      const seen = new Set()
      for (const r of rules) {
        if (Array.isArray(r.include_keys)) {
          for (const k of r.include_keys) {
            if (!seen.has(k)) { seen.add(k); allFields.push({ name: k, unit: r.unit_convert?.[k] || '', sub: r.sub_key || '' }) }
          }
        }
      }
      if (allFields.length) {
        fields = allFields
        fieldsType = 'include'
        fieldsLabel = `${rules.length} 条规则共保留字段`
      }
    }

    // 预览
    const previewText = resultValue === undefined
      ? '（未取到数据，请检查 mock_response 是否包含对应路径）'
      : (() => {
          try {
            const s = JSON.stringify(resultValue, null, 2)
            return s.length > 1500 ? s.slice(0, 1500) + '\n... (已截断)' : s
          } catch { return String(resultValue) }
        })()
    let previewSummary
    if (resultValue == null) previewSummary = '空'
    else if (Array.isArray(resultValue)) previewSummary = `数组 ${resultValue.length} 项`
    else if (typeof resultValue === 'object') previewSummary = `对象 ${Object.keys(resultValue).length} 字段`
    else previewSummary = typeof resultValue

    const dataMissing = missingFroms.length > 0
    map[slot.key] = {
      filled: true,
      dataMissing,
      missingFroms,
      ruleType,
      ruleLabel: RULE_LABEL_DL[ruleType] || ruleType,
      fromDesc,
      fields,
      fieldsType,
      fieldsLabel,
      previewText: dataMissing
        ? `⚠ 规则已配置，但 mock_response 中找不到数据集：${missingFroms.join(', ')}\n\n请检查接口配置中：\n  1) response_extract 是否声明了 ${missingFroms.join(' / ')} 这些 key\n  2) mock_response 中对应的 JSON 路径是否存在`
        : previewText,
      previewSummary: dataMissing ? '⚠ 数据缺失' : previewSummary,
    }
  }

  return { filledCount, map }
}


// 切到 domain_link tab 时确保详情已加载
// 打开「映射结果」弹窗时按需拉取详情（见 openMapResult），此处无需再监听 tab 切换

// ── 标准数据关联：接口卡片折叠状态 ──
const dlOpenSet = reactive(new Set())
const dlExplicitOpened = ref(false)  // 用户操作过后改为完全受控
function dlIsApiOpen(name, defaultOpen) {
  if (dlExplicitOpened.value) return dlOpenSet.has(name)
  return !!defaultOpen
}
function dlToggleApi(name, ev) {
  // details 默认会切换，这里同步状态
  ev?.preventDefault?.()
  dlExplicitOpened.value = true
  if (dlOpenSet.has(name)) dlOpenSet.delete(name)
  else dlOpenSet.add(name)
}
function dlExpandAll() {
  dlExplicitOpened.value = true
  dlOpenSet.clear()
  for (const a of availableApisForTpl.value) dlOpenSet.add(a.api_name)
}
function dlCollapseAll() {
  dlExplicitOpened.value = true
  dlOpenSet.clear()
}


const RULE_TYPE_LABEL_FLAT = {
  passthrough:    '整块透传',
  filter_include: '只保留',
  filter_exclude: '排除字段',
  extract_only:   '响应提取',
}

/** 接口已映射规则的扁平列表：每个 slot 的每条 rule 一行 */
function apiMappedRules(api) {
  const out = []
  const details = api.produced_slot_details || []
  for (const detail of details) {
    const slotLabel = slotLabelOf(detail.slot)
    for (const rule of (detail.rules || [])) {
      const fieldCount = rule.type === 'filter_include' ? (rule.include_keys?.length || 0)
                       : rule.type === 'filter_exclude' ? (rule.exclude_keys?.length || 0)
                       : 0
      const hasUnit = !!(rule.unit_convert && Object.keys(rule.unit_convert).length)
      out.push({
        slotKey: detail.slot,
        slotLabel,
        rule,
        ruleLabel: RULE_TYPE_LABEL_FLAT[rule.type] || rule.type,
        fieldCount,
        hasUnit,
      })
    }
  }
  return out
}

/** 接口尚未映射的标准域（用于"+ 添加映射"快速入口）*/
function apiUnmappedSlots(api) {
  const have = new Set(api.produced_slots || [])
  return STANDARD_SLOT_LIST.filter(s => !have.has(s.key))
}

/** 取某接口在某 slot 上的所有规则（含子键拆分）*/
function apiSlotRules(api, slotKey) {
  return apiMappedRules(api).filter(r => r.slotKey === slotKey)
}


/** 标准数据关联：某接口已填充的标准域列表 */
function filledSlotsOf(apiName) {
  const sim = getSimulatedSlots(apiName)
  return STANDARD_SLOT_LIST.filter(s => sim.map[s.key]?.filled)
}
/** 标准数据关联：某接口未填充的标准域列表 */
function emptySlotsOf(apiName) {
  const sim = getSimulatedSlots(apiName)
  return STANDARD_SLOT_LIST.filter(s => !sim.map[s.key]?.filled)
}





/** 在指定接口节点上增加 / 移除一个标准域产出（默认 passthrough）*/
function dfmAddApiSlot(apiName, slot) {
  const cfg = _resolveApiCfg(apiName)
  if (!cfg) return
  if (!cfg.field_transform) cfg.field_transform = {}
  if (!cfg.field_transform[slot]) {
    cfg.field_transform[slot] = { type: 'passthrough' }
  }
  _markDirty(apiName)
}
function dfmRemoveApiSlot(apiName, slot) {
  const cfg = _resolveApiCfg(apiName)
  if (!cfg?.field_transform) return
  // 移除该 slot 的所有 field_transform 规则（含 slot.* 子键）
  Object.keys(cfg.field_transform).forEach(k => {
    if (k === slot || k.startsWith(slot + '.')) delete cfg.field_transform[k]
  })
  _markDirty(apiName)
}

/** 切换模板的 linked_var */
function dfmToggleTplVar(uid, varKey) {
  const t = templateList.value.find(x => x._uid === uid)
  if (!t) return
  if (!Array.isArray(t.linked_vars)) t.linked_vars = []
  const i = t.linked_vars.indexOf(varKey)
  if (i >= 0) t.linked_vars.splice(i, 1)
  else t.linked_vars.push(varKey)
  emitChange()
}

/** 切换模板对接口的绑定 */
function dfmToggleTplApi(uid, apiName) {
  const t = templateList.value.find(x => x._uid === uid)
  if (!t) return
  if (!Array.isArray(t.linked_apis)) t.linked_apis = []
  const i = t.linked_apis.indexOf(apiName)
  if (i >= 0) t.linked_apis.splice(i, 1)
  else t.linked_apis.push(apiName)
  emitChange()
}

function _resolveApiCfg(apiName) {
  // 模式 A：从详情缓存取
  if (props.province && props.intent) {
    if (!ifcDetailsCache[apiName]) ifcDetailsCache[apiName] = {}
    return ifcDetailsCache[apiName]
  }
  // 模式 B：从 apiNodeList 取
  return apiNodeList.value.find(n => n._key === apiName)
}

function _markDirty(apiName) {
  if (props.province && props.intent) {
    dfmDirtyApis.add(apiName)
  } else {
    emitChange()  // 模式 B 直接同步到父组件 v-model
  }
}

/** 一键保存所有改动 */
async function saveDataflowChanges() {
  // 模式 B：emitChange 已实时触发，无需额外保存
  if (!props.province || !props.intent) {
    ElMessage.success('✅ 已保存到当前配置')
    return
  }
  // 模式 A：批量 PUT 到后端
  if (!dfmDirtyApis.size) { ElMessage.info('没有需要保存的改动'); return }
  dfmSaving.value = true
  try {
    const tasks = [...dfmDirtyApis].map(async apiName => {
      const cfg = ifcDetailsCache[apiName]
      if (!cfg) return
      const body = { field_transform: cfg.field_transform || {} }
      const res = await apiFetch(
        `/api/interfaces/${props.province}/${props.intent}/${apiName}`,
        { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      )
      const json = await res.json()
      if (json.code !== 200) throw new Error(json.detail || json.message || `${apiName} 保存失败`)
    })
    await Promise.all(tasks)
    dfmDirtyApis.clear()
    ElMessage.success(`✅ 已保存 ${tasks.length} 个接口的产出域映射`)
    await loadIfcItems()
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    dfmSaving.value = false
  }
}

// ── 高亮联动状态 ──
const dfmFocus = reactive({ kind: '', id: '' })  // kind: '' | 'api' | 'slot' | 'tpl'

function setDfmFocus(kind, id) {
  if (dfmFocus.kind === kind && dfmFocus.id === id) {
    dfmFocus.kind = ''; dfmFocus.id = ''
  } else {
    dfmFocus.kind = kind; dfmFocus.id = id
  }
}
function clearDfmFocus() { dfmFocus.kind = ''; dfmFocus.id = '' }

function isApiRelated(api) {
  if (!dfmFocus.kind) return false
  if (dfmFocus.kind === 'api') return api.api_name === dfmFocus.id
  if (dfmFocus.kind === 'slot') return api.produced_slots.includes(dfmFocus.id)
  if (dfmFocus.kind === 'tpl') {
    const t = templateList.value.find(x => x._uid === dfmFocus.id)
    if (!t) return false
    if ((t.linked_apis || []).length) return t.linked_apis.includes(api.api_name)
    return api.enabled !== false  // 未绑定 → 全部启用接口
  }
  return false
}

function isSlotRelated(slotKey) {
  if (!dfmFocus.kind) return false
  if (dfmFocus.kind === 'slot') return slotKey === dfmFocus.id
  if (dfmFocus.kind === 'api') {
    const a = availableApisForTpl.value.find(x => x.api_name === dfmFocus.id)
    return a ? a.produced_slots.includes(slotKey) : false
  }
  if (dfmFocus.kind === 'tpl') {
    const t = templateList.value.find(x => x._uid === dfmFocus.id)
    if (!t) return false
    return (t.linked_vars || []).some(v => VAR_KEY_TO_SLOT[v] === slotKey)
  }
  return false
}

function isTplRelated(tpl) {
  if (!dfmFocus.kind) return false
  if (dfmFocus.kind === 'tpl') return tpl._uid === dfmFocus.id
  if (dfmFocus.kind === 'slot') {
    return (tpl.linked_vars || []).some(v => VAR_KEY_TO_SLOT[v] === dfmFocus.id)
  }
  if (dfmFocus.kind === 'api') {
    if ((tpl.linked_apis || []).length) return tpl.linked_apis.includes(dfmFocus.id)
    // 未绑定 → 默认全部，则任何启用接口都关联
    const a = availableApisForTpl.value.find(x => x.api_name === dfmFocus.id)
    return !!(a && a.enabled !== false)
  }
  return false
}



// 策略
const strategy = reactive({
  default_strategy: 'direct',
  top_n: 3,
  max_script_length: 150,
  max_parallel_scripts: 3,
})
// 模板匹配取值配置（biz_config.template_match）：接口查询模式下，
// 指定推荐结果中哪个字段（支持点路径、逗号分隔多候选）用于匹配话术模板维度
const templateMatch = reactive({
  product_id_from: '',
  stage_from: '',
  scene_from: '',
})

// ── 模板匹配候选字段：从接口出参映射结果（mock 样例模拟）提取可选字段 ──

/** 轻量模拟某接口节点单个标准域的映射结果值（response_extract → field_transform 整域规则）。
 *  与 getSimulatedSlots 相比只求"域的最终值"，供候选字段提取；子键规则/多规则合并从简。 */
function _simulateSlotValue(cfg, slotKey) {
  if (!cfg) return undefined
  const mock = cfg.mock_response || {}
  const re = cfg.response_extract || {}
  const datasets = {}
  for (const [name, path] of Object.entries(re)) datasets[name] = _getByPath(mock, path)
  const ft = cfg.field_transform || {}
  for (const [k, r] of Object.entries(ft)) {
    if (String(k).split('.')[0] !== slotKey || k.includes('.') || !r || typeof r !== 'object') continue
    const fromN = r.from || slotKey
    const src = datasets[fromN] !== undefined ? datasets[fromN] : _getByPath(mock, fromN)
    if (src == null || typeof src !== 'object') return src
    if (r.type === 'filter_include' || r.type === 'include') {
      if (Array.isArray(src)) return src
      const keys = Array.isArray(r.include_keys) ? r.include_keys : []
      return Object.fromEntries(keys.filter(x => src[x] !== undefined).map(x => [x, src[x]]))
    }
    if (r.type === 'filter_exclude' || r.type === 'exclude') {
      if (Array.isArray(src)) return src
      const ex = new Set(Array.isArray(r.exclude_keys) ? r.exclude_keys : [])
      return Object.fromEntries(Object.entries(src).filter(([x]) => !ex.has(x)))
    }
    return src   // passthrough / 其它
  }
  if (re[slotKey] !== undefined) return datasets[slotKey]
  if (cfg.source_type === 'direct') return (mock || {})[slotKey]
  return undefined
}

/** 从域值提取候选字段路径（数组取首条；嵌套对象下钻一层为 a.b 点路径）*/
function _collectFieldPaths(val, prefix = '', depth = 0, out = []) {
  const item = Array.isArray(val) ? val[0] : val
  if (!item || typeof item !== 'object' || Array.isArray(item) || depth > 1) return out
  for (const [k, v] of Object.entries(item)) {
    if (String(k).startsWith('_')) continue
    const p = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      _collectFieldPaths(v, p, depth + 1, out)
    } else {
      const sample = Array.isArray(v) ? '[…]' : String(v ?? '')
      out.push({ field: p, sample: sample.length > 24 ? sample.slice(0, 24) + '…' : sample })
    }
  }
  return out
}

/** 产品ID类字段识别评分：3=典型ID字段名 2=含产品/套餐语义的ID 1=以id结尾 0=其它 */
function _pidScore(field) {
  const leaf = String(field).split('.').pop()
  if (/^(product_?id|offer_?id|cur_?offer_?id|package_?id|prod_?id|goods_?id)$/i.test(leaf)) return 3
  if (/(offer|product|prod|pkg|package|goods|policy|activity|biz).{0,8}id$/i.test(leaf)) return 2
  if (/id$/i.test(leaf)) return 1
  return 0
}

/** 候选字段清单：遍历接口节点（模式B用 apiNodeList，模式A回退接口详情缓存），
 *  模拟 recommended_packages / current_package 两域的映射结果，提取字段路径。 */
const matchFieldCandidates = computed(() => {
  const nodes = apiNodeList.value.length
    ? apiNodeList.value.map(n => [n._key, n])
    : Object.entries(ifcDetailsCache)
  const seen = new Map()
  for (const [name, cfg] of nodes) {
    if (!cfg || cfg.enabled === false) continue
    for (const slotKey of ['recommended_packages', 'current_package']) {
      const slotLabel = STANDARD_SLOT_LIST.find(s => s.key === slotKey)?.label || slotKey
      for (const c of _collectFieldPaths(_simulateSlotValue(cfg, slotKey))) {
        if (!seen.has(c.field)) {
          seen.set(c.field, {
            ...c,
            source: `${name} · ${slotLabel}`,
            score: _pidScore(c.field),
          })
        }
      }
    }
  }
  return [...seen.values()].sort((a, b) => b.score - a.score)
})

/** 逗号分隔字符串 ↔ 多选数组（el-select multiple 绑定用）*/
function _tmArrProxy(key) {
  return computed({
    get: () => String(templateMatch[key] || '').split(/[,，]/).map(s => s.trim()).filter(Boolean),
    set: (arr) => { templateMatch[key] = (arr || []).join(',') },
  })
}
const tmProductIdArr = _tmArrProxy('product_id_from')
const tmStageArr     = _tmArrProxy('stage_from')
const tmSceneArr     = _tmArrProxy('scene_from')

/** 序列化当前 UI 中的 template_match（空则返回 null，表示删除该配置段）*/
function buildTemplateMatchCfg() {
  const cfg = {}
  for (const k of ['product_id_from', 'stage_from', 'scene_from']) {
    const arr = String(templateMatch[k] || '')
      .split(/[,，]/).map(s => s.trim()).filter(Boolean)
    if (arr.length) cfg[k] = arr.length > 1 ? arr : arr[0]
  }
  return Object.keys(cfg).length ? cfg : null
}

/** 序列化空域兜底行 → api_nodes._domain_fallbacks（无有效行时返回 null）*/
function buildDomainFallbacksCfg() {
  const rows = domainFallbacks.value.filter(r => r.domain && String(r.path || '').trim())
  if (!rows.length) return null
  return Object.fromEntries(rows.map(r => [r.domain, String(r.path).trim()]))
}

/**
 * 保存模板匹配 / 空域兜底到生效配置。
 * Mode A（Skill 管理）：合并写入后端 biz_config / api_nodes（不覆盖话术模板与接口节点）。
 * Mode B（创建页）：仅同步到父组件 v-model，随技能创建一并落库。
 */
const [saveMatchSettings, matchSettingsSaving] = useLock(async () => {
  const tmCfg = buildTemplateMatchCfg()
  const dfCfg = buildDomainFallbacksCfg()

  // 先同步本地 v-model（Mode B 到此即可；Mode A 额外落库）
  emitChange()

  if (!props.province || !props.intent) {
    ElMessage.success('✅ 已写入当前配置（创建技能时一并保存）')
    return
  }

  const p = encodeURIComponent(props.province)
  const i = encodeURIComponent(props.intent)
  try {
    // ── biz_config.template_match：GET 合并再 PUT，避免覆盖 script_templates_v2 ──
    const bizRes = await apiFetch(`/api/skills/${p}/${i}/biz_config`)
    const bizJson = await bizRes.json().catch(() => ({}))
    if (!bizRes.ok || (bizJson.code !== undefined && bizJson.code !== 200)) {
      throw new Error(bizJson.detail || bizJson.message || '读取 biz_config 失败')
    }
    const biz = { ...(bizJson.data || {}) }
    if (tmCfg) biz.template_match = tmCfg
    else delete biz.template_match
    // 策略面板也在本编辑器内，一并合并当前值
    biz.strategy = { ...(biz.strategy || {}), ...strategy }

    const bizPut = await apiFetch(`/api/skills/${p}/${i}/biz_config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: biz }),
    })
    const bizPutJson = await bizPut.json().catch(() => ({}))
    if (!bizPut.ok || (bizPutJson.code !== undefined && bizPutJson.code !== 200)) {
      throw new Error(bizPutJson.detail || bizPutJson.message || '保存 template_match 失败')
    }

    // ── api_nodes._domain_fallbacks：GET 合并再 PUT，保留全部接口节点 ──
    const apiRes = await apiFetch(`/api/skills/${p}/${i}/api_nodes`)
    const apiJson = await apiRes.json().catch(() => ({}))
    if (!apiRes.ok || (apiJson.code !== undefined && apiJson.code !== 200)) {
      throw new Error(apiJson.detail || apiJson.message || '读取 api_nodes 失败')
    }
    const apiNodes = { ...(apiJson.data || {}) }
    if (dfCfg) apiNodes._domain_fallbacks = dfCfg
    else delete apiNodes._domain_fallbacks

    const apiPut = await apiFetch(`/api/skills/${p}/${i}/api_nodes`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: apiNodes }),
    })
    const apiPutJson = await apiPut.json().catch(() => ({}))
    if (!apiPut.ok || (apiPutJson.code !== undefined && apiPutJson.code !== 200)) {
      throw new Error(apiPutJson.detail || apiPutJson.message || '保存空域兜底失败')
    }

    // 回写父组件，保证再次打开/关闭弹窗前本地态与落库一致
    emit('update:modelValue', {
      api_nodes: apiNodes,
      biz_config: biz,
    })
    ElMessage.success('✅ 模板匹配与填槽设置已保存')
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  }
})

/** 选择变更：先同步本地；Mode B 即完成，Mode A 需再点「保存设置」落库 */
function onMatchSettingsChange() {
  emitChange()
}

/** 一键推荐：从候选中选产品ID语义最强的字段填入（最多 2 个候选按序兜底）*/
function autoRecommendMatchField() {
  const cands = matchFieldCandidates.value.filter(c => c.score >= 2)
  if (!cands.length) {
    ElMessage.warning('未在接口出参映射结果中识别到产品ID类字段：请先在接口配置里填好 mock 样例与出参映射，或手动输入字段名')
    return
  }
  const picked = cands.slice(0, 2).map(c => c.field)
  tmProductIdArr.value = picked
  onMatchSettingsChange()
  ElMessage.success(`已推荐产品ID匹配字段：${picked.join('、')}（请点「保存设置」写入生效配置）`)
}
// 模板列表
const templateList = ref([])

// ── 从 props 初始化 ───────────────────────────────────
function loadFromProps(val) {
  const apiNodes = val?.api_nodes || {}
  // `_` 前缀顶层键（配置元数据）与接口节点分离保存
  apiNodesMeta.value = Object.fromEntries(
    Object.entries(apiNodes).filter(([k]) => k.startsWith('_'))
  )
  domainFallbacks.value = Object.entries(apiNodesMeta.value._domain_fallbacks || {})
    .map(([domain, path]) => ({ domain, path: String(path ?? '') }))
  apiNodeList.value = Object.entries(apiNodes).filter(([k]) => !k.startsWith('_')).map(([k, v]) => ({
    _key: k,
    enabled:               v.enabled               ?? true,
    url:                   v.url                   ?? '',
    method:                v.method                ?? 'POST',
    headers:               reactive({ ...(v.headers || {}) }),
    timeout:               v.timeout               ?? 30,
    max_retries:           v.max_retries           ?? 2,
    mock_mode:             v.mock_mode             ?? false,
    request_body_wrapper:  v.request_body_wrapper  ?? '',
    request_template:      v.request_template      ?? {},
    response_extract:      reactive({ ...(v.response_extract || {}) }),
    field_transform:       v.field_transform       ?? {},
    mock_response:         v.mock_response         ?? {},
    // 直传/透传模式标识：作为一等字段保留，保证行内“直传”标签与 emitChange 透传一致
    source_type:           v.source_type === 'direct' ? 'direct' : 'api',
    direct_mode:           v.direct_mode === 'passthrough' ? 'passthrough' : 'mapping',
    passthrough_fields:    Array.isArray(v.passthrough_fields) ? [...v.passthrough_fields] : [],
    // pass-through 未知字段
    _extra: Object.fromEntries(
      Object.entries(v).filter(([fk]) =>
        !['enabled','url','method','headers','timeout','max_retries','mock_mode',
          'request_body_wrapper','request_template','response_extract',
          'field_transform','mock_response','_comment','created_by','created_at',
          'source_type','direct_mode','passthrough_fields'].includes(fk)
      )
    ),
  }))

  const biz = val?.biz_config || {}
  const s = biz.strategy || {}
  strategy.default_strategy    = s.default_strategy    ?? 'direct'
  strategy.top_n               = s.top_n               ?? 3
  strategy.max_script_length   = s.max_script_length   ?? 150
  strategy.max_parallel_scripts = s.max_parallel_scripts ?? 3

  // 模板匹配取值配置（值可能是字符串或数组，UI 统一按逗号分隔字符串编辑）
  const tm = biz.template_match || {}
  const tmStr = v => Array.isArray(v) ? v.join(',') : String(v ?? '')
  templateMatch.product_id_from = tmStr(tm.product_id_from)
  templateMatch.stage_from      = tmStr(tm.stage_from)
  templateMatch.scene_from      = tmStr(tm.scene_from)

  // Mode A（有 province+intent，如 Skill 管理）：话术模板改由 loadTplItems 分页拉全量并
  // 「按场景分类/环节/意图/话术内容」合并归类展示（不同 product_id 收进同一分组行），
  // 增删改经 /api/templates 独立持久化；此处不用 biz_config 直读列表覆盖，避免退回「按产品ID逐条铺开」。
  if (props.province && props.intent) return

  let uid = 0
  templateList.value = (biz.script_templates_v2 || []).map(t => {
    const pid = t.product_id ?? ''
    return {
    _uid:             String(uid++),
    template_id:      t.template_id      ?? '',
    template_name:    t.template_name    ?? '',
    stage:            t.stage            ?? '',
    scene:            t.scene            ?? '',
      product_id:       pid,
    template_content: t.template_content ?? (t.content ?? ''),
    prompt_template:  t.prompt_template  ?? '',
    script_requirement: t.script_requirement ?? '',
    linked_vars:      [...(t.linked_vars || [])],
    linked_apis:      t.linked_apis      ?? [],
    status:           t.status           ?? 'online',
    created_by:       t.created_by       ?? '',
      _showPreview:     false,
      // Mode B（biz_config 直读）也需要 _product_ids 使产品ID列正常显示
      _product_ids:     pid ? [pid] : [],
      _template_ids:    t.template_id ? [t.template_id] : [],
      _pid_to_tid:      t.template_id ? { [pid]: t.template_id } : {},
    }
  })
}

watch(() => props.modelValue, loadFromProps, { immediate: true, deep: false })

// ── 序列化并 emit ─────────────────────────────────────
function emitChange() {
  const tmCfg = buildTemplateMatchCfg()
  const dfCfg = buildDomainFallbacksCfg()

  // Mode A（Skill 管理）：接口节点 / 话术模板由独立 API 持久化；
  // 此处只合并匹配设置与元数据，避免用空的 templateList 覆盖 script_templates_v2。
  if (props.province && props.intent) {
    const prevApi = { ...(props.modelValue?.api_nodes || {}) }
    Object.assign(prevApi, apiNodesMeta.value)
    if (dfCfg) prevApi._domain_fallbacks = dfCfg
    else delete prevApi._domain_fallbacks

    const prevBiz = { ...(props.modelValue?.biz_config || {}) }
    prevBiz.strategy = { ...(prevBiz.strategy || {}), ...strategy }
    if (tmCfg) prevBiz.template_match = tmCfg
    else delete prevBiz.template_match

    emit('update:modelValue', { api_nodes: prevApi, biz_config: prevBiz })
    return
  }

  // Mode B（创建页）：完整序列化 api_nodes + biz_config
  const apiNodes = {}
  for (const node of apiNodeList.value) {
    const isDirect = node.source_type === 'direct'
    apiNodes[node._key] = {
      enabled:              node.enabled,
      url:                  node.url,
      method:               node.method,
      headers:              { ...node.headers },
      timeout:              node.timeout,
      max_retries:          node.max_retries,
      mock_mode:            node.mock_mode,
      ...(node.request_body_wrapper ? { request_body_wrapper: node.request_body_wrapper } : {}),
      request_template:     node.request_template,
      response_extract:     { ...node.response_extract },
      ...(Object.keys(node.field_transform || {}).length
        ? { field_transform: node.field_transform } : {}),
      ...(Object.keys(node.mock_response || {}).length
        ? { mock_response: node.mock_response } : {}),
      // 直传/透传模式标识：必须透传，否则会被当作普通接口查询节点（url 必填）导致校验失败
      ...(isDirect ? {
        source_type: 'direct',
        direct_mode: node.direct_mode || 'mapping',
        ...(node.direct_mode === 'passthrough'
          ? { passthrough_fields: [...(node.passthrough_fields || [])] } : {}),
      } : {}),
      ...node._extra,
    }
  }

  // `_` 前缀元数据键透传（防丢失）；空域兜底行同步回 _domain_fallbacks
  Object.assign(apiNodes, apiNodesMeta.value)
  if (dfCfg) apiNodes._domain_fallbacks = dfCfg
  else delete apiNodes._domain_fallbacks

  const scriptTemplatesV2 = templateList.value.map(tpl => {
    const obj = {
      template_id:       tpl.template_id || `tpl_${Date.now()}_${Math.random().toString(36).slice(2,6)}`,
      template_name:     tpl.template_name,
      stage:             tpl.stage,
      scene:             tpl.scene,
      product_id:        tpl.product_id,
      template_content:  tpl.template_content,
      linked_vars:       [...tpl.linked_vars],
      status:            tpl.status,
    }
    if (tpl.prompt_template)    obj.prompt_template    = tpl.prompt_template
    if (tpl.script_requirement) obj.script_requirement = tpl.script_requirement
    if (tpl.linked_apis?.length) obj.linked_apis       = tpl.linked_apis
    if (tpl.created_by)          obj.created_by        = tpl.created_by
    return obj
  })

  const bizConfig = {
    strategy: { ...strategy },
    field_aliases: props.modelValue?.biz_config?.field_aliases ?? {},
    ...(tmCfg ? { template_match: tmCfg } : {}),
    script_templates_v2: scriptTemplatesV2,
  }

  emit('update:modelValue', { api_nodes: apiNodes, biz_config: bizConfig })
}

// ── KV 编辑辅助 ──────────────────────────────────────
function renameKey(obj, oldKey, newKey) {
  if (!newKey || newKey === oldKey) return
  const val = obj[oldKey]
  delete obj[oldKey]
  obj[newKey] = val
  emitChange()
}
function removeKvKey(obj, key) {
  delete obj[key]
  emitChange()
}
function addKvKey(obj) {
  let k = 'new_key'
  let i = 1
  while (k in obj) k = `new_key_${i++}`
  obj[k] = ''
  emitChange()
}

// ── JSON 字段编辑 ─────────────────────────────────────
function prettyJson(v) {
  if (!v || typeof v !== 'object') return ''
  return JSON.stringify(v, null, 2)
}
function parseJsonField(node, field, text) {
  try {
    node[field] = JSON.parse(text)
    emitChange()
  } catch (_) {
    // 输入过程中允许临时非法 JSON，不 emit
  }
}

// ── 话术模板操作 ──────────────────────────────────────
let _uid = 1000

/** 表格"产品 ID"列：兼容模式 A（_product_ids 数组）与模式 B（单 product_id） */
function rowProductIds(row) {
  const pids = Array.isArray(row._product_ids) && row._product_ids.length
    ? row._product_ids
    : (row.product_id ? [row.product_id] : [])
  return pids.filter(p => p)
}

/** 合并归类后底层模板总条数（各分组产品数之和；兜底组按 1 条计），用于「N 组 · 共 M 条」展示 */
const templateTotalCount = computed(() =>
  templateList.value.reduce((sum, row) => sum + Math.max(rowProductIds(row).length, 1), 0))

/** 取一行分组下的全部底层 template_id（合并归类后一组可含多个产品的模板） */
function rowTemplateIds(row) {
  if (!row) return []
  return Array.isArray(row._template_ids) && row._template_ids.length
    ? row._template_ids.filter(Boolean)
    : (row.template_id ? [row.template_id] : [])
}

/** el-table 行主键：跨分页保留勾选需稳定 key */
function tplRowKey(row) {
  const tids = rowTemplateIds(row)
  return tids.length ? tids.join(',') : (row._uid || row.template_name || Math.random())
}

// ── 多选 / 批量删除 ────────────────────────────────────
const tplTableRef      = ref(null)
const selectedTemplates = ref([])
const batchDeleting    = ref(false)

function onTplSelectionChange(rows) {
  selectedTemplates.value = rows || []
}

/** 统一走后端批量删除：按分组收集所有 template_id，一次请求（单次 ES 写） */
async function _batchDeleteTids(tids, label) {
  const res  = await apiFetch('/api/templates/batch_delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // 传技能包定位，后端直接从该技能包删除，避免同 id 跨技能包/脏 province 误定位
    body: JSON.stringify({ template_ids: tids, province: props.province, intent: props.intent }),
  })
  const json = await res.json().catch(() => ({}))
  if (json.code === 200) {
    ElMessage.success(`✅ ${json.message || `删除成功（共 ${tids.length} 条）`}`)
  } else {
    ElMessage.error(`❌ ${json.detail || json.message || (label || '删除') + '失败'}`)
  }
  await loadTplItems()
  if (tplTableRef.value) tplTableRef.value.clearSelection()
  selectedTemplates.value = []
}

async function batchRemoveTemplates() {
  if (batchDeleting.value) return
  const rows = selectedTemplates.value
  if (!rows.length) return
  const tids = [...new Set(rows.flatMap(rowTemplateIds))]
  const total = tids.length
  if (!total) { ElMessage.warning('未找到可删除的模板 ID'); return }
  const ok = await $msg.confirm(
    `确认删除选中的 ${rows.length} 组话术模板（共 ${total} 条）？\n删除后需点「发布上线」热重载生效，操作不可撤销。`,
    { title: '批量删除确认', type: 'warning', confirmText: '确认删除' },
  )
  if (!ok) return
  batchDeleting.value = true
  try {
    await _batchDeleteTids(tids, '批量删除')
  } catch (e) {
    ElMessage.error(e.message || '请求失败')
  } finally {
    batchDeleting.value = false
  }
}

async function removeTemplate(idx) {
  const row = templateList.value[idx]
  // Mode A：调用后端批量删除接口（分组下所有 template_id 一次写入）
  if (props.province && props.intent && row) {
    const tids = rowTemplateIds(row)
    if (!tids.length) {
      ElMessage.warning('未找到可删除的模板 ID')
      return
    }
    const ok = await $msg.confirm(
      tids.length > 1
        ? `确认删除该分组（含 ${tids.length} 个产品模板）？删除后需点「发布上线」生效。`
        : `确认删除该话术模板？删除后需点「发布上线」生效。`,
      { title: '删除确认', type: 'warning', confirmText: '确认删除' },
    )
    if (!ok) return
    try {
      await _batchDeleteTids(tids)
    } catch (e) {
      ElMessage.error(e.message || '请求失败')
    }
    return
  }
  // Mode B：本地删除
  templateList.value.splice(idx, 1)
  emitChange()
}

</script>

<style scoped>
/* ── 导入 CSV 弹窗 ───────────────────────────── */
.csv-import-body { display: flex; flex-direction: column; gap: 18px; }
.csv-step {
  border: 1px solid var(--border, #e4e7ed);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}
.csv-step-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: linear-gradient(90deg, #f3f6ff 0%, #fafbff 100%);
  border-bottom: 1px solid var(--border, #e4e7ed);
}
.csv-step-no {
  width: 22px; height: 22px;
  flex: 0 0 22px;
  border-radius: 50%;
  background: var(--el-color-primary, #409eff);
  color: #fff;
  font-size: 13px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
}
.csv-step-title { font-size: 14px; font-weight: 600; color: #303133; }
.csv-step-body { padding: 14px; }
.csv-cols {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin: 12px 0 10px;
}
.csv-col-tag {
  font-size: 12px;
  padding: 2px 9px;
  border-radius: 12px;
  background: #f0f2f5;
  color: #606266;
  border: 1px solid #e4e7ed;
}
.csv-col-tag.req {
  background: #fef0f0;
  color: #f56c6c;
  border-color: #fbc4c4;
  font-weight: 600;
}
.csv-tips {
  margin: 0; padding-left: 18px;
  font-size: 12.5px; line-height: 1.9; color: #606266;
}
.csv-tips strong { color: #303133; }
.csv-upload-tip { font-size: 12px; color: var(--muted, #909399); margin-top: 6px; }
.csv-file-card {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 14px;
  border: 1px solid #d9ecff;
  border-radius: 8px;
  background: #f5fbff;
}
.csv-file-icon { font-size: 28px; color: var(--el-color-primary, #409eff); flex: 0 0 auto; }
.csv-file-meta { flex: 1 1 auto; min-width: 0; }
.csv-file-name {
  font-size: 14px; font-weight: 600; color: #303133;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.csv-file-size { font-size: 12px; color: var(--muted, #909399); margin-top: 2px; }
.csv-result :deep(.el-alert__title) { line-height: 1.6; }
:deep(.csv-import-dialog .el-upload-dragger) { padding: 22px 10px; }

.skill-config-editor { font-size: 13px; width: 100%; }
:deep(.skill-config-editor .el-tabs--border-card) { border-radius: 8px; }
:deep(.skill-config-editor .el-tabs__content) { padding: 14px 16px; }

.node-block {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  margin-bottom: 16px;
  background: #fafbff;
}
.node-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.node-title { font-weight: 700; font-size: 14px; color: var(--primary); }

.form-section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin: 14px 0 8px;
}
.form-section-hint {
  font-weight: 400;
  font-size: 11px;
  color: var(--muted);
  margin-left: 8px;
  text-transform: none;
  letter-spacing: 0;
}

/* KV 编辑器 */
.kv-editor { display: flex; flex-direction: column; gap: 6px; }
.kv-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.kv-row .el-input { flex: 1; }

/* 策略块 */
.strategy-block {
  background: #f8f9fa;
  border-radius: var(--radius);
  padding: 14px 16px;
  margin-bottom: 16px;
}


.empty-tip {
  text-align: center;
  padding: 32px;
  color: var(--muted);
  font-size: 13px;
}

/* ── 模式 A：接口管理表格 ── */
.ifc-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px; padding: 10px 14px;
  background: #f8faff; border: 1px solid #dce6fb; border-radius: 8px;
}
.ifc-toolbar-title { font-weight: 600; font-size: 14px; color: var(--text); }
.ifc-btn-link {
  background: transparent; border: none; color: var(--primary);
  font-size: 13px; cursor: pointer; padding: 0 3px; line-height: 1;
}
.ifc-btn-link:hover { text-decoration: underline; }
.ifc-btn-link.danger { color: var(--danger); }
.ifc-sep { color: #dee2e6; margin: 0 1px; font-size: 12px; }
:deep(.api-row-disabled td) { color: var(--muted) !important; }

/* 查看弹窗详情 */
:deep(.ifc-detail-row) {
  display: grid; grid-template-columns: 90px 1fr; gap: 8px;
  align-items: start; margin-bottom: 10px;
}
:deep(.ifc-detail-label) {
  font-size: 13px; font-weight: 500; color: var(--muted); text-align: right; padding-top: 2px;
}
:deep(.ifc-detail-value) { font-size: 13px; color: var(--text); word-break: break-word; }
:deep(.ifc-detail-value.mono) { font-family: monospace; }
:deep(.ifc-detail-value.code) {
  font-family: monospace; font-size: 12px; background: #f8f9fa;
  padding: 6px 8px; border-radius: 5px; border: 1px solid var(--border);
}

/* 代码 textarea */
.code-textarea :deep(textarea) {
  font-family: monospace; font-size: 12px;
  background: #0f172a; color: #e2e8f0; line-height: 1.6;
}

/* 智能分析说明 */
.ifc-auto-map-hint {
  font-size: 13px; color: var(--muted); margin-bottom: 10px; line-height: 1.55;
  padding: 10px 12px; background: #f8f9fa; border-radius: 7px; border: 1px solid var(--border);
}
.ifc-analysis-box {
  background: #fff8f0; border: 1px solid #ffd8a8; border-radius: 6px;
  padding: 10px 14px; font-size: 13px; color: #b45309; margin-bottom: 10px;
}
.ifc-pre {
  background: #0f172a; color: #e2e8f0; padding: 10px;
  border-radius: 6px; font-size: 11px; max-height: 200px; overflow: auto;
  margin: 0; white-space: pre-wrap; word-break: break-all;
}

/* ── 话术模板工具栏 ── */
/* 模板匹配与填槽设置面板 */
.tpl-match-panel {
  margin: 8px 0;
  border: 1px solid var(--border, #e4e7ed);
  border-radius: 6px;
  background: var(--bg-soft, #fafbfc);
}
.tpl-match-summary {
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  user-select: none;
}
.tpl-match-summary-hint {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
  color: var(--muted, #909399);
}
.tpl-match-body {
  padding: 4px 12px 12px;
  border-top: 1px dashed var(--border, #e4e7ed);
}
.tpl-match-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.tpl-match-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.tpl-match-label {
  width: 110px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text, #303133);
  font-weight: 600;
}
.tpl-match-hint {
  font-size: 11px;
  color: var(--muted, #909399);
}
.tpl-match-arrow {
  font-size: 12px;
  color: var(--muted, #909399);
  font-family: monospace;
}
.tpl-match-opt-src {
  float: right;
  margin-left: 16px;
  font-size: 11px;
  color: var(--muted, #909399);
}

.tpl-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
}
.tpl-toolbar-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text);
}

/* ── 下线模板行变灰 ── */
:deep(.tpl-row-offline td) { color: var(--muted) !important; }

/* ── 分页 ── */
.tpl-pagination {
  margin-top: 12px;
  display: flex;
  justify-content: center;
}

/* ── 弹窗内容区 ── */
.tpl-dialog-body { max-height: 68vh; overflow-y: auto; padding: 4px 2px; }
.import-csv-body { padding: 4px 0; }

/* ── 智能映射面板 ── */
:deep(.el-collapse) { border: 1px solid var(--border) !important; border-radius: 8px !important; }
:deep(.el-collapse-item__header) {
  padding: 10px 16px;
  background: #f8f9ff;
  border-radius: 8px;
  font-size: 13px;
}
:deep(.el-collapse-item__content) { padding: 12px 16px; }

/* ── 创建方式选择 ── */
.create-mode-hint { font-size: 13px; color: var(--muted); margin: 0 0 14px; line-height: 1.5; }
.create-mode-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.create-mode-card {
  text-align: left; padding: 18px; border: 2px solid var(--border);
  border-radius: 10px; background: #fff; cursor: pointer; transition: .2s;
  display: flex; flex-direction: column; gap: 8px; font: inherit;
}
.create-mode-card:hover { border-color: var(--primary); box-shadow: 0 4px 12px rgba(59,91,219,.12); }
.create-mode-card.primary { border-color: #c5d4fc; background: #f8faff; }
.create-mode-icon { font-size: 28px; line-height: 1; }
.create-mode-title { font-size: 15px; font-weight: 700; color: var(--text); }
.create-mode-desc { font-size: 12px; color: var(--muted); line-height: 1.45; }

/* ── Agent pipeline 弹窗 ── */
.agent-flow-steps {
  display: flex; align-items: center; justify-content: center;
  gap: 4px; margin-bottom: 20px; padding: 12px 8px;
  border-bottom: 1px solid var(--border); background: #fafafa; border-radius: 8px;
}
.agent-flow-step { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }
.agent-flow-step.active { color: var(--primary); font-weight: 600; }
.agent-flow-step.done { color: var(--success); }
.agent-flow-step .num {
  width: 22px; height: 22px; border-radius: 50%; background: #e9ecef;
  display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; flex-shrink: 0;
}
.agent-flow-step.active .num { background: var(--primary); color: #fff; }
.agent-flow-step.done .num { background: var(--success); color: #fff; }
.agent-flow-line { flex: 1; height: 2px; background: #e9ecef; min-width: 24px; max-width: 64px; }
.agent-flow-line.done { background: #96f2a9; }
.agent-sub { font-size: 13px; color: var(--muted); margin: 0 0 14px; }
.agent-specs {
  margin: 14px 0; font-size: 13px; border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px; background: #fafafa;
}
.agent-specs summary { cursor: pointer; font-weight: 500; color: var(--primary); }
.agent-specs ul { margin: 10px 0 0 18px; line-height: 1.6; color: var(--muted); padding: 0; }
.agent-drop-zone {
  border: 2px dashed var(--border); border-radius: 10px; padding: 28px;
  text-align: center; cursor: pointer; transition: .2s; background: #fafafa;
  font-size: 13px; color: var(--muted);
}
.agent-drop-zone:hover, .agent-drop-zone.dragging { border-color: var(--primary); background: #f8faff; }
.agent-drop-icon { font-size: 36px; margin-bottom: 8px; line-height: 1; }
.agent-file-name { margin-top: 10px; font-size: 13px; color: var(--primary); font-weight: 500; word-break: break-all; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
.agent-parsing-panel { padding: 8px 0 12px; }
.agent-parsing-title { font-size: 15px; font-weight: 600; text-align: center; margin: 0 0 14px; }
.agent-progress-track { height: 8px; background: #e9ecef; border-radius: 4px; overflow: hidden; margin-bottom: 18px; }
.agent-progress-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #748ffc); border-radius: 4px; transition: width .4s ease; }
.agent-pipeline-list { list-style: none; margin: 0; padding: 0; }
.agent-pipeline-item {
  display: flex; gap: 12px; padding: 12px 10px; border-radius: 8px;
  margin-bottom: 6px; border: 1px solid var(--border); background: #fff; transition: .2s;
}
.agent-pipeline-item.active { border-color: var(--primary); background: #f8faff; }
.agent-pipeline-item.done { opacity: .85; border-color: #d3f9d8; background: #f4fcf6; }
.agent-pipeline-item.pending { opacity: .65; }
.agent-pipeline-icon { font-size: 18px; line-height: 1.4; flex-shrink: 0; width: 26px; text-align: center; }
.agent-pipeline-title { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
.agent-pipeline-desc { font-size: 12px; color: var(--muted); line-height: 1.5; }
.agent-parsing-hint { font-size: 12px; color: var(--muted); text-align: center; margin-top: 14px; }

/* ── Review 步骤 ── */
.review-section-title {
  font-size: 14px; font-weight: 700; margin: 18px 0 12px;
  color: var(--text); border-left: 3px solid var(--primary); padding-left: 10px;
}
.review-label { font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 6px; }
.agent-review-done-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px; background: #e6fcf5; border: 1px solid #96f2d7;
  border-radius: 8px; margin-bottom: 18px;
}
.agent-review-done-text { font-size: 14px; font-weight: 600; color: #2b8a3e; }
.agent-inparam-hint-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;
  margin: 0 0 12px; padding: 10px 12px; background: #f8f9fa;
  border: 1px solid var(--border); border-radius: 8px; font-size: 12px;
}
.agent-hint-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.agent-hint-green { background: #d3f9d8; color: #2b8a3e; }
.agent-hint-blue  { background: #d0ebff; color: #1864ab; }
.agent-hint-warn  { background: #ffe8cc; color: #d9480f; }
.agent-hint-text  { color: var(--muted); }
.agent-param-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; }
.agent-param-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.agent-param-table th, .agent-param-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #f1f3f5; }
.agent-param-table th { background: #f8f9fa; font-weight: 600; }
.agent-param-table .mono { font-family: monospace; font-size: 11px; }
.review-param-select {
  width: 140px; height: 34px; padding: 0 8px; border: 1px solid var(--border);
  border-radius: 6px; font-size: 12px; outline: none;
}
.review-param-input {
  width: 200px; height: 34px; padding: 0 8px; border: 1px solid var(--border);
  border-radius: 6px; font-size: 12px; outline: none; font-family: monospace;
}
.agent-outmap-toolbar { display: flex; gap: 10px; margin: 10px 0 14px; }
.agent-llm-green-box {
  margin: 0 0 12px; padding: 12px 14px; background: #e6fcf5;
  border: 1px solid #96f2d7; border-radius: 8px; font-size: 13px;
  color: #2b8a3e; line-height: 1.55; white-space: pre-wrap;
}
.agent-outmap-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 8px; }
.agent-json-advanced {
  margin-top: 16px; border: 1px solid var(--border);
  border-radius: 8px; padding: 12px; background: #fafafa;
}
.agent-json-advanced summary { cursor: pointer; font-weight: 600; font-size: 13px; color: var(--primary); }

/* ═══════════ 数据流映射 Tab 样式 ═══════════ */
.dfm-kpi-row {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
  margin-bottom: 16px;
}
.dfm-kpi-card {
  padding: 14px 18px; border: 1px solid var(--border); border-radius: 10px;
  background: linear-gradient(135deg, #f8faff 0%, #fff 100%);
  text-align: center;
}
.dfm-kpi-card.warn { background: linear-gradient(135deg, #fff8e1 0%, #fff 100%); border-color: #ffe066; }
.dfm-kpi-card.danger { background: linear-gradient(135deg, #fff5f5 0%, #fff 100%); border-color: #ffa8a8; }
.dfm-kpi-num { font-size: 22px; font-weight: 700; color: var(--primary); line-height: 1.1; }
.dfm-kpi-card.danger .dfm-kpi-num { color: #c92a2a; }
.dfm-kpi-card.warn .dfm-kpi-num { color: #b45309; }
.dfm-kpi-label { font-size: 12px; color: var(--muted); margin-top: 4px; }

.dfm-flow-wrap {
  display: grid; grid-template-columns: 1fr 28px 1fr 28px 1fr;
  gap: 8px; align-items: stretch; margin-bottom: 16px;
}
.dfm-flow-arrow {
  font-size: 22px; color: #adb5bd; text-align: center;
  align-self: center; font-weight: 700;
}
.dfm-flow-col {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 10px; background: #fafbfc; max-height: 540px; overflow-y: auto;
  min-width: 0;
}
.dfm-col-title {
  font-size: 13px; font-weight: 700; color: var(--text);
  padding: 4px 6px 8px; border-bottom: 1px dashed var(--border); margin-bottom: 8px;
}
.dfm-card {
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 10px; margin-bottom: 8px; font-size: 12px;
  transition: all .15s;
}
.dfm-card.api { border-left: 3px solid #4263eb; }
.dfm-card.slot { border-left: 3px solid #adb5bd; }
.dfm-card.slot.supplied { border-left-color: #2b8a3e; background: #f4fcf6; }
.dfm-card.slot.unmet    { border-left-color: #c92a2a; background: #fff5f5; }
.dfm-card.tpl  { border-left: 3px solid #f59f00; }
.dfm-card.tpl.unmet { border-left-color: #c92a2a; background: #fff5f5; }
.dfm-card-name {
  font-weight: 700; font-size: 13px; color: var(--text);
  margin-bottom: 2px; word-break: break-all;
}
.dfm-card-desc { font-size: 11px; color: var(--muted); margin-bottom: 6px; line-height: 1.45; }
.dfm-card-desc.mono { font-family: monospace; }
.dfm-card-tags { display: flex; flex-wrap: wrap; gap: 3px; }
.dfm-tag { font-size: 11px; }
.dfm-more { font-size: 11px; color: var(--muted); align-self: center; }
.dfm-card-warn {
  margin-top: 6px; font-size: 11px; color: #c92a2a;
  background: #fff0f0; padding: 4px 6px; border-radius: 4px;
}
.dfm-card-apis {
  margin-top: 4px; font-size: 11px; color: var(--muted);
  font-family: monospace; word-break: break-all;
}
.dfm-card-apis.muted { color: #adb5bd; font-style: italic; font-family: inherit; }

.dfm-detail {
  border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 14px; background: #fafafa;
}
.dfm-detail summary {
  cursor: pointer; font-weight: 600; font-size: 13px; color: var(--primary);
}
.mono { font-family: monospace; }

/* ── Banner / Focus 控制条 ── */
.dfm-banner {
  background: linear-gradient(90deg, #f0f4ff 0%, #fff 100%);
  border: 1px solid #d0d9ff; border-radius: 10px;
  padding: 12px 16px; margin-bottom: 14px;
}
.dfm-banner-title {
  font-size: 14px; color: var(--text); display: flex; align-items: center; gap: 6px;
}
.dfm-banner-icon { font-size: 18px; }
.dfm-banner-sub { font-size: 12px; color: var(--muted); margin-top: 4px; line-height: 1.6; }

.dfm-focus-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: #fff8e1; border: 1px solid #ffe066;
  border-radius: 6px; margin-bottom: 12px; font-size: 12px;
}
.dfm-focus-label { font-weight: 600; color: #b45309; }
.dfm-focus-hint { color: #b45309; margin-left: auto; font-style: italic; }
.dfm-focus-bar:has(.dfm-focus-tip) { background: #fafbff; border-color: #e3e8f5; }
.dfm-focus-tip { color: var(--muted); }

/* 优化6：双向高亮联动 */
.dfm-clickable { cursor: pointer; transition: opacity .15s, background .15s, box-shadow .15s; }
.dfm-hit {
  background: #fff8e1 !important; box-shadow: inset 3px 0 0 #f59f00;
  border-radius: 4px;
}
.dfm-fade { opacity: .32; }

/* ── 卡片头/字段细节 ── */
.dfm-card { cursor: pointer; }
.dfm-card.focused {
  outline: 2px solid #4263eb; outline-offset: 1px;
  box-shadow: 0 4px 14px rgba(66,99,235,0.18); transform: translateY(-1px);
}
.dfm-card.related {
  background: #eef2ff; border-color: #adc1ff;
}
.dfm-card.dimmed { opacity: 0.32; }

.dfm-card-head {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 4px;
}
.dfm-card-foot {
  margin-top: 6px; font-size: 11px; color: var(--muted);
  border-top: 1px dashed #e9ecef; padding-top: 4px;
}
.dfm-card-foot .muted { color: #adb5bd; font-style: italic; }

/* 接口卡：标准域分组 */
.dfm-slot-list { display: flex; flex-direction: column; gap: 4px; margin: 4px 0; }
.dfm-slot-group {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
  padding: 4px 6px; background: #f8f9fa; border-radius: 4px;
  font-size: 11px;
}
.dfm-slot-key { font-weight: 600; color: #2b8a3e; flex-shrink: 0; }
.dfm-slot-arrow { color: #adb5bd; flex-shrink: 0; }
.dfm-fields { display: inline-flex; flex-wrap: wrap; gap: 2px; }
.dfm-fields-all { font-size: 10px; color: #868e96; font-style: italic; }
.dfm-slot-block {
  border: 1px solid #e9ecef; border-radius: 6px; background: #fff;
  padding: 6px 8px; margin-bottom: 6px;
}
.dfm-slot-block-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1px dashed #e9ecef;
}
.dfm-slot-code-inline {
  font-family: monospace; font-size: 10px; color: #868e96;
  padding: 1px 5px; background: #f1f3f5; border-radius: 3px;
}
.dfm-slot-rule {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px;
  font-size: 11px; padding: 3px 0;
}
.dfm-slot-rule + .dfm-slot-rule { border-top: 1px dotted #f1f3f5; }
.dfm-rule-tag { font-size: 10px !important; height: 18px !important; line-height: 18px !important; }
.dfm-rule-sub { font-family: monospace; color: #1864ab; font-weight: 600; }
.dfm-rule-from { color: var(--muted); font-size: 10px; }
.dfm-rule-from code {
  font-family: monospace; background: #f1f3f5; padding: 0 4px;
  border-radius: 3px; color: #495057;
}
.dfm-rule-fields { display: inline-flex; flex-wrap: wrap; gap: 2px; align-items: center; }
.dfm-rule-exclude-label { font-size: 10px; color: #c92a2a; }
.dfm-field-excl { background: #fff5f5 !important; color: #c92a2a !important; border-color: #ffa8a8 !important; }
.dfm-unit-mark { color: #f59f00; margin-left: 2px; font-size: 9px; }
.dfm-field-tag {
  font-size: 10px !important; padding: 0 5px !important; height: 18px !important;
  line-height: 18px !important; font-family: monospace; background: #fff !important;
  border: 1px solid #dee2e6 !important; color: #495057 !important;
}

/* 标准域卡 */
.dfm-slot-code {
  font-family: monospace; font-size: 10px; color: #868e96;
  margin-left: auto; padding: 1px 6px; background: #f1f3f5; border-radius: 3px;
}
.dfm-slot-stats { font-size: 11px; color: var(--muted); margin: 4px 0; }
.dfm-slot-stats .sep { margin: 0 4px; color: #ced4da; }

/* 列标题副提示 */
.dfm-col-hint {
  font-size: 11px; color: var(--muted); font-weight: 400;
  margin-left: 6px;
}

/* 详细矩阵单行 */
.dfm-mtx-line {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
  margin-bottom: 6px; padding: 2px 0;
}
.dfm-mtx-line:last-child { margin-bottom: 0; }
.dfm-mtx-fields { display: inline-flex; flex-wrap: wrap; gap: 2px; }

/* 顶部操作栏 */
.dfm-action-bar {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-bottom: 12px; padding: 10px 14px;
  background: #f8faff; border: 1px solid #dce6fb; border-radius: 8px;
}
.dfm-action-hint { flex: 1; font-size: 12px; color: var(--muted); line-height: 1.65; }
.dfm-action-hint strong { color: var(--text); font-weight: 600; }
.dfm-dirty-badge { margin-left: 6px; }

/* 卡片内快速编辑区 */
.dfm-quick-edit {
  margin-top: 6px; padding: 6px 8px;
  background: #f8f9fa; border-radius: 4px;
  font-size: 11px; line-height: 1.7;
}
.dfm-quick-label {
  display: inline-block; font-weight: 600; color: var(--muted);
  margin-right: 4px;
}
.dfm-quick-edit :deep(.el-checkbox) {
  margin-right: 8px; height: 20px;
}
.dfm-quick-edit :deep(.el-checkbox__label) {
  font-size: 11px; padding-left: 4px;
}
.dfm-quick-hint {
  font-size: 10px; color: #b45309; font-style: italic;
}

/* ═══════════ ① 接口节点卡片 v2（重新设计）═══════════ */
.dfm-api-legend {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 6px 10px; margin-bottom: 8px;
  font-size: 11px; color: var(--muted);
  background: #f1f5ff; border-radius: 6px;
}
.dfm-api-legend .lg-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle;
}
.dfm-api-legend .lg-dot-on { background: #2b8a3e; }
.dfm-api-legend .lg-dot-off { background: #ced4da; }
.dfm-api-legend .lg-hint { color: #4263eb; font-weight: 500; }

.dfm-api-card-v2 {
  padding: 0 !important;
  overflow: hidden;
}
.dfm-api-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 10px; padding: 10px 12px;
  background: linear-gradient(135deg, #eef2ff 0%, #fff 100%);
  border-bottom: 1px solid #e9ecef;
}
.dfm-api-header-left { display: flex; gap: 8px; align-items: flex-start; flex: 1; min-width: 0; }
.dfm-api-icon { font-size: 18px; line-height: 1.2; flex-shrink: 0; margin-top: 2px; }
.dfm-api-title-row {
  display: flex; align-items: center; flex-wrap: wrap; gap: 4px 6px; margin-bottom: 2px;
}
.dfm-api-desc-line {
  font-size: 11px; color: var(--muted); line-height: 1.4; word-break: break-all;
}
.dfm-api-coverage {
  flex-shrink: 0; text-align: center; min-width: 64px;
}
.dfm-cov-num {
  font-size: 18px; line-height: 1.1; color: #2b8a3e; font-weight: 700;
}
.dfm-cov-num strong { font-size: 22px; }
.dfm-cov-total { font-size: 12px; color: #adb5bd; font-weight: 500; }
.dfm-cov-bar {
  height: 4px; background: #e9ecef; border-radius: 2px; overflow: hidden;
  margin: 4px 0 2px;
}
.dfm-cov-bar-fill {
  height: 100%; background: linear-gradient(90deg, #51cf66, #2b8a3e);
  transition: width .3s;
}
.dfm-cov-label { font-size: 10px; color: var(--muted); }

.dfm-slot-checklist { padding: 8px 10px; }
.dfm-slot-checklist-title {
  font-size: 11px; font-weight: 600; color: var(--muted);
  margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px dashed #f1f3f5;
}

.dfm-slot-row {
  border: 1px solid #e9ecef; border-radius: 6px; margin-bottom: 5px;
  background: #fafbfc; transition: all .15s;
}
.dfm-slot-row.is-on {
  background: #f4fcf6; border-color: #c3fae8;
}
.dfm-slot-row.is-expanded {
  background: #fff; border-color: #74c0fc; box-shadow: 0 2px 6px rgba(74,144,226,.08);
}

.dfm-slot-row-main {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 8px; cursor: pointer; user-select: none;
}
.dfm-slot-row-main:hover { background: rgba(66,99,235,.04); }

.dfm-slot-row-label {
  font-weight: 600; font-size: 12px; color: var(--text); flex-shrink: 0;
}
.dfm-slot-row.is-on .dfm-slot-row-label { color: #2b8a3e; }
.dfm-slot-row-code {
  font-family: monospace; font-size: 10px; color: #868e96;
  padding: 1px 5px; background: #f1f3f5; border-radius: 3px; flex-shrink: 0;
}
.dfm-slot-row-empty {
  font-size: 11px; color: #adb5bd; font-style: italic; margin-left: auto;
}
.dfm-slot-row-summary {
  display: inline-flex; flex-wrap: wrap; gap: 3px; align-items: center;
  margin-left: auto;
}
.dfm-rule-mini {
  display: inline-block; font-size: 10px; padding: 1px 6px;
  border-radius: 3px; line-height: 1.5; font-weight: 500;
  background: #e7f5ff; color: #1864ab; border: 1px solid #d0ebff;
}
.dfm-rule-mini.rule-filter_include { background: #e7f5ff; color: #1864ab; border-color: #d0ebff; }
.dfm-rule-mini.rule-filter_exclude { background: #fff5f5; color: #c92a2a; border-color: #ffe3e3; }
.dfm-rule-mini.rule-passthrough    { background: #ebfbee; color: #2b8a3e; border-color: #d3f9d8; }
.dfm-rule-mini.rule-extract_only   { background: #f8f9fa; color: #495057; border-color: #dee2e6; }
.dfm-rule-mini.rule-unit           { background: #fff8e1; color: #b45309; border-color: #ffe066; }

.dfm-slot-row-caret {
  color: #adb5bd; transition: transform .2s; flex-shrink: 0;
}
.dfm-slot-row-caret.rotate { transform: rotate(90deg); }

.dfm-slot-row-detail {
  border-top: 1px dashed #dee2e6; padding: 8px 10px 10px;
  background: #fdfdfd;
}
.dfm-detail-rule { padding: 4px 0; }
.dfm-detail-rule + .dfm-detail-rule {
  border-top: 1px dotted #f1f3f5; margin-top: 4px; padding-top: 8px;
}
.dfm-detail-rule-head {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  margin-bottom: 4px;
}
.dfm-detail-fields {
  display: flex; flex-wrap: wrap; gap: 3px; margin-top: 2px;
}

/* ── Banner 链接 ── */
.dfm-banner-link {
  margin-left: 8px; color: var(--primary); cursor: pointer;
  font-weight: 600; text-decoration: underline dotted;
}
.dfm-banner-link:hover { color: #1864ab; }

/* ── 术语速查条 ── */
.dfm-glossary-bar {
  background: #fafafa; border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 14px; margin-bottom: 12px;
  font-size: 12px;
}
.dfm-glossary-bar > summary {
  cursor: pointer; font-weight: 600; color: var(--primary);
  list-style: none; padding: 4px 0;
}
.dfm-glossary-bar > summary::-webkit-details-marker { display: none; }
.dfm-glossary-bar > summary::before {
  content: '▶'; display: inline-block; margin-right: 6px;
  transition: transform .15s; font-size: 9px;
}
.dfm-glossary-bar[open] > summary::before { transform: rotate(90deg); }
.dfm-glossary-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 8px 14px; margin-top: 8px; padding-top: 8px;
  border-top: 1px dashed var(--border); line-height: 1.7;
  color: var(--muted);
}
.dfm-glossary-grid strong { color: #1864ab; }
.dfm-glossary-grid code {
  background: #f1f3f5; padding: 1px 5px; border-radius: 3px;
  font-family: monospace; font-size: 11px; color: #495057;
}

/* ── 三步递进可视化 ── */
.dfm-step-flow {
  display: flex; flex-direction: column; gap: 4px;
  background: linear-gradient(180deg, #f8faff 0%, #fff 100%);
  border: 1px solid #e7f0ff; border-radius: 8px;
  padding: 10px 12px;
}
.dfm-step {
  display: flex; gap: 10px; align-items: flex-start;
}
.dfm-step-num {
  flex-shrink: 0;
  width: 22px; height: 22px; border-radius: 50%;
  background: #4263eb; color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700;
  margin-top: 1px;
}
.dfm-step-body { flex: 1; min-width: 0; }
.dfm-step-title {
  font-size: 12px; font-weight: 700; color: var(--text);
  margin-bottom: 4px;
}
.dfm-step-content {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px;
  font-size: 11px;
}
.dfm-step-label {
  font-size: 10px; color: var(--muted); font-weight: 500;
}
.dfm-step-code {
  font-family: monospace; font-size: 11px;
  background: #fff; border: 1px solid #c5d4fc; color: #1864ab;
  padding: 2px 6px; border-radius: 3px; font-weight: 600;
}
.dfm-step-from {
  font-size: 11px; color: var(--muted);
}
.dfm-step-from code {
  font-family: monospace; background: #f1f3f5;
  padding: 1px 5px; border-radius: 3px; color: #495057;
}
.dfm-step-from-empty {
  font-size: 11px; color: #adb5bd; font-style: italic;
}
.dfm-step-rule-desc { font-size: 11px; color: var(--text); }
.dfm-step-rule-desc strong { color: #1864ab; }
.dfm-step-fields-label {
  font-size: 10px; color: var(--muted); font-weight: 600;
  display: block; margin-bottom: 4px;
}
.dfm-step-arrow {
  color: #4263eb; font-size: 16px; font-weight: 700;
  margin-left: 11px; line-height: 1; padding: 2px 0;
}
.dfm-help-icon {
  color: #adb5bd; font-size: 12px; cursor: help;
}
.dfm-help-icon:hover { color: #4263eb; }

/* ── 接口配置目标说明条 ── */
.api-goal-banner {
  background: linear-gradient(90deg, #f4f9ff 0%, #fff 100%);
  border: 1px solid #c5d4fc; border-radius: 10px;
  padding: 12px 16px; margin-bottom: 12px;
}
.api-goal-banner-title {
  font-size: 13px; color: var(--text); margin-bottom: 10px;
}
.api-goal-icon { font-size: 16px; margin-right: 4px; }
.api-goal-banner-flow {
  display: flex; align-items: stretch; gap: 6px; margin-bottom: 8px;
}
.api-goal-step {
  flex: 1; padding: 8px 10px; background: #fff;
  border: 1px solid #dee2e6; border-radius: 6px;
  font-size: 12px; font-weight: 600; color: var(--muted);
  text-align: center; line-height: 1.4;
}
.api-goal-step small { font-size: 10px; font-weight: 400; color: #adb5bd; }
.api-goal-step-cur {
  border-color: #4263eb; background: #eef2ff; color: #1864ab;
  box-shadow: 0 2px 6px rgba(66,99,235,0.12);
}
.api-goal-arrow {
  display: flex; align-items: center; color: #4263eb;
  font-size: 16px; font-weight: 700;
}
.api-goal-banner-hint {
  font-size: 11px; color: var(--muted);
  padding-top: 6px; border-top: 1px dashed #e9ecef;
}

/* ── 接口产出列 ── */
.api-output-cell { display: flex; flex-direction: column; gap: 3px; font-size: 11px; }
.api-output-line { display: flex; flex-wrap: wrap; align-items: center; gap: 3px; }
.api-output-label { font-size: 10px; color: var(--muted); font-weight: 600; flex-shrink: 0; }
.api-output-tag-field { font-family: monospace; }
.api-output-tag-slot { font-weight: 600; }
.api-output-more { font-size: 10px; color: #4263eb; font-weight: 600; }
.api-output-empty {
  font-size: 11px; color: #b45309;
  background: #fff8e1; padding: 2px 6px; border-radius: 3px;
  border: 1px solid #ffe066; display: inline-block;
}
.api-output-empty-pure { font-size: 11px; color: #adb5bd; font-style: italic; }

/* ═══════════ 接口编辑弹窗 — 顶部 wizard 引导条 ═══════════ */
.ifc-wizard-banner {
  background: linear-gradient(90deg, #f4f9ff 0%, #fff 100%);
  border: 1px solid #c5d4fc; border-radius: 10px;
  padding: 14px 16px 12px; margin-bottom: 16px;
}
.ifc-wizard-title {
  font-size: 14px; font-weight: 700; color: var(--text);
  margin-bottom: 12px; display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
}
.ifc-wizard-title small {
  font-size: 11px; font-weight: 400; color: var(--muted);
}
.ifc-wizard-flow {
  display: flex; align-items: stretch; gap: 6px;
}
.ifc-wizard-step {
  flex: 1; cursor: pointer; padding: 10px 12px;
  background: #fff; border: 2px solid #dee2e6; border-radius: 8px;
  transition: all .15s; position: relative;
  display: flex; flex-direction: column; gap: 2px;
}
.ifc-wizard-step:hover { border-color: #adc1ff; background: #f8faff; }
.ifc-wizard-step.cur {
  border-color: #4263eb; background: #eef2ff;
  box-shadow: 0 4px 12px rgba(66,99,235,0.18);
}
.ifc-wizard-step.done {
  border-color: #2b8a3e; background: #f4fcf6;
}
.ifc-wizard-step.done.cur {
  border-color: #4263eb; background: #eef2ff;
}
.ifc-wizard-no {
  font-size: 18px; font-weight: 700; color: #4263eb;
  position: absolute; right: 8px; top: 6px; opacity: 0.4;
}
.ifc-wizard-step.cur .ifc-wizard-no { opacity: 1; }
.ifc-wizard-step.done .ifc-wizard-no { color: #2b8a3e; opacity: 1; }
.ifc-wizard-step.done .ifc-wizard-no::after { content: ' ✓'; font-size: 14px; }
.ifc-wizard-name { font-size: 13px; font-weight: 700; color: var(--text); }
.ifc-wizard-step.cur .ifc-wizard-name { color: #1864ab; }
.ifc-wizard-desc { font-size: 11px; color: var(--muted); line-height: 1.4; }
.ifc-wizard-tag {
  align-self: flex-start; font-size: 10px; padding: 1px 6px;
  background: #f1f3f5; color: #495057; border-radius: 3px;
  font-weight: 600; margin-top: 2px;
}
.ifc-wizard-step.cur .ifc-wizard-tag { background: #4263eb; color: #fff; }
.ifc-wizard-step.done .ifc-wizard-tag { background: #2b8a3e; color: #fff; }
.ifc-wizard-arrow {
  display: flex; align-items: center; justify-content: center;
  color: #4263eb; font-size: 18px; font-weight: 700;
  flex-shrink: 0; width: 18px;
}
.ifc-wizard-output {
  margin-top: 12px; padding: 8px 10px;
  background: #fff; border: 1px dashed #c5d4fc; border-radius: 6px;
  font-size: 11px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
}
.ifc-wizard-output-label {
  font-weight: 600; color: var(--muted); margin-right: 4px;
}
.ifc-wizard-output-tag { font-family: monospace; }
.ifc-wizard-output-more { color: #4263eb; font-weight: 600; }
.ifc-wizard-output-empty { color: #adb5bd; font-style: italic; }

/* 朴素步骤栏 */
.ifc-steps {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 8px 10px; margin-bottom: 12px;
  background: #fafafa; border: 1px solid var(--border); border-radius: 6px;
  font-size: 12px;
}
.ifc-step {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; border-radius: 4px;
  background: transparent; border: 1px solid transparent;
  color: var(--muted); cursor: pointer; font: inherit;
}
.ifc-step:hover { color: var(--text); }
.ifc-step.cur {
  background: #fff; border-color: var(--border);
  color: var(--text); font-weight: 600;
}
.ifc-step.done .ifc-step-no { color: #2b8a3e; }
.ifc-step.done.cur { border-color: #c3fae8; }
.ifc-step-no {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 3px;
  background: #e9ecef; color: #495057;
  font-size: 10px; font-weight: 700;
}
.ifc-step.cur .ifc-step-no { background: #495057; color: #fff; }
.ifc-step.done .ifc-step-no { background: #d3f9d8; color: #2b8a3e; }
.ifc-step-line { flex: 0 0 16px; height: 1px; background: #dee2e6; }
.ifc-step-out {
  margin-left: auto; font-size: 11px; color: var(--muted);
  display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap;
}
.ifc-step-out code {
  font-family: monospace; font-size: 10px; padding: 1px 5px;
  background: #fff; border: 1px solid #e9ecef; border-radius: 3px;
  color: #495057;
}
.ifc-step-out-empty { color: #adb5bd; font-style: italic; }

/* 优化1：产出预览区（始终可见的 7 大标准域覆盖度） */
.ifc-produce-preview {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  padding: 8px 12px; margin: 0 0 12px;
  background: #fafbff; border: 1px solid #e3e8f5; border-radius: 6px;
}
.ipp-label { font-size: 12px; color: var(--muted); margin-right: 2px; }
.ipp-chip {
  font-size: 12px; color: #adb5bd; background: #f3f4f6;
  border: 1px solid #e5e7eb; border-radius: 12px; padding: 2px 10px;
  display: inline-flex; align-items: center; gap: 3px;
}
.ipp-chip .ipp-mark { font-size: 11px; }
.ipp-chip.on {
  color: #16794c; background: #e8f8ef; border-color: #a7e0c1; font-weight: 600;
}
.ipp-count { margin-left: auto; font-size: 12px; color: var(--muted); font-weight: 600; }

/* 优化1：智能分析结果 —— 产出标准域（分析后展示） */
.ifc-analysis-result {
  margin-top: 10px; padding: 10px 12px;
  background: linear-gradient(180deg,#f0f7ff 0%,#f7fbff 100%);
  border: 1px solid #cfe0f8; border-radius: 8px;
}
.iar-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.iar-badge {
  font-size: 11px; font-weight: 700; color: #1d4ed8; background: #dbeafe;
  border-radius: 10px; padding: 1px 8px;
}
.iar-title { font-size: 13px; font-weight: 600; color: #1f2d5c; }
.iar-count { margin-left: auto; font-size: 12px; color: #6b7280; font-weight: 600; }
.iar-count.full { color: #16794c; }
.iar-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ifc-analysis-empty {
  margin-top: 10px; padding: 12px 14px; font-size: 12px; line-height: 1.7;
  color: #8a94a6; background: #fafbfc; border: 1px dashed #d9dee8; border-radius: 8px;
}
.ifc-analysis-empty b { color: #5b6472; }
.iae-icon { margin-right: 4px; }

/* 优化2：出参映射说明（步骤化引导） */
.om-hint-lead {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  background: #f0f7ff; border-color: #cfe0f8; color: #33507f;
}
.om-hint-step {
  font-size: 11px; font-weight: 700; color: #1d4ed8; background: #dbeafe;
  border-radius: 10px; padding: 1px 8px;
}
.om-hint-arrow { color: #9db8e0; font-weight: 700; }
.om-hint-sub { margin-top: 6px; }

/* 优化3：_unit_conversions 单位换算规则 */
.ft-unit-box {
  margin-top: 10px; padding: 8px 12px;
  background: #fff8ec; border: 1px solid #ffe0a8; border-radius: 6px;
}
.ft-unit-head { display: flex; align-items: center; gap: 10px; }
.ft-unit-title { font-size: 12px; font-weight: 600; color: #a15c00; }
.ft-unit-help { font-size: 11px; color: #b8860b; cursor: help; text-decoration: underline dotted; }
.ft-unit-del { margin-left: 4px; }
.ft-unit-table { margin-top: 8px; background: transparent; }
.ft-unit-table :deep(.el-table__cell) { padding: 4px 0; }
.ft-unit-empty {
  margin-top: 8px; padding: 8px 10px; font-size: 12px; line-height: 1.6;
  color: #a17a2e; background: #fffdf7; border: 1px dashed #ffe0a8; border-radius: 4px;
}

/* 713_3：映射结果弹窗头部 */
.mapres-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin: 4px 0 10px;
}
.mapres-desc { font-size: 12px; color: var(--muted); }
.mapres-cov {
  margin-left: auto; font-size: 12px; font-weight: 600; color: #16794c;
  background: #e6f6ee; border-radius: 10px; padding: 1px 10px;
}

/* 优化4：全部映射域一览 */
.ft-alldomain { margin-top: 12px; }
.ft-alldomain-hd {
  font-size: 12px; font-weight: 600; color: #495057; margin-bottom: 6px;
  display: flex; align-items: center;
}
.ft-alldomain-count { margin-left: auto; font-size: 12px; color: var(--muted); font-weight: 600; }
.ft-alldomain-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 6px;
}
.ft-ad-item {
  display: flex; align-items: center; gap: 6px; font-size: 12px;
  padding: 5px 10px; border: 1px solid #e5e7eb; border-radius: 6px;
  background: #f8f9fa; color: #adb5bd;
}
.ft-ad-item.on { background: #e8f8ef; border-color: #a7e0c1; color: #16794c; }
.ft-ad-mark { font-size: 11px; }
.ft-ad-label { font-weight: 600; }
.ft-ad-src { margin-left: auto; font-size: 11px; font-weight: 400; }
.ft-ad-src.muted { color: #c3c8d0; }

/* 朴素提示条 */
.ifc-hint {
  font-size: 12px; color: var(--muted); line-height: 1.7;
  padding: 8px 12px; margin-bottom: 10px;
  background: #fafafa; border: 1px solid var(--border); border-radius: 6px;
}
.ifc-hint b { color: var(--text); }
.ifc-hint code {
  font-family: monospace; font-size: 11px; color: #495057;
  background: #fff; border: 1px solid #e9ecef;
  padding: 0 5px; border-radius: 3px;
}
.ifc-link { color: var(--primary); cursor: pointer; }
.ifc-link:hover { text-decoration: underline; }

.ifc-step-hint {
  font-size: 12px; color: var(--text); margin-bottom: 12px;
  padding: 10px 12px; line-height: 1.7;
  background: #fff8e1; border: 1px solid #ffe066; border-radius: 7px;
}

.ifc-step-hint strong { color: #b45309; font-size: 13px; }
.ifc-step-hint code {
  font-family: monospace; background: #fff; padding: 1px 5px;
  border-radius: 3px; color: #1864ab; font-size: 11px;
}
.ifc-step-output-hint {
  display: block; margin-top: 6px; padding-top: 6px;
  border-top: 1px dashed #ffd43b; font-size: 11px; color: #495057;
}
.ifc-mock-tip {
  margin-left: 10px; font-size: 11px; color: var(--muted); font-style: italic;
}
.ifc-step-next {
  margin-top: 12px; padding: 10px 12px;
  background: #e6fcf5; border: 1px solid #96f2d7; border-radius: 7px;
  font-size: 12px; color: #2b8a3e;
}
.ifc-step-next a {
  color: #1864ab; cursor: pointer; font-weight: 600;
  text-decoration: underline;
}
.ifc-step-next a:hover { color: #0c5392; }

/* ═══════════ 标准数据关联 tab（简洁朴素版）═══════════ */
.dl-hint {
  font-size: 13px; color: var(--muted);
  padding: 10px 14px; margin-bottom: 12px;
  background: #fafafa; border: 1px solid var(--border); border-radius: 6px;
  line-height: 1.6;
}
.dl-hint a { color: var(--primary); cursor: pointer; margin-left: 8px; }
.dl-hint a:hover { text-decoration: underline; }

.dl-slot-overview {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 6px; margin-bottom: 14px;
}
.dl-slot-chip {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; background: #fff;
  border: 1px solid var(--border); border-radius: 4px;
  font-size: 12px;
}
.dl-slot-chip.supplied { border-left: 3px solid #2b8a3e; }
.dl-slot-chip.unmet    { border-left: 3px solid #c92a2a; }
.dl-slot-chip-icon {
  width: 14px; flex-shrink: 0; text-align: center;
  font-weight: 700; font-size: 12px; color: #adb5bd;
}
.dl-slot-chip.supplied .dl-slot-chip-icon { color: #2b8a3e; }
.dl-slot-chip.unmet .dl-slot-chip-icon    { color: #c92a2a; }
.dl-slot-chip-label { color: var(--text); flex: 1; }
.dl-slot-chip-count { font-size: 11px; color: var(--muted); }


.dl-card-list {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 14px;
}
.dl-card {
  border: 1px solid var(--border); border-radius: 10px;
  background: #fff; overflow: hidden;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.dl-card-head {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 10px 14px;
  background: #fafafa;
  border-bottom: 1px solid #e9ecef;
}
.dl-card-head-main { flex: 1; min-width: 0; }
.dl-card-title {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px;
  margin-bottom: 2px;
}
.dl-card-cov {
  flex-shrink: 0; padding: 3px 10px;
  background: #fff; border: 1px solid var(--border); border-radius: 12px;
  font-size: 11px; color: var(--muted);
}
.dl-card-edit { flex-shrink: 0; margin-left: 4px; }
.dl-card-cov strong { font-size: 13px; font-weight: 700; color: var(--text); }


.dl-block-title {
  font-size: 12px; font-weight: 700; color: var(--text);
  margin-bottom: 8px; display: flex; align-items: center; gap: 6px;
}
.dl-block-num {
  width: 18px; height: 18px; border-radius: 4px;
  background: #4263eb; color: #fff;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
}

.dl-source-block {
  padding: 12px 14px;
  background: #fafbfc;
  border-bottom: 1px dashed #e9ecef;
}
.dl-source-fields {
  display: flex; flex-wrap: wrap; gap: 4px;
  min-height: 24px;
}

.dl-arrow-down {
  text-align: center; color: #4263eb;
  font-size: 12px; font-weight: 600; padding: 4px 0;
  background: #f8faff;
}

.dl-target-block { padding: 12px 14px; }
.dl-slot-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 6px;
}
.dl-slot-cell {
  display: flex; align-items: flex-start; gap: 6px;
  padding: 8px 10px;
  border: 1px solid #e9ecef; border-radius: 6px;
  background: #fff; cursor: pointer;
  transition: all .15s;
}
.dl-slot-cell:hover { border-color: #adc1ff; background: #f8faff; }
.dl-slot-cell.checked {
  border-color: #c3fae8; background: #f4fcf6;
  box-shadow: 0 1px 3px rgba(43,138,62,0.08);
}
.dl-slot-cell-body { flex: 1; min-width: 0; }
.dl-slot-cell-label {
  font-size: 12px; font-weight: 600; color: var(--text);
}
.dl-slot-cell.checked .dl-slot-cell-label { color: #2b8a3e; }
.dl-slot-cell-key {
  font-family: monospace; font-size: 10px; color: #868e96;
  margin-top: 1px;
}
.dl-slot-cell-summary {
  display: flex; flex-wrap: wrap; gap: 3px; margin-top: 4px;
}

.dl-tip {
  margin-top: 10px; padding: 6px 10px;
  font-size: 11px; color: var(--muted); line-height: 1.6;
  background: #fafafa; border-radius: 4px;
  border-left: 2px solid #ced4da;
}

/* ═══════════ 出参映射 step ③ — 三阶段流程 ═══════════ */
.om-flow {
  display: flex; flex-direction: column; gap: 0;
  margin-top: 4px;
}
.om-node {
  border: 2px solid #dee2e6; border-radius: 10px;
  background: #fff; padding: 12px 14px;
  transition: all .15s;
}
.om-node-input    { border-color: #74c0fc; background: linear-gradient(135deg, #f4faff 0%, #fff 100%); }
.om-node-extract  { border-color: #ffd43b; background: linear-gradient(135deg, #fffbe6 0%, #fff 100%); }
.om-node-transform { border-color: #51cf66; background: linear-gradient(135deg, #f4fcf6 0%, #fff 100%); }

.om-node-head {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  margin-bottom: 8px;
}
.om-badge {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 11px; font-weight: 700; color: #fff; flex-shrink: 0;
}
.om-badge-input { background: #228be6; }
.om-badge-b     { background: #f59f00; }
.om-badge-c     { background: #2b8a3e; }
.om-node-title { font-size: 13px; font-weight: 700; color: var(--text); flex: 1; min-width: 0; }
.om-node-title .om-mono {
  font-family: monospace; font-size: 12px; font-weight: 600;
  background: #f1f3f5; padding: 1px 6px; border-radius: 3px;
  color: #1864ab; margin-left: 4px;
}
.om-keys-count { flex-shrink: 0; }
.om-mono {
  font-family: monospace; background: #f1f3f5; padding: 1px 5px;
  border-radius: 3px; color: #495057; font-size: 11px;
}

.om-node-desc {
  font-size: 12px; color: var(--muted); line-height: 1.7;
  padding: 8px 10px; background: rgba(255,255,255,0.7);
  border-radius: 6px; margin-bottom: 8px;
}
.om-node-desc strong { color: var(--text); }
.om-node-desc code {
  font-family: monospace; background: #fff; padding: 1px 5px;
  border-radius: 3px; color: #1864ab; font-size: 11px;
  border: 1px solid #e9ecef;
}

.om-action-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-top: 8px;
}
.om-analysis-msg {
  font-size: 11px; color: #b45309;
  background: #fff8e1; padding: 4px 8px; border-radius: 4px;
  border: 1px solid #ffe066;
}

.om-arrow {
  display: flex; flex-direction: column; align-items: center;
  padding: 6px 0; gap: 2px;
}
.om-arrow-line {
  width: 2px; height: 14px; background: linear-gradient(180deg, #adb5bd, #4263eb);
}
.om-arrow-label {
  font-size: 11px; color: #4263eb; font-weight: 600;
  background: #eef2ff; padding: 2px 10px; border-radius: 10px;
  border: 1px solid #c5d4fc;
}
.om-arrow-tip {
  color: #4263eb; font-size: 14px; font-weight: 700;
}

.om-keys-preview {
  margin-top: 8px; padding: 8px 10px;
  background: rgba(255,255,255,0.7); border-radius: 6px;
  font-size: 11px;
}
.om-keys-label {
  font-weight: 600; color: var(--muted); margin-right: 6px;
}
.om-key-tag {
  font-family: monospace; margin: 2px 3px 2px 0 !important;
}
.om-keys-warn {
  margin-top: 6px; padding-top: 6px;
  border-top: 1px dashed #ffd43b;
  color: #c92a2a; font-weight: 500;
}

.om-example-fold {
  margin-bottom: 8px;
  border: 1px dashed #c3fae8; border-radius: 6px;
  padding: 6px 10px; background: rgba(255,255,255,0.6);
}
.om-example-fold > summary {
  cursor: pointer; font-size: 12px; font-weight: 600;
  color: #2b8a3e; list-style: none; padding: 2px 0;
}
.om-example-fold > summary::-webkit-details-marker { display: none; }
.om-example-fold > summary::before {
  content: '▶'; display: inline-block; margin-right: 6px;
  transition: transform .15s; font-size: 9px;
}
.om-example-fold[open] > summary::before { transform: rotate(90deg); }
.om-example-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px; margin-top: 10px;
}
.om-example-card {
  background: #fff; border: 1px solid #e9ecef;
  border-radius: 6px; padding: 8px 10px;
}
.om-example-tag {
  display: inline-block; padding: 2px 8px; border-radius: 3px;
  font-size: 11px; font-weight: 700; margin-bottom: 6px;
}
.om-tag-pass { background: #ebfbee; color: #2b8a3e; border: 1px solid #c3fae8; }
.om-tag-incl { background: #e7f5ff; color: #1864ab; border: 1px solid #d0ebff; }
.om-tag-excl { background: #fff5f5; color: #c92a2a; border: 1px solid #ffe3e3; }
.om-example-pre {
  margin: 0; padding: 8px 10px; background: #0f172a; color: #e2e8f0;
  font-family: monospace; font-size: 11px; line-height: 1.5;
  border-radius: 4px; overflow-x: auto;
}
.om-example-note {
  font-size: 11px; color: var(--muted); line-height: 1.5;
  margin-top: 6px; padding-top: 6px; border-top: 1px dashed #f1f3f5;
}

.om-final {
  margin-top: 10px; padding: 12px 14px;
  background: linear-gradient(135deg, #e6fcf5 0%, #fff 100%);
  border: 2px solid #96f2d7; border-radius: 8px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  font-size: 12px;
}
.om-final-icon { font-size: 16px; }
.om-final-label { font-weight: 600; color: #2b8a3e; }
.om-final-empty { color: #adb5bd; font-style: italic; }

/* ═══════════ 数据流映射 · ① 接口节点卡片 v3（重新设计 - 扁平规则视图）═══════════ */
.dfm-legend-v3 {
  background: linear-gradient(90deg, #f4f9ff 0%, #fff 100%);
  border: 1px dashed #c5d4fc; padding: 8px 12px;
}
.dfm-legend-flow {
  display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap;
  font-size: 11px; line-height: 1.4;
}
.dfm-legend-step {
  flex: 1; min-width: 120px; padding: 6px 8px;
  background: #fff; border: 1px solid #dee2e6; border-radius: 5px;
  text-align: center;
}
.dfm-legend-step b { color: var(--text); font-size: 12px; }
.dfm-legend-step i { color: var(--muted); font-style: normal; font-size: 10px; }
.dfm-legend-step.dfm-legend-mid { background: #fffbe6; border-color: #ffd43b; }
.dfm-legend-step.dfm-legend-tgt { background: #f4fcf6; border-color: #c3fae8; }
.dfm-legend-arrow {
  display: flex; align-items: center; justify-content: center;
  color: #4263eb; font-weight: 700; font-size: 14px; flex-shrink: 0;
}

.dfm-api-card-v3 { padding: 0 !important; overflow: hidden; }

.dfm-section {
  padding: 10px 12px;
  border-top: 1px solid #f1f3f5;
}
.dfm-section:first-of-type { border-top: none; }
.dfm-section-head {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 8px;
  margin-bottom: 8px;
}
.dfm-section-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 4px;
  font-size: 11px; font-weight: 700; color: #fff;
  flex-shrink: 0;
}
.dfm-badge-1 { background: #f59f00; }
.dfm-badge-2 { background: #2b8a3e; }
.dfm-section-title { font-size: 12px; font-weight: 700; color: var(--text); }
.dfm-section-sub { font-size: 11px; color: var(--muted); }

/* 中间数据集 chip */
.dfm-extract-list {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 6px 8px; background: #fffbe6;
  border: 1px dashed #ffe066; border-radius: 6px;
  min-height: 30px;
}
.dfm-extract-chip {
  display: inline-flex; align-items: center; gap: 4px;
  background: #fff; border: 1px solid #ffd43b; border-radius: 4px;
  padding: 3px 8px; font-size: 11px;
}
.dfm-extract-key {
  font-family: monospace; color: #b45309; font-weight: 700;
  background: transparent; padding: 0;
}
.dfm-extract-from { color: #adb5bd; font-size: 10px; }
.dfm-extract-path {
  font-family: monospace; color: #495057; font-size: 10px;
  background: #f8f9fa; padding: 1px 5px; border-radius: 3px;
}
.dfm-extract-empty {
  font-size: 11px; color: #adb5bd; font-style: italic;
  padding: 4px 8px;
}

/* 规则行（扁平一行一规则）*/
.dfm-rule-list { display: flex; flex-direction: column; gap: 3px; }
.dfm-rule-row {
  background: #f4fcf6;
  border: 1px solid #c3fae8;
  border-radius: 6px;
  overflow: hidden;
  transition: all .15s;
}
.dfm-rule-row.is-expanded {
  background: #fff;
  border-color: #74c0fc;
  box-shadow: 0 2px 6px rgba(74,144,226,.08);
}
.dfm-rule-row-main {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; cursor: pointer; user-select: none;
  font-size: 11px; flex-wrap: wrap;
}
.dfm-rule-row-main:hover { background: rgba(43,138,62,.04); }
.dfm-rule-src {
  flex-shrink: 0;
}
.dfm-rule-src code {
  font-family: monospace; background: #fff8e1;
  border: 1px solid #ffd43b; color: #b45309;
  padding: 2px 6px; border-radius: 3px;
  font-weight: 600; font-size: 11px;
}
.dfm-rule-arrow { color: #4263eb; font-weight: 700; flex-shrink: 0; }
.dfm-rule-tag-flat { font-size: 10px !important; height: 18px !important; line-height: 18px !important; flex-shrink: 0; }
.dfm-rule-unit-mark {
  color: #b45309; font-size: 11px; flex-shrink: 0;
  background: #fff8e1; border-radius: 3px; padding: 0 4px;
}
.dfm-rule-tgt {
  display: inline-flex; align-items: center; gap: 4px;
  flex-shrink: 0;
}
.dfm-rule-tgt-label {
  font-weight: 700; color: #2b8a3e; font-size: 12px;
}
.dfm-rule-tgt-code {
  font-family: monospace; font-size: 10px; color: #495057;
  background: #fff; border: 1px solid #c3fae8;
  padding: 1px 5px; border-radius: 3px;
}
.dfm-rule-actions {
  margin-left: auto; display: inline-flex; align-items: center; gap: 4px;
  flex-shrink: 0;
}
.dfm-rule-caret {
  color: #adb5bd; transition: transform .2s;
}
.dfm-rule-caret.rotate { transform: rotate(90deg); }
.dfm-rule-del { padding: 2px 6px !important; }

.dfm-rule-row-detail {
  border-top: 1px dashed #c3fae8;
  padding: 8px 12px 10px;
  background: #fafffc;
}
.dfm-rule-explain {
  font-size: 11px; color: var(--text); line-height: 1.7;
  padding: 6px 8px; background: #fff;
  border-left: 3px solid #2b8a3e; border-radius: 3px;
  margin-bottom: 6px;
}
.dfm-rule-explain code {
  font-family: monospace; background: #f1f3f5;
  padding: 1px 5px; border-radius: 3px; color: #1864ab;
  font-size: 10px;
}
.dfm-rule-explain strong { color: #b45309; }
.dfm-rule-fields {
  display: flex; flex-wrap: wrap; gap: 3px; align-items: center;
  margin-top: 4px;
}
.dfm-rule-fields-label {
  font-size: 10px; color: var(--muted); font-weight: 600;
  flex-shrink: 0;
}

.dfm-rule-empty {
  font-size: 11px; color: #adb5bd; font-style: italic;
  padding: 8px 10px; text-align: center;
  background: #fafafa; border-radius: 6px; border: 1px dashed #dee2e6;
}

/* "+ 添加映射" 行 */
.dfm-unmapped-row {
  margin-top: 8px; padding: 6px 8px;
  background: #fafafa; border: 1px dashed #dee2e6; border-radius: 6px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px;
}
.dfm-unmapped-label {
  font-size: 11px; color: var(--muted); font-weight: 600;
  flex-shrink: 0;
}
.dfm-unmapped-chip {
  cursor: pointer; transition: all .15s;
  font-size: 11px !important;
}
.dfm-unmapped-chip:hover {
  background: #eef2ff !important; border-color: #4263eb !important;
  color: #1864ab !important;
}
.dfm-unmapped-code {
  font-family: monospace; font-size: 10px; color: #adb5bd;
  margin-left: 2px;
}

.dfm-card-foot-stat { color: #4263eb; font-weight: 500; }

/* ═══ 标准域分组色块（接口卡片新版）═══ */
.dfm-slot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 8px;
}
.dfm-slot-box {
  border: 1px solid #e9ecef; border-radius: 6px;
  background: #fff; overflow: hidden;
  transition: all .15s;
}
.dfm-slot-box.mapped {
  border-color: #c3fae8; background: #f4fcf6;
}
.dfm-slot-box.empty {
  border-style: dashed; background: #fafafa;
}
.dfm-slot-box-head {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid #f1f3f5;
}
.dfm-slot-box.empty .dfm-slot-box-head {
  border-bottom-color: transparent;
}
.dfm-slot-box-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 4px;
  font-size: 12px; font-weight: 700; flex-shrink: 0;
  background: #e9ecef; color: #adb5bd;
}
.dfm-slot-box.mapped .dfm-slot-box-icon {
  background: #2b8a3e; color: #fff;
}
.dfm-slot-box-label {
  font-size: 12px; font-weight: 700; color: var(--text);
}
.dfm-slot-box.mapped .dfm-slot-box-label { color: #2b8a3e; }
.dfm-slot-box-key {
  font-family: monospace; font-size: 10px; color: #868e96;
  background: #f1f3f5; padding: 1px 6px; border-radius: 3px;
}
.dfm-slot-box-del, .dfm-slot-box-add {
  margin-left: auto !important; padding: 0 6px !important;
  font-size: 11px !important;
}
.dfm-slot-box-body {
  padding: 6px 10px;
  display: flex; flex-direction: column; gap: 6px;
}
.dfm-slot-rule {
  font-size: 11px;
}
.dfm-slot-rule + .dfm-slot-rule {
  padding-top: 6px; border-top: 1px dotted #e9ecef;
}
.dfm-slot-rule-line {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px;
  margin-bottom: 4px;
}
.dfm-slot-rule-from-label {
  font-size: 10px; color: var(--muted); font-weight: 600;
}
.dfm-slot-rule-from {
  font-family: monospace; font-size: 11px;
  background: #fff8e1; border: 1px solid #ffd43b; color: #b45309;
  padding: 1px 6px; border-radius: 3px; font-weight: 600;
}
.dfm-slot-rule-sub {
  font-size: 10px; color: #1864ab;
  background: #e7f5ff; padding: 1px 5px; border-radius: 3px;
}
.dfm-slot-rule-fields {
  display: flex; flex-wrap: wrap; align-items: center; gap: 3px;
  margin-top: 2px;
}
.dfm-slot-rule-fields-label {
  font-size: 10px; color: var(--muted); font-weight: 600;
  flex-shrink: 0;
}
.dfm-slot-rule-fields-label.dfm-excl-label { color: #c92a2a; }
.dfm-slot-rule-allnote {
  font-size: 10px; color: var(--muted); font-style: italic;
}
.dfm-slot-rule-allnote code {
  font-family: monospace; font-style: normal;
  background: #f1f3f5; padding: 0 5px; border-radius: 3px;
  color: #495057; font-size: 10px;
}
.dfm-slot-box-empty {
  padding: 6px 10px;
  font-size: 10px; color: #adb5bd; font-style: italic;
}


/* ═══ 出参映射 step ③ — 朴素三段式 ═══ */
.om-hint {
  font-size: 12px; color: var(--muted); line-height: 1.7;
  padding: 8px 12px; margin-bottom: 10px;
  background: #fafafa; border: 1px solid var(--border); border-radius: 6px;
}
.om-hint b { color: var(--text); }

/* 优化2：出参映射数据流向图示 */
.om-flow-legend {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  padding: 10px 12px; margin-bottom: 10px;
  background: linear-gradient(90deg, #f5f8ff 0%, #fafafa 100%);
  border: 1px solid #dbe5ff; border-radius: 6px;
}
.ofl-node {
  font-size: 12px; color: var(--text); background: #fff;
  border: 1px solid var(--border); border-radius: 5px; padding: 3px 8px;
  display: inline-flex; flex-direction: column; line-height: 1.4;
}
.ofl-node em { font-style: normal; font-size: 10px; color: var(--muted); }
.ofl-step { border-color: #b7c9ff; }
.ofl-target { border-color: #86c8a0; background: #f0fdf4; }
.ofl-arrow { color: #9aa7c7; font-size: 10px; }

/* 优化3：field_transform 表格化配置 */
.ft-mode-switch { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.ft-mode-hint { font-size: 12px; color: var(--muted); }
.ft-table {
  border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 6px;
}
.ft-row {
  display: grid; grid-template-columns: 220px 1.2fr 110px 1.6fr 56px;
  gap: 8px; align-items: center; padding: 7px 10px; border-bottom: 1px solid #f0f0f0;
}
.ft-row:last-child { border-bottom: none; }
.ft-head {
  background: #fafbff; font-size: 12px; color: var(--muted); font-weight: 600;
}
.ft-c-slot { display: flex; gap: 4px; align-items: center; }
.ft-c-slot .el-select { flex: 1; }
.ft-subkey { width: 82px; flex: 0 0 82px; }
.ft-c-from .el-select, .ft-c-type .el-select, .ft-c-keys .el-select { width: 100%; }
.ft-dash { font-size: 12px; color: #adb5bd; }
.ft-c-op { text-align: right; }
.ft-empty { padding: 12px; font-size: 12px; color: var(--muted); text-align: center; }
.ft-add { margin: 8px 0 2px; }
/* 直接提取的标准域（response_extract 整块透传）：只读展示行 */
.ft-direct-block { border-bottom: 1px solid #e8f5ec; }
.ft-direct-row { background: #f6fdf8; font-size: 12px; }
.ft-direct-row .ft-c-slot b { font-size: 12px; color: #2f7a4d; font-weight: 600; }
.ft-direct-row .ft-c-from code {
  font-size: 12px; background: #eef7f0; border-radius: 4px; padding: 1px 6px; color: #4a6b57;
}

/* 优化5：内联验证映射结果 */
.om2-validate { border-color: #a7e0c1; background: #fafffb; }
.om2-validate-chips { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.om2 { display: flex; flex-direction: column; gap: 12px; }
.om2-sec {
  border: 1px solid var(--border); border-radius: 6px;
  background: #fff; padding: 10px 12px;
}
.om2-sec-head {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  margin-bottom: 8px;
}
.om2-no {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 4px;
  background: #495057; color: #fff;
  font-size: 11px; font-weight: 700; flex-shrink: 0;
}
.om2-title { font-size: 13px; font-weight: 700; color: var(--text); }
.om2-title code {
  font-family: monospace; font-size: 11px; color: #495057;
  background: #f1f3f5; padding: 1px 6px; border-radius: 3px;
  font-weight: 600;
}
.om2-aux { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.om2-aux-text { font-size: 11px; color: var(--muted); }
.om2-tip {
  font-size: 11px; color: var(--muted); line-height: 1.7;
  padding: 6px 10px; margin-bottom: 8px;
  background: #fafafa; border-radius: 4px;
}
.om2-tip code {
  font-family: monospace; font-size: 11px; color: #495057;
  background: #fff; border: 1px solid #e9ecef;
  padding: 0 5px; border-radius: 3px;
}
.om2-msg {
  margin-top: 6px; font-size: 11px; color: #b45309;
  padding: 6px 10px; background: #fff8e1;
  border-left: 3px solid #ffd43b; border-radius: 0 4px 4px 0;
}
.om2-fold { display: inline-block; margin-left: 6px; }
.om2-fold > summary {
  cursor: pointer; font-size: 11px; color: var(--primary);
  list-style: none;
}
.om2-fold > summary::-webkit-details-marker { display: none; }
.om2-pre {
  margin: 6px 0 0; padding: 8px 10px;
  background: #fafafa; color: #495057;
  font-family: monospace; font-size: 11px; line-height: 1.55;
  border-radius: 4px; border: 1px solid #e9ecef;
  overflow-x: auto;
}
.om2-pre-light { max-height: 220px; }
.om2-chips {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
  margin-top: 6px; font-size: 11px;
}
.om2-chips-label { color: var(--muted); margin-right: 4px; }
.om2-chip {
  font-family: monospace; font-size: 11px;
  padding: 2px 8px; border-radius: 3px;
  background: #f5f5f5; color: #495057;
  border: 1px solid #e9ecef;
}
.om2-chip-ok { background: #f4fcf6; border-color: #c3fae8; color: #2b8a3e; }
.om2-chip-bad { background: #fff5f5; border-color: #ffe3e3; color: #c92a2a; }

/* 直传透传字段勾选 */
.pt-field-grid { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.pt-field-item {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border: 1px solid #e9ecef; border-radius: 6px;
  background: #fff; cursor: pointer; transition: .15s; font-size: 12px;
}
.pt-field-item:hover { border-color: #4263eb; background: #f8f9ff; }
.pt-field-item.checked { border-color: #4263eb; background: #eef2ff; }
.pt-field-item input { flex-shrink: 0; }
.pt-field-key { font-family: monospace; font-size: 12px; color: #1c3faa; font-weight: 600; flex-shrink: 0; }
.pt-field-tag {
  flex-shrink: 0; font-size: 10px; padding: 1px 6px; border-radius: 3px;
  background: #fff3bf; color: #b45309; border: 1px solid #ffe066;
}
.pt-field-preview { color: #868e96; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.om2-warn {
  margin-top: 6px; font-size: 11px; color: #c92a2a;
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
}
.om2-chips-note { color: #adb5bd; font-size: 11px; margin-left: 4px; }
.om2-hint-inline {
  margin-top: 6px; font-size: 11px; color: #8a6d1a; line-height: 1.7;
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
  background: #fffbf0; border: 1px solid #ffe8b3; border-radius: 4px; padding: 6px 8px;
}
.om2-flow-block {
  margin-top: 8px; padding: 8px 10px;
  background: #fafafa; border-radius: 4px;
}
.om2-flow-block + .om2-flow-block { margin-top: 8px; }
.om2-flow-label {
  font-size: 11px; font-weight: 600; color: var(--muted);
  margin-bottom: 6px;
}
.om2-flow-row {
  background: #fff; border: 1px solid #e9ecef; border-radius: 4px;
  margin-bottom: 4px;
}
.om2-flow-row.is-bad { border-color: #ffe3e3; background: #fff5f5; }
.om2-flow-row > summary {
  padding: 6px 10px; cursor: pointer; user-select: none; list-style: none;
  font-size: 11px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
}
.om2-flow-row > summary::-webkit-details-marker { display: none; }
.om2-flow-row > summary::before {
  content: '▶'; font-size: 9px; color: #adb5bd;
  transition: transform .15s; flex-shrink: 0;
}
.om2-flow-row[open] > summary::before { transform: rotate(90deg); }
.om2-flow-key {
  font-family: monospace; font-weight: 700; color: #495057;
  background: transparent; padding: 0;
}
.om2-flow-sep { color: #adb5bd; }
.om2-flow-type {
  font-size: 10px; padding: 1px 6px; border-radius: 3px;
  background: #f1f3f5; color: #495057; border: 1px solid #e9ecef;
}
.om2-flow-path {
  font-family: monospace; font-size: 10px; color: #868e96;
}
.om2-flow-meta { font-size: 10px; color: #868e96; }
.om2-flow-warn { font-size: 10px; color: #c92a2a; font-weight: 600; }
.om2-flow-warn code { font-family: monospace; background: #fff0f0; padding: 0 3px; border-radius: 3px; }
/* 直连响应路径：正常形态，标绿以区别于「取不到数据」的红色告警 */
.om2-flow-ok {
  font-size: 10px; color: #2b8a3e; font-weight: 600;
  background: #ebfbee; border: 1px solid #d3f9d8;
  border-radius: 3px; padding: 0 4px;
}
.om2-flow-empty {
  font-size: 11px; color: #adb5bd; font-style: italic;
  padding: 4px 8px;
}

/* ═══ 出参映射 step ③ — 数据流可视化（智能分析返回）═══ */

.om-viz {
  margin-top: 12px; padding: 12px 14px;
  background: linear-gradient(135deg, #f8fbff 0%, #fff 100%);
  border: 2px solid #c5d4fc; border-radius: 8px;
}
.om-viz-head {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px;
  margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px dashed #c5d4fc;
}
.om-viz-title { font-size: 13px; font-weight: 700; color: #1864ab; }
.om-viz-hint { font-size: 11px; color: var(--muted); }

.om-viz-block { margin-bottom: 10px; }
.om-viz-block:last-child { margin-bottom: 0; }
.om-viz-block-head {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 700; color: var(--text);
  margin-bottom: 6px;
}
.om-viz-rows { display: flex; flex-direction: column; gap: 4px; }
.om-viz-row {
  background: #fff; border: 1px solid #dee2e6; border-radius: 6px;
  overflow: hidden; transition: all .15s;
}
.om-viz-row[open] {
  border-color: #74c0fc; box-shadow: 0 2px 6px rgba(74,144,226,.08);
}
.om-viz-row.is-invalid { border-color: #ffa8a8; background: #fff5f5; }
.om-viz-row > summary {
  padding: 7px 10px; cursor: pointer; user-select: none;
  list-style: none; font-size: 11px;
}
.om-viz-row > summary::-webkit-details-marker { display: none; }
.om-viz-row > summary::before {
  content: '▶'; display: inline-block; margin-right: 6px;
  transition: transform .15s; font-size: 9px; color: #adb5bd;
}
.om-viz-row[open] > summary::before { transform: rotate(90deg); }
.om-viz-row-head {
  display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px;
}
.om-viz-key {
  font-family: monospace; background: #fff8e1; color: #b45309;
  border: 1px solid #ffd43b; padding: 2px 6px; border-radius: 3px;
  font-weight: 700;
}
.om-viz-arrow { color: #4263eb; font-weight: 700; }
.om-viz-path {
  font-family: monospace; background: #f1f3f5; color: #495057;
  padding: 1px 5px; border-radius: 3px; font-size: 10px;
}
.om-viz-meta { font-size: 10px; color: #868e96; font-style: italic; }
.om-viz-slot {
  font-weight: 700; color: #2b8a3e; font-size: 12px;
}
.om-viz-rule-tag {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 10px; font-weight: 600;
  background: #e7f5ff; color: #1864ab; border: 1px solid #d0ebff;
}
.om-viz-rule-tag.rule-passthrough    { background: #ebfbee; color: #2b8a3e; border-color: #c3fae8; }
.om-viz-rule-tag.rule-filter_include { background: #e7f5ff; color: #1864ab; border-color: #d0ebff; }
.om-viz-rule-tag.rule-filter_exclude { background: #fff5f5; color: #c92a2a; border-color: #ffe3e3; }
.om-viz-from {
  font-family: monospace; background: #fff; border: 1px solid #ffd43b;
  color: #b45309; padding: 1px 5px; border-radius: 3px; font-size: 10px;
}
.om-viz-fields { font-size: 10px; color: var(--muted); }
.om-viz-warn { font-size: 10px; color: #c92a2a; font-weight: 600; }
.om-viz-pre {
  margin: 0; padding: 8px 10px; background: #0f172a; color: #e2e8f0;
  font-family: monospace; font-size: 11px; line-height: 1.5;
  border-top: 1px solid #495057; overflow-x: auto;
  max-height: 240px;
}
.om-viz-empty {
  font-size: 11px; color: #adb5bd; font-style: italic;
  padding: 6px 10px; background: #fff; border-radius: 4px;
}

/* ═══════════ 标准数据关联 v2 — 规则转换结果展示 ═══════════ */
.dl-bulk-bar {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 8px; font-size: 12px; color: var(--muted);
}
.dl-bulk-info { margin-right: auto; }

.dl-card-list-v2 {
  display: flex; flex-direction: column; gap: 10px;
}
.dl-card-v2 {
  border: 1px solid var(--border); border-radius: 8px;
  background: #fff; overflow: hidden;
}
.dl-card-v2 > summary {
  list-style: none; cursor: pointer; user-select: none;
}
.dl-card-v2 > summary::-webkit-details-marker { display: none; }
.dl-card-caret {
  display: inline-block; width: 14px; flex-shrink: 0;
  color: #adb5bd; font-size: 10px; transition: transform .2s;
  margin-top: 4px;
}
.dl-card-v2[open] .dl-card-caret { transform: rotate(90deg); }
.dl-card-v2 > summary:hover { background: #f5f5f5; }
.dl-card-v2 > summary:hover .dl-card-caret { color: var(--primary); }


/* 数据流说明条（朴素灰）*/
.dl-flow-bar {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 8px 12px; background: #fafafa;
  border-bottom: 1px solid #e9ecef;
  font-size: 11px; color: var(--muted);
}
.dl-flow-step { color: var(--muted); }
.dl-flow-step.dl-flow-tgt { color: var(--text); font-weight: 600; }
.dl-flow-arrow { color: #adb5bd; font-size: 12px; }


/* 分区（已映射 / 未映射）*/
.dl-section {
  padding: 10px 12px;
  border-top: 1px solid #f1f3f5;
}
.dl-section:first-of-type { border-top: none; }
.dl-section-muted { background: #fafafa; }
.dl-section-head {
  display: flex; align-items: baseline; gap: 8px;
  margin-bottom: 8px;
}
.dl-section-title {
  font-size: 12px; font-weight: 700; color: var(--text);
}
.dl-section-title-muted { color: var(--muted); font-weight: 600; }
.dl-section-count {
  font-size: 11px; color: var(--muted);
  padding: 1px 8px; background: #f1f3f5; border-radius: 10px;
}
.dl-section-empty {
  font-size: 12px; color: #adb5bd; font-style: italic;
  padding: 10px; text-align: center;
  background: #fafafa; border-radius: 4px;
}
.dl-empty-row {
  display: flex; flex-wrap: wrap; gap: 6px;
}
.dl-empty-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; background: #fff;
  border: 1px solid #e9ecef; border-radius: 14px;
  font-size: 12px; color: var(--muted);
}
.dl-empty-dot { color: #ced4da; font-weight: 700; }
.dl-empty-code {
  font-family: monospace; font-size: 10px; color: #adb5bd;
}
.dl-card-desc-inline { color: var(--muted); font-size: 12px; font-weight: 400; }

/* 7 大域 grid */
.dl-domain-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 10px;
}

.dl-domain-cell {
  border: 1px solid #e9ecef; border-radius: 8px;
  padding: 10px 12px; background: #fafafa;
  transition: all .15s;
}
.dl-domain-cell.filled {
  background: #fff; border-color: #d0d9e6;
}
.dl-domain-cell.warning {
  background: #fff;
  border-left: 3px solid #f0a500;
}
.dl-domain-cell.warning .dl-domain-status { color: #f0a500; background: transparent; }


.dl-domain-head {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 6px; padding-bottom: 6px;
  border-bottom: 1px dashed #dee2e6;
}
.dl-domain-status {
  width: 16px; flex-shrink: 0; text-align: center;
  font-size: 12px; font-weight: 700; color: #ced4da;
}
.dl-domain-cell.filled .dl-domain-status { color: #2b8a3e; }
.dl-domain-label {
  font-size: 13px; font-weight: 600; color: var(--text);
}
.dl-domain-code {
  font-family: monospace; font-size: 10px; color: #adb5bd;
  margin-left: auto;
}
.dl-domain-rule-line {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  font-size: 12px; margin-bottom: 4px; color: var(--text);
}
.dl-rule-text { font-weight: 500; color: var(--text); }
.dl-domain-sep { color: #ced4da; }
.dl-domain-from { color: var(--muted); font-size: 12px; }
.dl-domain-from code {
  font-family: monospace; background: #f5f5f5;
  padding: 1px 5px; border-radius: 3px;
  color: #495057; font-size: 11px;
}
.dl-field-text {
  font-family: monospace; font-size: 11px; color: #495057;
  padding: 0 2px;
}
.dl-field-text + .dl-field-text::before {
  content: '·'; color: #ced4da; margin: 0 4px 0 0;
}
.dl-field-text.excluded { color: #c92a2a; text-decoration: line-through; }
.dl-unit-mark { color: #f0a500; margin-left: 1px; font-size: 10px; }

.dl-domain-fields {
  display: flex; flex-wrap: wrap; gap: 3px; align-items: center;
  margin: 4px 0; padding: 4px 0;
  border-top: 1px dotted #f1f3f5;
}
.dl-fields-label {
  font-size: 10px; color: var(--muted); font-weight: 600;
  flex-shrink: 0;
}
.dl-field-tag {
  font-size: 10px !important; padding: 0 5px !important;
  height: 18px !important; line-height: 18px !important;
  font-family: monospace; background: #fff !important;
  border: 1px solid #dee2e6 !important; color: #495057 !important;
}
.dl-field-tag.excluded {
  background: #fff5f5 !important; color: #c92a2a !important;
  border-color: #ffa8a8 !important;
}
.dl-fields-more { font-size: 10px; color: #4263eb; font-weight: 600; }
.dl-domain-empty {
  font-size: 11px; color: #adb5bd; font-style: italic;
  padding: 4px 0;
}

/* 转换结果预览（折叠）*/
.dl-preview-fold {
  margin-top: 6px; padding-top: 6px;
  border-top: 1px dashed #dee2e6;
}
.dl-preview-fold > summary {
  cursor: pointer; font-size: 11px; font-weight: 600;
  color: #4263eb; list-style: none; padding: 2px 0;
  display: flex; align-items: center; gap: 6px;
}
.dl-preview-fold > summary::-webkit-details-marker { display: none; }
.dl-preview-fold > summary::before {
  content: '▶'; font-size: 9px; color: #adb5bd;
  transition: transform .15s;
}
.dl-preview-fold[open] > summary::before { transform: rotate(90deg); }
.dl-preview-meta {
  font-size: 10px; color: #868e96; font-weight: 400;
  margin-left: auto; font-style: italic;
}
.dl-preview-pre {
  margin: 6px 0 0; padding: 8px 10px;
  background: #0f172a; color: #e2e8f0;
  font-family: monospace; font-size: 11px; line-height: 1.5;
  border-radius: 4px; overflow-x: auto;
  max-height: 240px;
}

/* 原始配置折叠 */
.dl-raw-fold {
  border-top: 1px solid #e9ecef;
  padding: 10px 12px; background: #fafafa;
}
.dl-raw-fold > summary {
  cursor: pointer; font-size: 12px; font-weight: 600;
  color: #495057; list-style: none; padding: 2px 0;
}
.dl-raw-fold > summary::-webkit-details-marker { display: none; }
.dl-raw-fold > summary::before {
  content: '▶'; display: inline-block; margin-right: 6px;
  transition: transform .15s; font-size: 9px;
}
.dl-raw-fold[open] > summary::before { transform: rotate(90deg); }
.dl-raw-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px; margin-top: 10px;
}
.dl-raw-label {
  font-size: 11px; font-weight: 700; color: var(--muted);
  margin-bottom: 4px;
}
.dl-raw-pre {
  margin: 0; padding: 8px 10px;
  background: #0f172a; color: #e2e8f0;
  font-family: monospace; font-size: 10px; line-height: 1.5;
  border-radius: 4px; overflow-x: auto;
  max-height: 200px;
}

/* ═══════════ 智能映射弹窗 — 双栏 + 重新执行/重新生成 ═══════════ */
.automap-step-bar {
  display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap;
  padding: 10px 12px; background: #fafafa;
  border: 1px solid var(--border); border-radius: 6px;
}
.automap-hint {
  flex: 1; min-width: 280px;
  font-size: 12px; color: var(--muted); line-height: 1.7;
}
.automap-hint strong { color: var(--text); }
.automap-cols {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  margin-top: 12px;
}
.automap-col { min-width: 0; }
.automap-col-label {
  font-size: 12px; font-weight: 600; color: var(--muted);
  margin-bottom: 6px;
}
.automap-col-hint {
  font-weight: 400; color: var(--muted); font-size: 11px;
  margin-left: 4px;
}
.automap-empty-hint {
  height: 100%; min-height: 240px;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: #adb5bd; font-style: italic;
  background: #fafafa; border: 1px dashed var(--border); border-radius: 4px;
  padding: 20px; text-align: center; line-height: 1.7;
}
@media (max-width: 720px) {
  .automap-cols { grid-template-columns: 1fr; }
}

/* ═══════════ 数据流映射 Tab（简化版）═══════════ */
.dfm-v2-intro {
  margin: 0 0 14px; font-size: 13px; color: var(--muted); line-height: 1.6;
}
.dfm-v2-warn-inline { color: #c92a2a; font-weight: 600; }
.dfm-v2-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
}
@media (max-width: 860px) {
  .dfm-v2-grid { grid-template-columns: 1fr; }
}
.dfm-v2-panel {
  border: 1px solid var(--border); border-radius: 8px; background: #fff;
  padding: 12px 14px;
}
.dfm-v2-panel-hd {
  font-size: 14px; font-weight: 600; color: var(--text);
  padding-bottom: 10px; margin-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.dfm-v2-section-label {
  font-size: 12px; font-weight: 600; color: var(--muted);
  margin: 12px 0 8px;
}
.dfm-v2-section-label:first-of-type { margin-top: 0; }
.dfm-v2-list { display: flex; flex-direction: column; gap: 8px; }
.dfm-v2-item {
  padding: 10px 12px; border: 1px solid #e9ecef; border-radius: 6px;
  background: #fafafa; font-size: 13px; line-height: 1.55;
}
.dfm-v2-item.is-warn { border-color: #ffc9c9; background: #fff5f5; }
.dfm-v2-item-name { font-weight: 600; color: var(--text); margin-bottom: 4px; }
.dfm-v2-item-line { font-size: 12px; color: var(--text); margin-top: 2px; }
.dfm-v2-k {
  display: inline-block; min-width: 56px; color: var(--muted); font-weight: 500;
}
.dfm-v2-muted { color: var(--muted); }
.dfm-v2-empty {
  padding: 16px; text-align: center; font-size: 12px; color: var(--muted);
  background: #f8f9fa; border-radius: 6px; border: 1px dashed #dee2e6;
}
.dfm-v2-tpl-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-bottom: 4px;
}
.dfm-v2-status {
  font-size: 11px; padding: 1px 8px; border-radius: 10px; flex-shrink: 0;
  background: #f1f3f5; color: var(--muted);
}
.dfm-v2-status.on { background: #ebfbee; color: #2b8a3e; }
.dfm-v2-status.off { background: #f1f3f5; color: #868e96; }
.dfm-v2-item-warn {
  margin-top: 6px; font-size: 12px; color: #c92a2a;
}

/* ── 数据流映射：接口分组 ── */
.dfm-v2-api-groups { display: flex; flex-direction: column; gap: 6px; }
.dfm-v2-api-group {
  border: 1px solid #e9ecef; border-radius: 6px; overflow: hidden;
}
.dfm-v2-api-group-hd {
  display: flex; align-items: center; gap: 6px; padding: 8px 10px;
  background: #f8f9fa; cursor: pointer; user-select: none;
  font-size: 13px;
}
.dfm-v2-api-group-hd:hover { background: #f0f4ff; }
.dfm-api-arrow {
  font-size: 12px; color: var(--muted); transition: transform .2s;
}
.dfm-v2-api-name { font-weight: 600; font-family: monospace; color: var(--text); }
.dfm-v2-api-slot-count {
  margin-left: auto; font-size: 11px; color: var(--muted);
}
.dfm-v2-api-slots { padding: 8px 12px; display: flex; flex-direction: column; gap: 6px; }
.dfm-v2-slot-item {
  font-size: 12px; padding: 4px 0;
  border-bottom: 1px solid #f1f3f5;
}
.dfm-v2-slot-item:last-child { border-bottom: none; }
.dfm-v2-slot-label { font-weight: 500; color: var(--text); margin-right: 6px; }
.dfm-v2-slot-key { font-size: 11px; color: var(--muted); }
.dfm-v2-slot-fields { margin-top: 3px; }

/* ── 数据流映射：话术模板分区 ── */
.dfm-v2-tpl-section { display: flex; flex-direction: column; gap: 6px; }
.dfm-v2-tpl-section-hd {
  font-size: 12px; font-weight: 600; color: #2b8a3e;
  padding: 4px 0; border-bottom: 1px solid #ebfbee; margin-bottom: 4px;
}
.dfm-v2-tpl-section-hd--unlinked { color: #e6a23c; border-bottom-color: #fdf3e3; }
</style>






