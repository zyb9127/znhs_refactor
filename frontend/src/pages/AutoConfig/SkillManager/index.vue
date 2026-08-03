<template>
  <div>
    <!-- ══ PageHeader：环境提示条 + 面包屑 + 主操作（规范 3 / 10）══ -->
    <div class="page-header">
      <EnvBanner />
      <div class="page-header-row">
        <div class="breadcrumb" style="margin-bottom:0;">
          <div class="breadcrumb-item">话术智能体运营</div>
          <div class="breadcrumb-item active"><span class="crumb-dot"></span>智能话术配置管理</div>
        </div>
        <div class="page-header-actions">
          <!-- 当前登录用户信息：账号 / 姓名 / 省份 / 角色（有则显示，规范 3）-->
          <div v-if="authStore.loaded" class="user-info">
            <el-icon class="user-info-avatar"><UserFilled /></el-icon>
            <div class="user-info-meta">
              <div class="user-info-line-1">
                <span class="user-info-name">{{ authStore.username || '未登录' }}</span>
                <span v-if="authStore.account" class="user-info-account">（{{ authStore.account }}）</span>
              </div>
              <div class="user-info-line-2">
                <el-tag size="small" :type="authStore.isHQ ? 'success' : 'primary'" effect="plain">
                  {{ userProvinceLabel }}
                </el-tag>
                <el-tag
                  v-for="rn in authStore.roleNames"
                  :key="rn"
                  size="small"
                  type="info"
                  effect="plain"
                >{{ rn }}</el-tag>
              </div>
            </div>
          </div>
          <el-button size="small" type="success" @click="$router.push('/Import')">
            <el-icon><Plus /></el-icon>&nbsp;创建新配置
          </el-button>
          <el-button v-if="showExportImport" size="small" type="warning" plain :loading="exporting" :disabled="exporting" @click="exportAllConfigs">
            <el-icon><Download /></el-icon>&nbsp;一键导出配置
          </el-button>
          <el-button v-if="showExportImport" size="small" type="warning" plain :loading="importing" :disabled="importing" @click="triggerImportConfigs">
            <el-icon><Upload /></el-icon>&nbsp;导入配置
          </el-button>
          <input ref="importFileInput" type="file" accept=".json,application/json" style="display:none" @change="onImportFileChosen" />
          <el-button size="small" plain @click="$router.push('/StandardDomains')">
            <el-icon><InfoFilled /></el-icon>&nbsp;用户手册
          </el-button>
          <el-button v-if="showExportLog" size="small" plain @click="logDrawerVisible = true">
            <el-icon><Document /></el-icon>&nbsp;操作日志
          </el-button>
        </div>
      </div>
    </div>

    <!-- ══ PageFilter：筛选栏（规范 3）══ -->
    <div class="filter-bar page-filter">
      <div class="filter-group">
        <label>省份</label>
        <!-- 非本部用户：省份锁定为自身省份，不可切换 -->
        <el-select
          v-model="filter.province"
          clearable
          placeholder="全部省份"
          size="small"
          style="width:140px"
          :disabled="!authStore.isHQ"
          @change="onProvinceFilter"
        >
          <el-option
            v-for="p in provinceOptions"
            :key="p.province"
            :label="`${p.province_name}（${p.province}）`"
            :value="p.province"
          />
        </el-select>
      </div>
      <div class="filter-group">
        <label>场景分类</label>
        <el-select
          v-model="filter.intent"
          clearable
          placeholder="全部场景分类"
          size="small"
          style="width:140px"
          :disabled="intentOptions.length === 0"
          @change="debouncedLoad"
          @clear="debouncedLoad"
        >
          <el-option
            v-for="i in intentOptions"
            :key="i"
            :label="i"
            :value="i"
          />
        </el-select>
      </div>
      <div class="filter-actions">
        <el-button size="small" @click="resetFilter">重置</el-button>
        <el-button size="small" type="primary" :loading="loading" :disabled="loading" @click="loadSkills">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
      </div>
      <!-- 权限标识 -->
      <div v-if="authStore.loaded && !authStore.isHQ" style="margin-left:auto;font-size:12px;color:var(--muted);">
        <el-tag size="small" type="info">{{ authStore.province }} 省 · 只读其他省份</el-tag>
      </div>
    </div>

    <!-- ══ PageContent：同步状态 + 表格（规范 3）══ -->
    <div class="page-content">

    <!-- 配置来源汇总（验收 S1：ES/Redis 同步状态一目了然） -->
    <div v-if="sourceSummary && Object.keys(sourceSummary).length" class="sync-status-bar">
      <span class="sync-status-label">配置来源：</span>
      <el-tag v-if="sourceSummary.es" size="small" type="success" effect="plain">ES {{ sourceSummary.es }}</el-tag>
      <el-tag v-if="sourceSummary.redis" size="small" type="warning" effect="plain">Redis {{ sourceSummary.redis }}</el-tag>
      <el-tag v-if="sourceSummary.local" size="small" type="info" effect="plain">本地 {{ sourceSummary.local }}</el-tag>
      <span v-if="sourceSummary.local && !sourceSummary.es && !sourceSummary.redis"
        class="sync-status-warn">⚠ 全部来自本地文件，请确认 ES 是否已发布配置</span>
      <span v-else class="sync-status-ok">✓ 运行时配置已从 ES/Redis 同步</span>
    </div>

    <!-- 表格卡片 -->
    <div class="page-card" style="padding:0;overflow:hidden;">
      <el-table
        :data="skills"
        v-loading="loading"
        stripe
        highlight-current-row
        style="width:100%"
      >
        <!-- 全局统一空状态（规范 8）：区分加载失败 / ES 无配置 / 无数据 -->
        <template #empty>
          <EmptyState
            v-if="loadError"
            variant="error"
            title="配置列表加载失败"
            :description="loadError"
            retryable
            @retry="loadSkills"
          />
          <EmptyState
            v-else-if="!loading"
            variant="es-missing"
            title="暂无智能话术配置"
            description="当前筛选条件下没有配置。若 ES 索引缺失或线上尚未发布配置，请先通过「创建新配置」创建，或检查 ES 连接。"
            retryable
            @retry="loadSkills"
          />
        </template>
        <el-table-column label="配置 ID / 名称" prop="skill_id" min-width="200">
          <template #default="{ row }">
            <span class="td-name">{{ row.skill_id }}</span>
            <div style="font-size:11px;color:var(--muted);margin-top:2px;">{{ row.skill_name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="省份 / 场景分类" min-width="120">
          <template #default="{ row }">
            <div>{{ row.province_name || row.province }}</div>
            <div style="font-size:12px;color:var(--muted);">{{ row.intent }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" prop="status" width="88" align="center">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="statusTagType(row.status)"
              :effect="row.status === 'published' ? 'dark' : 'light'"
            >{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="接口" width="56" align="center">
          <template #default="{ row }">
            <span :class="row.api_node_count > 0 ? 'badge badge--ok' : 'badge badge--muted'">
              {{ row.api_node_count ?? 0 }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="话术" width="54" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.template_count ?? 0 }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="配置来源" width="82" align="center">
          <template #default="{ row }">
            <!-- 统一 EsStatusTag（规范 5）：hover 展示 ES 详情/异常 -->
            <EsStatusTag
              :source="row.config_source || 'local'"
              :loaded-at="row.config_loaded_at || ''"
              :es-error="row.es_error || ''"
            />
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="108">
          <template #default="{ row }">
            <div style="font-size:12px;">{{ row.created_at || '—' }}</div>
            <div v-if="row.created_by" style="font-size:11px;color:var(--muted);">{{ row.created_by }}</div>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="108">
          <template #default="{ row }">
            <div style="font-size:12px;">{{ row.updated_at || '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <div class="ops" @click.stop>
              <button class="btn-link" @click="openTest(row)">测试</button>
              <template v-if="authStore.canWrite(row.province)">
                <span class="ops-sep">|</span>
                <button class="btn-link" @click="openEdit(row)">编辑</button>
                <span class="ops-sep">|</span>
                <!-- 已下线或编辑中：显示"发布"；已发布：显示"下线" -->
                <button
                  v-if="row.status !== 'published'"
                  class="btn-link success"
                  @click="doPublish(row)"
                >发布</button>
                <button
                  v-else
                  class="btn-link warn"
                  @click="doOffline(row)"
                >下线</button>
                <span class="ops-sep">|</span>
                <!-- 基于 ES 当前配置自愈修复：重新校验 → 自动补回缺失映射 → 发布生效 -->
                <el-tooltip
                  content="重新校验 ES 当前接口配置，自动补回缺失的映射槽位/标准域、规范化字段名，并发布生效"
                  placement="top"
                >
                  <button class="btn-link" @click="doRepairConfig(row)">修复</button>
                </el-tooltip>
                <span class="ops-sep">|</span>
                <!-- 已发布时禁用删除，需先下线 -->
                <el-tooltip
                  v-if="row.status === 'published'"
                  content="请先【下线】后再删除"
                  placement="top"
                >
                  <button class="btn-link danger disabled">删除</button>
                </el-tooltip>
                <button
                  v-else
                  class="btn-link danger"
                  @click="openDelete(row)"
                >删除</button>
              </template>
              <template v-else>
                <span class="ops-sep">|</span>
                <button class="btn-link" @click="openDetail(row)">查看</button>
                <el-tooltip content="非本省份配置，仅可查看，不能编辑" placement="top">
                  <span class="badge badge--muted" style="margin-left:2px;">只读</span>
                </el-tooltip>
              </template>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div style="padding:12px 20px;font-size:13px;color:var(--muted);">
        共 {{ skills.length }} 个配置 · 默认按版本倒序排列
      </div>
    </div>
    </div><!-- /page-content -->

    <!-- ── 日志侧边抽屉（规范 6）────────────────────── -->
    <LogDrawer v-model="logDrawerVisible" />

    <!-- ── 详情抽屉（查看） ─────────────────────────── -->
    <el-drawer
      v-model="detailVisible"
      :title="`智能话术配置详情 — ${current?.province} / ${current?.intent}`"
      size="620px"
      direction="rtl"
      destroy-on-close
    >
      <div v-if="detailLoading" style="text-align:center;padding:40px;">
        <el-icon class="is-loading" :size="28"><Loading /></el-icon>
      </div>
      <div v-else-if="detail">
        <el-tabs v-model="detailTab">
          <el-tab-pane label="元数据" name="meta">
            <div v-if="!detail.files?.meta" class="empty-tip">暂无元数据</div>
            <div v-else class="config-section">
              <table class="config-table">
                <tbody>
                  <tr><th>配置项</th><th>配置值</th><th>说明</th></tr>
                  <tr v-for="row in metaRows" :key="row.key">
                    <td><code>{{ row.key }}</code></td>
                    <td class="config-val">{{ row.value }}</td>
                    <td style="color:var(--muted)">{{ row.desc }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </el-tab-pane>

          <!-- 数据流概览（验收 S2-5：7 大固定域 + 模板槽位闭环，只读） -->
          <el-tab-pane label="数据流概览" name="dataflow">
            <div v-if="auditLoading" style="text-align:center;padding:30px;">
              <el-icon class="is-loading" :size="24"><Loading /></el-icon>
            </div>
            <div v-else-if="contextAudit">
              <el-alert
                :type="contextAudit.passed ? 'success' : 'warning'"
                :closable="false" style="margin-bottom:12px;"
              >
                {{ contextAudit.summary }}
                <span style="font-size:12px;margin-left:8px;color:var(--muted);">
                  配置来源：{{ contextAudit.config_source || '—' }}
                  · 域覆盖 {{ contextAudit.domains_covered }}/{{ contextAudit.domains_total }}
                  · 模板闭环 {{ contextAudit.templates_closed }}/{{ contextAudit.templates_total }}
                </span>
              </el-alert>

              <div class="audit-section-title">7 大固定数据域</div>
              <div class="audit-domain-grid">
                <div v-for="d in contextAudit.domain_status" :key="d.key"
                  class="audit-domain-chip" :class="{ supplied: d.supplied }">
                  <span class="audit-domain-label">{{ d.label }}</span>
                  <code class="audit-domain-key">{{ d.key }}</code>
                  <span v-if="d.supplied" class="audit-domain-providers">
                    ← {{ (d.providers || []).join('、') || '—' }}
                  </span>
                  <span v-else class="audit-domain-missing">未配置</span>
                </div>
              </div>

              <div v-if="contextAudit.template_audit?.length" class="audit-section-title" style="margin-top:16px;">
                话术模板槽位闭环
              </div>
              <div v-for="t in contextAudit.template_audit" :key="t.template_id || t.template_name"
                class="audit-tpl-row" :class="{ 'is-warn': !t.closed }">
                <span class="audit-tpl-name">{{ t.template_name || '（未命名）' }}</span>
                <span v-if="t.closed" class="audit-tpl-ok">✓ 闭环</span>
                <span v-else class="audit-tpl-warn">缺域：{{ (t.unmet_slots || []).join('、') }}</span>
              </div>

              <div v-if="contextAudit.issues?.length" class="audit-section-title" style="margin-top:16px;">
                审计问题（{{ contextAudit.issues.length }}）
              </div>
              <div v-for="(issue, i) in contextAudit.issues" :key="i"
                class="audit-issue" :class="'level-' + issue.level">
                <el-tag size="small" :type="issue.level === 'critical' ? 'danger' : 'warning'">
                  {{ issue.level }}
                </el-tag>
                {{ issue.message }}
              </div>
            </div>
            <div v-else class="empty-tip">暂无审计数据</div>
          </el-tab-pane>

          <el-tab-pane label="原始 JSON" name="raw">
            <el-select v-model="rawFile" size="small" style="width:180px;margin-bottom:12px;">
              <el-option label="api_nodes.json" value="api_nodes" />
              <el-option label="biz_config.json" value="biz_config" />
              <el-option label="_meta.json" value="meta" />
            </el-select>
            <pre class="json-pre">{{ prettyJson(detail.files?.[rawFile]) }}</pre>
          </el-tab-pane>
        </el-tabs>
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button
          v-if="authStore.canWrite(current?.province)"
          type="primary"
          @click="openEdit(current)"
        >编辑配置</el-button>
      </template>
    </el-drawer>

    <!-- ── 编辑弹窗（可拖拽拉宽，内容区独立滚动）──────── -->
    <el-dialog
      v-model="editVisible"
      :title="`智能话术配置 — ${current?.province} / ${current?.intent}`"
      :width="editDialogWidth + 'px'"
      :close-on-click-modal="false"
      destroy-on-close
      class="edit-skill-dialog"
      align-center
    >
      <!-- 左侧拖拽手柄 -->
      <div
        class="edit-dialog-resize-handle"
        @mousedown.prevent="startResizeDialog"
      ></div>

      <el-alert
        :type="current?.status === 'published' ? 'warning' : 'info'"
        :closable="false" style="margin-bottom:14px;flex-shrink:0;"
      >
        <template v-if="current?.status === 'published'">
          ⚠ 当前配置为<strong>「已发布」</strong>状态，保存改动后将自动转为「编辑中」，需重新发布才能在线上生效。
        </template>
        <template v-else>
          接口配置与话术模板的改动保存后<strong>写入 ES（版本化）+ Redis 缓存 + 内存热更新</strong>，无需重启服务即可生效。
        </template>
        <div style="font-size:12px;color:var(--muted);margin-top:4px;">
          状态流转：编辑中 →（发布）→ 已发布 →（下线）→ 已下线；已发布的配置需先下线才能删除。
        </div>
      </el-alert>
      <div class="edit-dialog-body">
        <SkillConfigEditor
          v-model="editConfigValue"
          :province="current?.province || ''"
          :intent="current?.intent || ''"
        />
      </div>
      <!-- 双操作（规范 4）：编辑器内各项「保存」写入草稿，「发布上线」二次确认后热重载生效 -->
      <template #footer>
        <el-button @click="editVisible = false">关闭</el-button>
        <el-button type="success" @click="publishFromEdit">发布上线</el-button>
      </template>
    </el-dialog>

    <!-- ── Skill 测试弹窗 ─────────────────────────────── -->
    <el-dialog
      v-model="testVisible"
      :title="`🧪 测试智能话术配置 — ${current?.province} / ${current?.intent}`"
      width="min(1080px, 95vw)"
      :close-on-click-modal="false"
      destroy-on-close
      align-center
      class="skill-test-dialog"
    >
      <div class="tc-layout">
        <!-- 左：测试参数（与 TestConsole 一致） -->
        <div>
          <div class="tc-card">
            <div class="tc-card-title">
              🧪 测试参数
              <button class="tc-autofill-btn" :disabled="autofilling" @click="autofillTestParams">
                {{ autofilling ? '填充中…' : '🪄 智能填充测试参数' }}
              </button>
            </div>
            <div class="tc-autofill-hint">
              打开时已按该配置的接口类型（直传 / 接口查询）自动生成一份 <strong>{{ current?.province }} / {{ current?.intent }}</strong> 的完整入参，可直接执行；如需重置点「🪄 智能填充测试参数」。
            </div>

            <!-- 测试用例：与该配置关联，可保存多组、随时切换 -->
            <div class="tc-case-bar">
              <select class="tc-input tc-case-select" :value="selectedCaseIdx" @change="loadCase(Number($event.target.value))">
                <option :value="-1">（新用例）</option>
                <option v-for="(c, i) in testCases" :key="i" :value="i">{{ c.name }}</option>
              </select>
              <input class="tc-input tc-case-name" v-model="caseName" placeholder="用例名称，如：北京-套餐推荐-mock">
              <button class="tc-btn tc-btn-secondary tc-case-btn" :disabled="savingCase" @click="saveCase">
                {{ savingCase ? '保存中…' : '💾 保存用例' }}
              </button>
              <button class="tc-btn tc-btn-secondary tc-case-btn" :disabled="savingCase || selectedCaseIdx < 0" @click="deleteCase">
                🗑 删除
              </button>
            </div>

            <div class="tc-fullbody-head">
              <label class="tc-label" style="margin:0;">
                完整请求体（JSON · 全部入参）
                <span class="tc-fullbody-tag" v-if="testSkillType">{{ { direct:'直传模式', api:'接口查询模式', mixed:'混合模式', none:'无接口' }[testSkillType] || '' }}</span>
              </label>
            </div>
            <div class="tc-autofill-hint" style="margin-top:0;">
              这就是将 POST 给 /marketing/recommend 的完整入参，可直接编辑后执行；province / intent 会自动锁定为当前配置。
            </div>
            <textarea class="tc-input" v-model="testForm.fullBody"
              placeholder='点「🪄 智能填充测试参数」按接口类型自动生成，或手动粘贴完整请求体'
              style="resize:vertical;min-height:320px;font-family:monospace;font-size:12px;"></textarea>

            <hr class="tc-divider">

            <button class="tc-btn tc-btn-primary tc-btn-block" :disabled="testLoading" @click="runSkillTest">
              {{ testLoading ? '⏳ 推荐中...' : '▶ 执行推荐' }}
            </button>
            <button class="tc-btn tc-btn-secondary tc-btn-block" style="margin-top:8px;" @click="clearTestResult">
              清空结果
            </button>
          </div>
        </div>

        <!-- 右：结果区（与 TestConsole 一致：状态条 + 分步折叠卡片） -->
        <div class="tc-result-wrap">
          <div class="tc-status-bar" :class="testStatusClass">
            <span>{{ testStatusIcon }}</span> {{ testStatusText }}
          </div>
          <div class="tc-result-pane">
            <div v-for="(card, idx) in testResultCards" :key="idx" class="tc-step-card">
              <div class="tc-step-header" @click="card.open = !card.open">
                <div class="tc-step-title">
                  <span class="tc-step-badge" :class="card.badgeClass">{{ card.open ? '展开' : '折叠' }}</span>
                  {{ card.title }}
                </div>
                <span style="color:var(--muted)">{{ card.open ? '▲' : '▼' }}</span>
              </div>
              <div class="tc-step-body" :class="{ open: card.open }" v-html="card.content"></div>
            </div>
            <div v-if="!testResultCards.length && !testLoading" class="test-empty" style="margin-top:32px;text-align:center;">
              填写左侧参数后点击「执行推荐」查看分步结果
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="testVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- ── 删除确认 ──────────────────────────────────── -->
    <!-- 高危操作：禁止遮罩误关（规范 9），删除按钮带 loading 锁（规范 1） -->
    <el-dialog v-model="deleteVisible" title="确认删除" width="420px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" style="margin-bottom:12px;">
        当前状态：<strong>{{ statusLabel(current?.status) }}</strong>
      </el-alert>
      <p>确认要删除配置 <strong>{{ current?.province }}/{{ current?.intent }}</strong> 吗？</p>
      <p style="margin-top:8px;font-size:13px;color:var(--danger);">删除后不可恢复，请谨慎操作。</p>
      <template #footer>
        <el-button @click="deleteVisible = false">取消</el-button>
        <el-button type="danger" :loading="deleting" @click="doDelete">确认删除</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'
import {
  acListSkills, acGetSkillConfig,
  acDeleteSkill, acReloadSkill, acUpdateSkillStatus,
  acExportAllSkills, acImportAllSkills, acContextAudit,
} from '@/api/autoConfig.js'
import { apiFetch, marketingRecommendFetch, readJsonOrThrow } from '@/utils/apiUrl'
import { $msg, useLock, debounce } from '@/utils/msg'
import { uiLog } from '@/utils/uiLog'
import { useEnv } from '@/composables/useEnv'
import EsStatusTag from '@/components/EsStatusTag.vue'
import EmptyState from '@/components/EmptyState.vue'
import LogDrawer from '@/components/LogDrawer.vue'
import EnvBanner from '@/components/EnvBanner.vue'
import SkillConfigEditor from '../components/SkillConfigEditor.vue'

// ── 权限 / 路由 ────────────────────────────────────────
const authStore = useAuthStore()
const router = useRouter()
const route = useRoute()

// ── 运行环境（按环境显隐导出/日志按钮，规范 10）────────────
const { showExportLog, showExportImport } = useEnv()

function goInterfaceMapper(row) {
  detailVisible.value = false
  router.push({ path: '/InterfaceMapper', query: { province: row.province, intent: row.intent } })
}

function goTemplateConfig(row) {
  detailVisible.value = false
  router.push({ path: '/TemplateConfig', query: { province: row.province, intent: row.intent } })
}

// ── 状态 ──────────────────────────────────────────────
const loading = ref(false)
const loadError = ref('')          // ES/接口异常时展示统一空状态 + 刷新重试（规范 1/8）
const sourceSummary = ref({})
const skills  = ref([])
const allSkillsCache = ref([])
const filter  = ref({ province: '', intent: '' })
const logDrawerVisible = ref(false)

const provinceOptions = computed(() => {
  const seen = new Set()
  return allSkillsCache.value
    .filter(s => { if (seen.has(s.province)) return false; seen.add(s.province); return true })
    .map(s => ({ province: s.province, province_name: s.province_name || s.province }))
})

// 当前用户省份展示：本部→"本部·全部省份"；否则优先部门名，其次省份中文名，兜底 code
const userProvinceLabel = computed(() => {
  if (authStore.isHQ) return '本部 · 全部省份'
  if (authStore.deptName) return authStore.deptName
  const code = authStore.province
  if (!code) return '未知省份'
  const hit = provinceOptions.value.find(p => p.province === code)
  return hit ? hit.province_name : code
})

const intentOptions = computed(() => {
  const list = filter.value.province
    ? allSkillsCache.value.filter(s => s.province === filter.value.province)
    : allSkillsCache.value
  return [...new Set(list.map(s => s.intent))]
})

function onProvinceFilter() {
  filter.value.intent = ''
  debouncedLoad()
}

const current = ref(null)
const detailVisible  = ref(false)
const detailLoading  = ref(false)
const detail         = ref(null)
const detailTab      = ref('meta')
const contextAudit   = ref(null)
const auditLoading   = ref(false)
const rawFile        = ref('api_nodes')
const editVisible     = ref(false)
const editConfigValue = ref({ api_nodes: {}, biz_config: {} })
const editDialogWidth = ref(Math.min(1100, window.innerWidth - 80))

// ── 拖拽拉宽弹窗 ───────────────────────────────────────
function startResizeDialog(e) {
  const startX = e.clientX
  const startW = editDialogWidth.value
  const onMove = (mv) => {
    const delta = startX - mv.clientX  // 左侧手柄向左拖 = 变宽
    editDialogWidth.value = Math.min(
      window.innerWidth - 40,
      Math.max(700, startW + delta)
    )
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}
const deleteVisible  = ref(false)
const deleting       = ref(false)

// ── 配置描述字典 ───────────────────────────────────────
const apiFieldDesc = {
  url: '接口请求地址', method: '请求方法（GET/POST）', enabled: '是否启用该接口节点',
  mock_mode: '是否使用 Mock 数据', request_template: '请求体模板，支持 {phone} 等变量',
  response_extract: '从响应 JSON 提取字段', timeout: '超时时间（秒）', headers: '自定义请求头',
}
const strategyDesc = { top_n: '最多推荐套餐数', sort_by: '排序字段', filter_enabled: '是否启用筛选' }
const metaFieldDesc = {
  skill_id: '配置唯一标识', name: '显示名称', description: '功能描述',
  status: '配置状态', province: '适用省份',
  scenario_id: '场景 ID / 意图名', author: '作者', created_by: '创建人',
  created_at: '创建时间', updated_at: '最后更新时间',
}

function statusLabel(s) {
  return { published: '已发布', draft: '编辑中', offline: '已下线' }[s] || s || '—'
}

function statusTagType(s) {
  return { published: 'success', draft: 'warning', offline: 'info' }[s] || 'info'
}

// ── 加载列表 ───────────────────────────────────────────
// 默认按 version 倒序（规范 5），版本号相同再按更新时间倒序
function compareVersionDesc(a, b) {
  const pa = String(a.version || '0').split('.').map(n => parseInt(n, 10) || 0)
  const pb = String(b.version || '0').split('.').map(n => parseInt(n, 10) || 0)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pb[i] || 0) - (pa[i] || 0)
    if (d !== 0) return d
  }
  return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
}

// 瞬时网关/后端未就绪错误：502/503/504 或超时/网络中断（冷启动窗口常见）。
// 这类错误对幂等 GET 可安全重试，避免登录首屏因 Pod 冷启动瞬时 504 直接报错。
function isTransientGatewayError(e) {
  const status = e?.response?.status
  if (status === 502 || status === 503 || status === 504) return true
  const code = e?.code
  if (code === 'ECONNABORTED' || code === 'ERR_NETWORK') return true
  return false
}

async function acListSkillsWithRetry(params, { retries = 2, baseDelay = 800 } = {}) {
  let lastErr
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await acListSkills(params)
    } catch (e) {
      lastErr = e
      if (attempt < retries && isTransientGatewayError(e)) {
        const status = e?.response?.status || e?.code || ''
        loadError.value = `服务正在启动或繁忙（${status}），正在重试（${attempt + 1}/${retries}）…`
        uiLog.warn('SkillManager', `配置列表加载瞬时失败，重试 ${attempt + 1}/${retries}`, String(status))
        await new Promise(r => setTimeout(r, baseDelay * Math.pow(2, attempt)))
        continue
      }
      throw e
    }
  }
  throw lastErr
}

async function loadSkills() {
  if (loading.value) return   // 防重复请求（规范 1）
  loading.value = true
  loadError.value = ''
  try {
    const res = await acListSkillsWithRetry({ province: filter.value.province, intent: filter.value.intent })
    loadError.value = ''
    skills.value = (res.skills || []).slice().sort(compareVersionDesc)
    sourceSummary.value = res.source_summary || {}
    uiLog.info('SkillManager', `加载配置列表成功：${skills.value.length} 个`, res.source_summary)
    if (!filter.value.province && !filter.value.intent) {
      allSkillsCache.value = skills.value
    } else if (allSkillsCache.value.length === 0) {
      try {
        const all = await acListSkillsWithRetry({})
        allSkillsCache.value = all.skills || []
      } catch { /* 全量缓存失败不影响当前筛选结果展示 */ }
    }
  } catch (e) {
    // 区分「网关超时/服务未就绪」与真实错误，给出可操作文案（规范 1/8）
    if (isTransientGatewayError(e)) {
      const status = e?.response?.status || e?.code || ''
      loadError.value = `服务暂时不可用或正在启动（${status}），请稍候点「刷新重试」`
      uiLog.error('SkillManager', '配置列表加载失败：网关超时/服务未就绪', String(status))
    } else {
      loadError.value = $msg.errOf(e, '配置列表加载失败，请刷新重试')
      uiLog.error('SkillManager', '配置列表加载失败', loadError.value)
    }
    skills.value = []
    sourceSummary.value = {}
  } finally {
    loading.value = false
  }
}

// 筛选变化 300ms 防抖（规范 1）
const debouncedLoad = debounce(loadSkills, 300)

function resetFilter() {
  // 非本部用户省份保持锁定
  filter.value = {
    province: authStore.isHQ ? '' : authStore.province,
    intent: '',
  }
  loadSkills()
}

// ── 一键导出全部配置 ───────────────────────────────────
// 导出 ES/Redis 同步后的生效配置（接口 + 话术模板 + 结构化信息），
// 按当前省份/意图筛选条件导出，浏览器直接下载 JSON 文件。
// useLock：执行期间按钮 loading + 防重复点击（规范 1）
const [exportAllConfigs, exporting] = useLock(async () => {
  try {
    const blob = await acExportAllSkills({
      province: filter.value.province,
      intent: filter.value.intent,
    })
    const ts = new Date()
    const pad = n => String(n).padStart(2, '0')
    const fname = `skills_export_${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}`
      + `_${pad(ts.getHours())}${pad(ts.getMinutes())}${pad(ts.getSeconds())}.json`
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fname
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    $msg.ok('配置已导出')
    uiLog.info('SkillManager', `一键导出配置成功：${fname}`)
  } catch (e) {
    const text = $msg.errOf(e, '导出失败')
    uiLog.error('SkillManager', '一键导出配置失败', text)
  }
})

// ── 导入配置（与导出往返对称：直接选择导出的 JSON 文件即可整批还原到 ES）──
const importFileInput = ref(null)
const importing = ref(false)

function triggerImportConfigs() {
  importFileInput.value?.click()
}

async function onImportFileChosen(e) {
  const file = e.target.files?.[0]
  if (e.target) e.target.value = ''   // 允许连续选同一文件
  if (!file) return
  let payload
  try {
    payload = JSON.parse(await file.text())
  } catch {
    $msg.err('文件不是合法 JSON，请选择由「一键导出配置」导出的文件')
    return
  }
  const skills = Array.isArray(payload) ? payload : payload?.skills
  if (!Array.isArray(skills) || !skills.length) {
    $msg.err('文件里没有可导入的技能包（需要 skills 列表）')
    return
  }
  const confirmed = await $msg.confirm(
    `将导入 ${skills.length} 个技能包的接口与话术模板到 ES（同名覆盖，保存即自愈）。是否继续？`,
    { title: '导入配置', type: 'warning', confirmText: '导入' },
  )
  if (!confirmed) return

  importing.value = true
  try {
    const res = await acImportAllSkills(Array.isArray(payload) ? { skills } : payload)
    const s = res?.summary || {}
    const failed = (res?.results || []).filter(r => r.status === 'failed' || r.status === 'skipped')
    if (res?.success) {
      $msg.ok(res.message || `导入完成：成功 ${s.ok || 0}`)
    } else {
      const detail = failed.slice(0, 5)
        .map(r => `${r.province || ''}/${r.intent || ''}：${r.error || (r.notes || []).join('；')}`)
        .join('\n')
      $msg.warn(`${res?.message || '部分导入失败'}${detail ? '\n' + detail : ''}`)
    }
    uiLog.info('SkillManager', `导入配置：${res?.message || ''}`)
    await loadSkills()
  } catch (err) {
    const text = $msg.errOf(err, '导入失败')
    uiLog.error('SkillManager', '导入配置失败', text)
  } finally {
    importing.value = false
  }
}

// ── 查看详情 ───────────────────────────────────────────
async function openDetail(row) {
  current.value = row
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  contextAudit.value = null
  detailTab.value = 'meta'
  rawFile.value = 'api_nodes'
  try {
    detail.value = await acGetSkillConfig(row.province, row.intent)
    // 并行加载 Context 审计（只读，不写配置）
    auditLoading.value = true
    acContextAudit(row.province, row.intent)
      .then(a => { contextAudit.value = a })
      .catch(() => { contextAudit.value = null })
      .finally(() => { auditLoading.value = false })
  } catch (e) {
    const text = $msg.errOf(e, '加载配置详情失败')
    uiLog.error('SkillManager', `加载详情失败：${row.province}/${row.intent}`, text)
  } finally {
    detailLoading.value = false
  }
}

function apiNodeRows(name, node) {
  const skip = new Set(['response_extract', 'headers'])
  return Object.entries(node || {})
    .filter(([k]) => !skip.has(k))
    .map(([k, v]) => ({ key: k, value: formatVal(v), desc: apiFieldDesc[k] || '' }))
}

const bizTemplates = computed(() => detail.value?.files?.biz_config?.script_templates_v2 || [])
const metaRows = computed(() => {
  const meta = detail.value?.files?.meta || {}
  return Object.entries(meta).map(([k, v]) => ({ key: k, value: formatVal(v), desc: metaFieldDesc[k] || '' }))
})

function formatVal(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function prettyJson(obj) { return obj ? JSON.stringify(obj, null, 2) : '—' }

// ── 编辑配置 ───────────────────────────────────────────
async function openEdit(row) {
  // 省份写权限保护：非本省份仅可查看（跨省份改走只读详情）
  if (!authStore.canWrite(row.province)) {
    $msg.warn('非本省份配置仅可查看，不能编辑')
    detailVisible.value = false
    editVisible.value = false
    return openDetail(row)
  }
  current.value = row
  editConfigValue.value = { api_nodes: {}, biz_config: {} }
  editVisible.value = true
  try {
    const res = await acGetSkillConfig(row.province, row.intent)
    editConfigValue.value = {
      api_nodes:  res.files?.api_nodes  || {},
      biz_config: res.files?.biz_config || {},
    }
  } catch (e) {
    const text = $msg.errOf(e, '加载配置失败，请刷新重试')
    uiLog.error('SkillManager', `加载编辑配置失败：${row.province}/${row.intent}`, text)
  }
}

// ── Skill 测试 ─────────────────────────────────────────
const testVisible = ref(false)
const testLoading = ref(false)
const testForm = ref({
  // 完整请求体：直接作为 POST /marketing/recommend 的 body（唯一入参来源）
  fullBody: '',
})
// 与 TestConsole 一致的状态条 + 分步结果卡片
const testStatusClass = ref('status-idle')
const testStatusIcon  = ref('⚡')
const testStatusText  = ref('准备就绪，点击「执行推荐」开始测试')
const testResultCards = ref([])

// ── 测试用例（与 skill 配置关联，生产存 ES）────────────────────
const testCases      = ref([])   // [{name, payload, updated_at}]
const selectedCaseIdx = ref(-1)  // -1 表示"新用例"
const caseName        = ref('')
const savingCase      = ref(false)
const testSkillType   = ref('')  // direct | api | mixed | none

async function openTest(row) {
  current.value = row
  testVisible.value = true
  testResultCards.value = []
  testStatusClass.value = 'status-idle'
  testStatusIcon.value  = '⚡'
  testStatusText.value  = '准备就绪，点击「执行推荐」开始测试'
  testForm.value.fullBody = ''
  testSkillType.value = ''
  testCases.value = []
  selectedCaseIdx.value = -1
  caseName.value = ''
  // 加载已保存的测试用例；有则默认载入第一条，无则按接口类型自动生成完整入参
  let hasCase = false
  try {
    const res = await apiFetch(`/api/skills/${encodeURIComponent(row.province)}/${encodeURIComponent(row.intent)}/test_cases`)
    const json = await res.json()
    testCases.value = Array.isArray(json.data) ? json.data : []
    if (testCases.value.length) { loadCase(0); hasCase = true }
  } catch (e) {
    uiLog.warn('SkillTest', `加载测试用例失败：${row.province}/${row.intent}`, e?.message)
  }
  if (!hasCase) await autofillTestParams({ silent: true })
}

// 载入指定用例：payload → 完整请求体
function loadCase(idx) {
  selectedCaseIdx.value = idx
  if (idx < 0 || idx >= testCases.value.length) {
    caseName.value = ''
    return
  }
  const c = testCases.value[idx]
  caseName.value = c.name || ''
  const p = c.payload && typeof c.payload === 'object' ? c.payload : {}
  testForm.value.fullBody = JSON.stringify(p, null, 2)
}

// 保存当前用例（以完整请求体为准）
async function saveCase() {
  if (!current.value) return
  const name = String(caseName.value || '').trim()
  if (!name) { $msg.warn('请先填写用例名称'); return }
  const raw = String(testForm.value.fullBody || '').trim()
  if (!raw) { $msg.warn('完整请求体为空，请先生成或填写'); return }
  let payload
  try { payload = JSON.parse(raw) } catch { $msg.err('完整请求体 JSON 格式错误，无法保存'); return }
  // 强制省份/意图与当前配置一致
  payload.province = current.value.province
  payload.intent = current.value.intent
  const now = new Date().toISOString()
  const list = [...testCases.value]
  const existIdx = list.findIndex(c => c.name === name)
  const item = { name, payload, updated_at: now }
  if (existIdx >= 0) list[existIdx] = item
  else list.push(item)
  savingCase.value = true
  try {
    const res = await apiFetch(
      `/api/skills/${encodeURIComponent(current.value.province)}/${encodeURIComponent(current.value.intent)}/test_cases`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cases: list }) },
    )
    const json = await res.json()
    if (res.ok && json.code === 200) {
      testCases.value = list
      selectedCaseIdx.value = existIdx >= 0 ? existIdx : list.length - 1
      $msg.ok(`用例「${name}」已保存（与该配置关联）`)
      uiLog.info('SkillTest', `保存测试用例：${current.value.province}/${current.value.intent} · ${name}`)
    } else {
      $msg.err(json.message || json.detail || '用例保存失败')
    }
  } catch (e) {
    $msg.err($msg.errOf(e, '用例保存失败'))
  } finally {
    savingCase.value = false
  }
}

// 删除当前选中用例
async function deleteCase() {
  if (!current.value || selectedCaseIdx.value < 0) return
  const target = testCases.value[selectedCaseIdx.value]
  const ok = await $msg.confirm(`确认删除用例「${target?.name}」？`, { title: '删除用例', type: 'warning', confirmText: '删除' })
  if (!ok) return
  const list = testCases.value.filter((_, i) => i !== selectedCaseIdx.value)
  savingCase.value = true
  try {
    const res = await apiFetch(
      `/api/skills/${encodeURIComponent(current.value.province)}/${encodeURIComponent(current.value.intent)}/test_cases`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cases: list }) },
    )
    const json = await res.json()
    if (res.ok && json.code === 200) {
      testCases.value = list
      selectedCaseIdx.value = -1
      caseName.value = ''
      $msg.ok('用例已删除')
    } else {
      $msg.err(json.message || json.detail || '删除失败')
    }
  } catch (e) {
    $msg.err($msg.errOf(e, '删除失败'))
  } finally {
    savingCase.value = false
  }
}

function clearTestResult() {
  testResultCards.value = []
  testStatusClass.value = 'status-idle'
  testStatusIcon.value  = '⚡'
  testStatusText.value  = '已清空，点击「执行推荐」重新测试'
}

// ── 智能填充测试参数 ─────────────────────────────────────
// 由后端依据该配置的接口类型（直传 / 接口查询）生成一份完整请求体
const autofilling = ref(false)

async function autofillTestParams(opts = {}) {
  const silent = opts && opts.silent === true
  if (!current.value) return
  autofilling.value = true
  try {
    // 由后端依据接口类型生成完整请求体（直传→extra_info；接口→extra_data；含 batch_contexts）
    const res = await apiFetch(
      `/api/skills/${encodeURIComponent(current.value.province)}/${encodeURIComponent(current.value.intent)}/gen_test_payload`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    )
    const json = await res.json()
    const data = json.data || {}
    const payload = data.payload
    if (!payload || typeof payload !== 'object') {
      if (!silent) $msg.info('该配置未生成可用参数，请手动填写完整请求体')
      return
    }
    testSkillType.value = data.skill_type || ''
    testForm.value.fullBody = JSON.stringify(payload, null, 2)
    if (!silent) {
      const typeLabel = { direct: '直传模式', api: '接口查询模式', mixed: '混合模式', none: '无接口' }[data.skill_type] || ''
      const noteTxt = (data.notes && data.notes.length) ? `：${data.notes.join('；')}` : ''
      $msg.ok(`已按${typeLabel}生成完整测试参数${noteTxt}。请核对后点「执行推荐」`)
    }
  } catch (e) {
    if (!silent) $msg.err($msg.errOf(e, '智能填充失败，请手动填写完整请求体'))
    else uiLog.warn('SkillTest', `自动生成测试参数失败：${current.value?.province}/${current.value?.intent}`, e?.message)
  } finally {
    autofilling.value = false
  }
}

// ── 与 TestConsole 完全一致的渲染函数 ──────────────────
function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderTestResult(json) {
  const d = json.data || {}
  const rc = json.resource_context || {}
  const meta = json.metadata || {}
  const finalRecs = (d.final_recommendations?.length ? d.final_recommendations : rc.recommended_packages) || []
  // 接口查询模式：仅展示 source_type=api（或未标注）的节点调用轨迹
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
    { title: '📊 执行元信息', badgeClass: 'badge-blue', open: false, content: renderTestMeta(meta, json) },
    { title: '🗂 原始响应 (JSON)', badgeClass: 'badge-blue', open: false, content: `<pre>${escHtml(JSON.stringify(json, null, 2))}</pre>` },
  )
  testResultCards.value = cards
}

/** 渲染发给大模型的最终提示词（上下文数据 / 话术模板 / 话术要求 / 其他） */
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

/** 渲染接口查询节点的实际请求入参 / 响应出参（排障用） */
function renderApiCalls(calls) {
  if (!calls?.length) {
    return '<p style="color:var(--muted);font-size:13px">本次未调用外部查询接口（可能为直传模式或无启用节点）</p>'
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
    const reqJson = escHtml(JSON.stringify(c.request ?? null, null, 2))
    const resJson = escHtml(JSON.stringify(c.response ?? null, null, 2))
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
      ${wrapHint}
      ${errHtml}
      <div style="font-size:12px;font-weight:600;color:var(--muted);margin:8px 0 4px;">📤 查询入参（实际上送）</div>
      <pre style="max-height:220px;">${reqJson}</pre>
      <div style="font-size:12px;font-weight:600;color:var(--muted);margin:10px 0 4px;">📥 查询出参（原始响应）</div>
      <pre style="max-height:280px;">${resJson}</pre>
      ${renderMappingDiag(c)}
    </div>`
  }).join('')
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
        const dv = String(row.diff || '')
        const ds = dv.startsWith('+') ? 'color:#2f9e44;font-weight:600' : dv.startsWith('-') ? 'color:#c92a2a;font-weight:600' : ''
        tableHtml += `<tr><td>${escHtml(String(row.label||''))}</td><td>${escHtml(String(row.current||'—'))}</td><td>${escHtml(String(row.target||'—'))}</td><td style="${ds}">${escHtml(dv||'—')}</td></tr>`
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
  html += Object.entries(cp).map(([k,v]) => `<div class="meta-row"><span>${escHtml(k)}</span><span>${escHtml(String(v))}</span></div>`).join('') || '<span style="color:var(--muted);font-size:13px">无</span>'
  html += '</div><div><div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">用户标签</div>'
  html += Object.entries(tags).slice(0,8).map(([k,v]) => `<div class="meta-row"><span>${escHtml(k)}</span><span>${escHtml(String(v))}</span></div>`).join('') || '<span style="color:var(--muted);font-size:13px">无</span>'
  html += '</div></div>'
  if (usage.data_usage || usage.voice_usage || usage.consumption) {
    const allUsage = { ...(usage.data_usage||{}), ...(usage.voice_usage||{}), ...(usage.consumption||{}) }
    html += '<hr class="tc-divider"><div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px">用量数据</div>'
    html += '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:4px">' + Object.entries(allUsage).map(([k,v]) => `<div class="meta-row"><span>${escHtml(k)}</span><span>${escHtml(String(v))}</span></div>`).join('') + '</div>'
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

function renderTestMeta(meta, json) {
  const rows = [
    ['elapsed_ms', (meta.elapsed_ms||0)+'ms'],
    ['推荐数量', meta.recommendation_count||0],
    ['话术数量', meta.script_count||0],
    ['trace_id', json.data?.callId||''],
    ['province', json.data?.province||''],
    ['intent', json.data?.intent||''],
  ]
  return rows.map(([k,v]) => `<div class="meta-row"><span style="color:var(--muted)">${k}</span><span><b>${escHtml(String(v))}</b></span></div>`).join('')
}

async function runSkillTest() {
  const raw = String(testForm.value.fullBody || '').trim()
  if (!raw) { $msg.err('完整请求体为空，请先点「🪄 智能填充测试参数」生成或手动填写'); return }
  let body
  try { body = JSON.parse(raw) }
  catch { $msg.err('完整请求体 JSON 格式错误，请检查后重试'); return }
  if (!body || typeof body !== 'object') { $msg.err('完整请求体需为 JSON 对象'); return }
  // 省份/意图强制与当前配置一致
  body.province = current.value.province
  body.intent = current.value.intent
  if (!body.callId) body.callId = 'skilltest-' + Date.now()
  if (!String(body.phone || '').trim()) { $msg.err('完整请求体缺少 phone'); return }
  testLoading.value = true
  testStatusClass.value = 'status-loading'
  testStatusIcon.value  = '🔄'
  testStatusText.value  = '执行中，请稍候...'
  testResultCards.value = []
  const t0 = Date.now()
  try {
    const res = await marketingRecommendFetch({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const json = await readJsonOrThrow(res)
    const elapsed = Date.now() - t0
    if (json.code === 200) {
      const n = json.data?.recommend_results?.length || 0
      testStatusClass.value = 'status-ok'
      testStatusIcon.value  = '✅'
      testStatusText.value  = `推荐成功，耗时 ${elapsed}ms，生成 ${n} 条话术`
      uiLog.info('SkillTest', `${body.province}/${body.intent} 测试成功：${elapsed}ms，${n} 条话术`)
      renderTestResult(json)
    } else {
      testStatusClass.value = 'status-err'
      testStatusIcon.value  = '❌'
      testStatusText.value  = `失败：${json.message || '未知错误'}`
      uiLog.error('SkillTest', `${body.province}/${body.intent} 测试失败`, json.message || json)
      testResultCards.value = [{
        title: '错误详情', badgeClass: 'badge-blue', open: true,
        content: `<pre style="background:#fef2f2;color:var(--danger)">${escHtml(JSON.stringify(json, null, 2))}</pre>`,
      }]
    }
  } catch (e) {
    testStatusClass.value = 'status-err'
    testStatusIcon.value  = '❌'
    testStatusText.value  = `请求异常：${e.message}`
    uiLog.error('SkillTest', `${body.province}/${body.intent} 请求异常`, e.message)
  } finally {
    testLoading.value = false
  }
}

// ── 发布（热重载 + 状态=published，高危操作二次确认 + 防重锁）──
const [doPublish] = useLock(async (row) => {
  if (!authStore.canWrite(row.province)) {
    $msg.warn('无权操作该省份的配置')
    return
  }
  const ok = await $msg.confirm(
    `确认发布并热重载 ${row.province}/${row.intent}？\n发布后配置将进入"已发布"状态，可被话术调度引擎调用。`,
    { title: '发布确认', type: 'warning', confirmText: '确认发布' },
  )
  if (!ok) return
  try {
    await acReloadSkill(row.province, row.intent)
    // 立即更新本地状态，避免等待接口刷新
    row.status = 'published'
    $msg.ok('发布成功，话术智能体已热重载，状态变更为"已发布"')
    uiLog.info('SkillManager', `发布配置：${row.province}/${row.intent}`)
    loadSkills()
  } catch (e) {
    const text = $msg.errOf(e, '发布失败')
    uiLog.error('SkillManager', `发布失败：${row.province}/${row.intent}`, text)
  }
})

// ── 下线（高危操作二次确认 + 防重锁）───────────────────
const [doOffline] = useLock(async (row) => {
  if (!authStore.canWrite(row.province)) {
    $msg.warn('无权操作该省份的配置')
    return
  }
  const ok = await $msg.confirm(
    `确认下线 ${row.province}/${row.intent}？\n下线后配置不再被调度，可重新发布或删除。`,
    { title: '下线确认', type: 'warning', confirmText: '确认下线' },
  )
  if (!ok) return
  try {
    await acUpdateSkillStatus(row.province, row.intent, 'offline')
    row.status = 'offline'
    $msg.ok('已下线，可重新发布或安全删除')
    uiLog.warn('SkillManager', `下线配置：${row.province}/${row.intent}`)
    loadSkills()
  } catch (e) {
    const text = $msg.errOf(e, '下线失败')
    uiLog.error('SkillManager', `下线失败：${row.province}/${row.intent}`, text)
  }
})

// ── 基于 ES 当前配置自愈修复（重新校验 → 自动修正 → 发布生效 + 防重锁）──
// 修复依据是配置自证线索：field_transform 声明的 from 槽位（如 raw_tags）+
// 节点 mock_response 里的同名字段（bean.tags）→ 探测补回 response_extract；
// 同时补回可识别的标准域映射、规范化畸形字段名。只增不删，无问题时不发布。
const [doRepairConfig] = useLock(async (row) => {
  if (!authStore.canWrite(row.province)) {
    $msg.warn('无权操作该省份的配置')
    return
  }
  const ok = await $msg.confirm(
    `确认对 ${row.province}/${row.intent} 的接口配置执行【校验并修复】？\n` +
    `系统将基于 ES 当前配置重新校验，自动补回缺失的映射槽位/标准域并发布生效；` +
    `只增不删，校验通过时不做任何变更。`,
    { title: '校验并修复接口配置', type: 'warning', confirmText: '校验并修复' },
  )
  if (!ok) return
  try {
    const res = await apiFetch(
      `/api/skills/${encodeURIComponent(row.province)}/${encodeURIComponent(row.intent)}/repair_config`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    )
    const json = await res.json()
    const d = json.data || {}
    if (json.code === 200) {
      const fixes = d.fixes || []
      const unfixed = d.unfixed || []
      if (fixes.length) {
        $msg.ok(`${json.message}：${fixes.slice(0, 3).join('；')}${fixes.length > 3 ? ' 等' : ''}`)
      } else {
        $msg.info(json.message || '校验通过，无需修复')
      }
      if (unfixed.length) {
        $msg.warn(`需人工处置：${unfixed.slice(0, 2).join('；')}${unfixed.length > 2 ? ' 等' : ''}`)
      }
      uiLog.warn('SkillManager', `校验并修复：${row.province}/${row.intent}`, d)
      if (d.published) loadSkills()
    } else {
      $msg.err(json.message || '修复失败')
      uiLog.error('SkillManager', `修复失败：${row.province}/${row.intent}`, json)
    }
  } catch (e) {
    const text = $msg.errOf(e, '修复失败')
    uiLog.error('SkillManager', `修复失败：${row.province}/${row.intent}`, text)
  }
})

// ── 编辑弹窗内「发布上线」：复用发布流程（含二次确认，规范 4）──
async function publishFromEdit() {
  if (!current.value) return
  await doPublish(current.value)
  editVisible.value = false
}

// ── 删除 ──────────────────────────────────────────────
function openDelete(row) {
  if (row.status === 'published') {
    $msg.warn('已发布的配置不可直接删除，请先点击【下线】')
    return
  }
  current.value = row
  deleteVisible.value = true
}

async function doDelete() {
  if (deleting.value) return   // 防重复提交（规范 1）
  if (!authStore.canWrite(current.value.province)) {
    $msg.warn('无权操作该省份的配置')
    return
  }
  deleting.value = true
  try {
    const { province: dp, intent: di } = current.value
    await acDeleteSkill(dp, di)
    $msg.ok('删除成功')
    uiLog.warn('SkillManager', `删除配置：${dp}/${di}`)
    deleteVisible.value = false
    // 立即从列表与缓存中移除该行（乐观更新），避免后端列表短暂最终一致导致仍显示已删项；
    // 随后再拉取一次以对齐真实状态。
    skills.value = skills.value.filter(s => !(s.province === dp && s.intent === di))
    allSkillsCache.value = (allSkillsCache.value || []).filter(s => !(s.province === dp && s.intent === di))
    await loadSkills()
  } catch (e) {
    const text = $msg.errOf(e, '删除失败')
    uiLog.error('SkillManager', `删除失败：${current.value.province}/${current.value.intent}`, text)
  } finally {
    deleting.value = false
  }
}

// ── 初始化 ────────────────────────────────────────────
onMounted(async () => {
  await authStore.fetchMe()
  // 非本部用户：省份筛选锁定为自身省份
  if (!authStore.isHQ && authStore.province) {
    filter.value.province = authStore.province
  }
  await loadSkills()
  // 快速创建后跳转：自动定位并打开该技能的编辑抽屉，便于补配接口/话术
  const ep = route.query.edit_province
  const ei = route.query.edit_intent
  if (ep && ei) {
    const row = skills.value.find(s => s.province === ep && s.intent === ei)
    if (row) {
      openEdit(row)
    } else {
      $msg.info('新配置已创建，请在列表中找到并编辑补充接口与话术模板')
    }
    // 清理 query，避免刷新重复打开
    router.replace({ path: '/SkillManager' })
  }
})
</script>

<style scoped>
/* ── 三段式布局：PageHeader + PageFilter + PageContent（规范 3）── */
.page-header { margin-bottom: 12px; }
.page-header-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap;
}
.page-header-actions { display: flex; align-items: center; gap: 0; }

/* ── 当前登录用户信息 ── */
.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-right: 14px;
  padding-right: 14px;
  border-right: 1px solid var(--el-border-color, #e4e7ed);
}
.user-info-avatar {
  font-size: 26px;
  color: #3b5bdb;
  flex-shrink: 0;
}
.user-info-meta { display: flex; flex-direction: column; gap: 3px; line-height: 1.1; }
.user-info-line-1 { font-size: 13px; }
.user-info-name { font-weight: 600; color: var(--el-text-color-primary, #303133); }
.user-info-account { font-size: 12px; color: var(--muted, #909399); }
.user-info-line-2 { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }

.page-filter { margin-bottom: 12px; }
.page-content { min-width: 0; }

/* ── 编辑弹窗：内容区独立滚动，自适应高度 ── */
.edit-dialog-body {
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  padding-right: 2px;
}
/* 滚动条美化 */
.edit-dialog-body::-webkit-scrollbar { width: 5px; }
.edit-dialog-body::-webkit-scrollbar-track { background: #f1f3f5; border-radius: 4px; }
.edit-dialog-body::-webkit-scrollbar-thumb { background: #ced4da; border-radius: 4px; }
.edit-dialog-body::-webkit-scrollbar-thumb:hover { background: #adb5bd; }

/* ── 拖拽拉宽手柄 ── */
.edit-dialog-resize-handle {
  position: absolute;
  left: -4px;
  top: 0;
  width: 8px;
  height: 100%;
  cursor: ew-resize;
  z-index: 10;
  border-radius: 4px 0 0 4px;
}
.edit-dialog-resize-handle:hover,
.edit-dialog-resize-handle:active {
  background: rgba(66, 99, 235, 0.15);
}

/* 让 el-dialog 内部 position: relative 才能定位手柄 */
:deep(.edit-skill-dialog .el-dialog) {
  position: relative;
  max-height: 94vh;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
}
:deep(.edit-skill-dialog .el-dialog__header) {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(135deg, #f8faff 0%, #fff 100%);
  border-radius: 12px 12px 0 0;
}
:deep(.edit-skill-dialog .el-dialog__title) {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}
:deep(.edit-skill-dialog .el-dialog__body) {
  flex: 1;
  overflow: hidden;
  padding: 16px 24px 0;
  display: flex;
  flex-direction: column;
}
:deep(.edit-skill-dialog .el-dialog__footer) {
  padding: 12px 24px;
  border-top: 1px solid var(--border);
  background: #fafafa;
  border-radius: 0 0 12px 12px;
}
/* SkillConfigEditor tab card 美化 */
:deep(.edit-skill-dialog .el-tabs--border-card) {
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: none;
}
:deep(.edit-skill-dialog .el-tabs--border-card > .el-tabs__header) {
  background: #f8f9fa;
  border-bottom: 1px solid var(--border);
  border-radius: 8px 8px 0 0;
}
:deep(.edit-skill-dialog .el-tabs--border-card > .el-tabs__content) {
  padding: 16px;
}

.td-name { font-weight: 500; }
.empty-tip { text-align: center; padding: 32px; color: var(--muted); font-size: 13px; }
.ops-sep { color: #dee2e6; margin: 0 1px; }
.btn-link.success { color: #67c23a; }
.btn-link.success:hover { color: #529b2e; }
.btn-link.warn { color: #e6a23c; }
.btn-link.warn:hover { color: #b88230; }
.btn-link.disabled {
  color: #c0c4cc;
  cursor: not-allowed;
  pointer-events: none;
}
.json-pre {
  font-size: 12px;
  background: #f8f9fa;
  padding: 14px;
  border-radius: 6px;
  overflow: auto;
  max-height: 520px;
  white-space: pre;
  font-family: Menlo, Consolas, monospace;
  line-height: 1.6;
}

/* ── Skill 测试弹窗（样式与 TestConsole 完全一致） ── */
.tc-layout {
  display: grid; grid-template-columns: 320px 1fr; gap: 20px;
  max-height: 72vh;
}
.tc-card {
  background: var(--card, #fff); border: 1px solid var(--border);
  border-radius: var(--radius, 10px); padding: 20px;
  overflow-y: auto; max-height: 72vh;
}
.tc-card-title { font-size: 15px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tc-autofill-btn {
  margin-left: auto; font-size: 12px; font-weight: 500; cursor: pointer;
  border: 1px solid #c3d0e8; background: #f7faff; color: #2563eb;
  border-radius: 6px; padding: 4px 10px; transition: all .15s;
}
.tc-autofill-btn:hover:not(:disabled) { background: #eef2ff; border-color: #2563eb; }
.tc-autofill-btn:disabled { opacity: .6; cursor: not-allowed; }
.tc-autofill-hint {
  font-size: 12px; line-height: 1.6; color: var(--muted);
  background: #f8f9fb; border: 1px dashed #dbe2ea; border-radius: 6px;
  padding: 8px 10px; margin-bottom: 12px;
}
.tc-label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 4px; margin-top: 12px; }
.tc-label:first-of-type { margin-top: 0; }
/* 测试用例栏 */
.tc-case-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 10px; margin-bottom: 12px;
  background: #f8fafc; border: 1px solid var(--border); border-radius: 7px;
}
.tc-case-select { flex: 1 1 140px; min-width: 120px; }
.tc-case-name { flex: 2 1 180px; min-width: 140px; }
.tc-case-btn { padding: 8px 12px; font-size: 13px; }
/* 完整请求体头部 */
.tc-fullbody-head { display: flex; align-items: center; justify-content: space-between; margin-top: 16px; }
.tc-fullbody-tag {
  display: inline-block; margin-left: 8px; padding: 1px 8px; border-radius: 10px;
  background: #eef2ff; color: #2563eb; font-size: 11px; font-weight: 600;
}
.tc-fullbody-tools { display: flex; gap: 12px; }
.tc-mini-link { font-size: 12px; color: var(--primary); cursor: pointer; }
.tc-mini-link:hover { text-decoration: underline; }
.tc-input {
  width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 7px;
  font-size: 14px; outline: none; transition: .2s; font-family: inherit; box-sizing: border-box;
  background: #fff; color: var(--text);
}
.tc-input:disabled { background: #f8fafc; color: var(--muted); cursor: not-allowed; }
.tc-input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,.12); }
.tc-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  padding: 9px 20px; border: none; border-radius: 7px;
  font-size: 14px; font-weight: 500; cursor: pointer; transition: .2s;
}
.tc-btn-primary { background: var(--primary); color: #fff; }
.tc-btn-primary:hover { background: #1d4ed8; }
.tc-btn-secondary { background: #f1f5f9; color: var(--text); }
.tc-btn-secondary:hover { background: #e2e8f0; }
.tc-btn:disabled { opacity: .5; cursor: not-allowed; }
.tc-btn-block { width: 100%; }
.tc-divider { border: none; border-top: 1px solid var(--border); margin: 16px 0; }
.tc-result-wrap { overflow-y: auto; min-width: 0; max-height: 72vh; }
.tc-status-bar {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; padding: 10px 14px; border-radius: 7px; margin-bottom: 12px;
}
.status-idle    { background: #f1f5f9; color: var(--muted); }
.status-loading { background: #eff6ff; color: var(--primary); }
.status-ok      { background: #f0fdf4; color: var(--success, #15803d); }
.status-err     { background: #fef2f2; color: var(--danger, #c92a2a); }
.tc-result-pane { display: flex; flex-direction: column; gap: 16px; }
.tc-step-card { border: 1px solid var(--border); border-radius: var(--radius, 10px); overflow: hidden; }
.tc-step-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px; background: #f8fafc; border-bottom: 1px solid var(--border);
  cursor: pointer; user-select: none;
}
.tc-step-title { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.tc-step-badge { font-size: 12px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }
.badge-blue   { background: #dbeafe; color: #1d4ed8; }
.badge-green  { background: #dcfce7; color: #15803d; }
.badge-purple { background: #f3e8ff; color: #7e22ce; }
.badge-red    { background: #fee2e2; color: #b91c1c; }
.tc-step-body { padding: 14px 16px; display: none; }
.tc-step-body.open { display: block; }
.test-empty { font-size: 13px; color: var(--muted); padding: 12px; }
:deep(.tc-step-body .meta-row) { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); padding: 4px 0; }
:deep(.tc-step-body .script-item) { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 14px; margin-bottom: 10px; }
:deep(.tc-step-body .script-rank) { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
:deep(.tc-step-body .script-text) { font-size: 14px; line-height: 1.7; color: var(--text); }
:deep(.tc-step-body .diff-table) { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
:deep(.tc-step-body .diff-table th) { background: #f1f5f9; padding: 8px 10px; text-align: left; font-weight: 600; color: var(--muted); border: 1px solid var(--border); }
:deep(.tc-step-body .diff-table td) { padding: 8px 10px; border: 1px solid var(--border); }
:deep(.tc-step-body pre) { background: #0f172a; color: #e2e8f0; padding: 14px; border-radius: 8px; font-size: 12px; overflow: auto; max-height: 360px; line-height: 1.6; margin: 0; }
:deep(.tc-step-body .tc-divider) { border: none; border-top: 1px solid var(--border); margin: 16px 0; }

/* 配置来源汇总条 */
.sync-status-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 16px; margin-bottom: 8px;
  background: #f8fafc; border: 1px solid var(--border); border-radius: 8px;
  font-size: 12px;
}
.sync-status-label { color: var(--muted); font-weight: 500; }
.sync-status-warn { color: #b45309; margin-left: 4px; }
.sync-status-ok { color: #15803d; margin-left: 4px; }

/* 数据流概览审计 */
.audit-section-title { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 8px; }
.audit-domain-grid { display: flex; flex-direction: column; gap: 6px; }
.audit-domain-chip {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border);
  background: #fafafa; font-size: 12px;
}
.audit-domain-chip.supplied { background: #f0fdf4; border-color: #bbf7d0; }
.audit-domain-label { font-weight: 600; min-width: 72px; }
.audit-domain-key { font-size: 11px; color: var(--muted); }
.audit-domain-providers { color: #15803d; font-size: 11px; }
.audit-domain-missing { color: #c92a2a; font-size: 11px; }
.audit-tpl-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 10px; border-radius: 4px; font-size: 12px; margin-bottom: 4px;
  background: #f8fafc;
}
.audit-tpl-row.is-warn { background: #fffbeb; }
.audit-tpl-name { font-weight: 500; }
.audit-tpl-ok { color: #15803d; }
.audit-tpl-warn { color: #b45309; }
.audit-issue { font-size: 12px; padding: 4px 0; display: flex; align-items: center; gap: 6px; }
</style>
