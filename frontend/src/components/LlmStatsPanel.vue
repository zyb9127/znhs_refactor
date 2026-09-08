<template>
  <div class="llm-stats-panel">
    <!-- ══ 筛选栏 ══ -->
    <div class="filter-bar">
      <div class="filter-group">
        <label>日期范围</label>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          :clearable="false"
          :disabled-date="disabledDate"
          style="width:280px"
          @change="loadAll"
        />
      </div>
      <div class="filter-group">
        <label>省份</label>
        <el-select
          v-model="filterProvince"
          placeholder="全部"
          clearable
          filterable
          style="width:160px"
          @change="loadRows"
        >
          <el-option
            v-for="p in provinceOptions"
            :key="p"
            :label="p"
            :value="p"
          />
        </el-select>
      </div>
      <div class="filter-actions">
        <el-button size="small" :loading="loading" @click="loadAll">🔄 刷新</el-button>
      </div>
    </div>

    <!-- ══ 统计未启用（dev 模式 / Redis 不可用）══ -->
    <el-alert
      v-if="!loading && !enabled"
      type="info"
      :closable="false"
      show-icon
      title="模型调用量统计未启用"
      description="当前环境未初始化 Redis 统计通道（本地开发模式或 ZNHS_LLM_STATS=0），生产/灰度环境启动后自动开启。"
      style="margin-bottom:12px;"
    />

    <template v-if="enabled">
      <!-- ══ 汇总卡片 ══ -->
      <div class="summary-cards">
        <div class="summary-card">
          <div class="summary-label">总调用次数</div>
          <div class="summary-value">{{ summary.calls }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">成功率</div>
          <div class="summary-value">{{ summary.successRate }}%</div>
          <div class="progress-track">
            <div class="progress-fill" :style="{ width: summary.successRate + '%' }"></div>
          </div>
        </div>
        <div class="summary-card">
          <div class="summary-label">Prompt Tokens</div>
          <div class="summary-value">{{ summary.promptTokens }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">Completion Tokens</div>
          <div class="summary-value">{{ summary.completionTokens }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">平均耗时</div>
          <div class="summary-value">{{ summary.avgElapsed }} ms</div>
        </div>
      </div>

      <!-- ══ 明细表格 ══ -->
      <div class="table-card">
        <el-table :data="rows" v-loading="loading" stripe style="width:100%">
          <template #empty>
            <el-empty description="所选范围内暂无调用量数据" :image-size="80" />
          </template>
          <el-table-column label="日期" prop="date" width="120" sortable />
          <el-table-column label="省份" prop="province" width="110" />
          <el-table-column label="调用次数" prop="calls" width="100" sortable />
          <el-table-column label="成功 / 失败" width="110">
            <template #default="{ row }">
              <span class="ok-text">{{ row.success }}</span> / <span class="err-text">{{ row.fail }}</span>
            </template>
          </el-table-column>
          <el-table-column label="成功率" min-width="160">
            <template #default="{ row }">
              <div class="rate-cell">
                <div class="progress-track">
                  <div
                    class="progress-fill"
                    :class="{ 'progress-warn': rateOf(row) < 90 }"
                    :style="{ width: rateOf(row) + '%' }"
                  ></div>
                </div>
                <span class="rate-text">{{ rateOf(row) }}%</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="Prompt Tokens" prop="prompt_tokens" width="130" sortable />
          <el-table-column label="Completion Tokens" prop="completion_tokens" width="150" sortable />
          <el-table-column label="平均耗时(ms)" prop="avg_elapsed_ms" width="120" sortable />
          <el-table-column label="意图细分" min-width="220">
            <template #default="{ row }">
              <el-tag
                v-for="(v, intent) in row.intents"
                :key="intent"
                size="small"
                effect="plain"
                style="margin:2px 4px 2px 0"
              >
                {{ intent }}：{{ v.calls }} 次<span v-if="v.fail">（败 {{ v.fail }}）</span>
              </el-tag>
              <span v-if="!row.intents || !Object.keys(row.intents).length">—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>
  </div>
</template>

<script setup>
/**
 * LlmStatsPanel — 分省模型调用量统计查询面板（共享组件）
 *
 * 数据自取（/api/stats/llm/daily + /provinces，走 @/api axios 实例自动带 satoken），
 * 无 props。两处使用：
 *   - pages/LlmStats/index.vue（独立页 /LlmStats）
 *   - SkillManager 头部「调用统计」弹窗（v-if 懒挂载，打开时才发请求）
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/api'

const loading = ref(false)
const enabled = ref(true)
const rows = ref([])
const provinceOptions = ref([])
const filterProvince = ref('')

// 默认近 7 天
function fmt(d) {
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
const _today = new Date()
const _weekAgo = new Date(_today.getTime() - 6 * 86400000)
const dateRange = ref([fmt(_weekAgo), fmt(_today)])

// 只允许选今天及以前（日期范围上限 40 天由后端校验，与 Redis TTL 对齐）
function disabledDate(d) {
  return d.getTime() > Date.now()
}

function rateOf(row) {
  if (!row.calls) return 0
  return Math.round((row.success / row.calls) * 1000) / 10
}

const summary = computed(() => {
  const s = { calls: 0, success: 0, promptTokens: 0, completionTokens: 0, elapsedSum: 0 }
  for (const r of rows.value) {
    s.calls += r.calls || 0
    s.success += r.success || 0
    s.promptTokens += r.prompt_tokens || 0
    s.completionTokens += r.completion_tokens || 0
    s.elapsedSum += (r.avg_elapsed_ms || 0) * (r.calls || 0)
  }
  return {
    calls: s.calls,
    successRate: s.calls ? Math.round((s.success / s.calls) * 1000) / 10 : 0,
    promptTokens: s.promptTokens,
    completionTokens: s.completionTokens,
    avgElapsed: s.calls ? Math.round(s.elapsedSum / s.calls) : 0,
  }
})

async function loadRows() {
  if (!dateRange.value || dateRange.value.length !== 2) return
  loading.value = true
  try {
    const params = { start: dateRange.value[0], end: dateRange.value[1] }
    if (filterProvince.value) params.province = filterProvince.value
    const res = await http.get('/api/stats/llm/daily', { params })
    const data = res?.data || {}
    enabled.value = data.enabled !== false
    rows.value = data.rows || []
  } catch (e) {
    ElMessage.error('调用量统计查询失败：' + (e?.response?.data?.detail || e.message || e))
  } finally {
    loading.value = false
  }
}

async function loadProvinces() {
  if (!dateRange.value || dateRange.value.length !== 2) return
  try {
    const res = await http.get('/api/stats/llm/provinces', {
      params: { start: dateRange.value[0], end: dateRange.value[1] },
    })
    const data = res?.data || {}
    provinceOptions.value = data.provinces || []
    // 当前选中的省份已不在范围内时清空
    if (filterProvince.value && !provinceOptions.value.includes(filterProvince.value)) {
      filterProvince.value = ''
    }
  } catch { /* 下拉加载失败不阻断主流程 */ }
}

async function loadAll() {
  await Promise.all([loadRows(), loadProvinces()])
}

onMounted(loadAll)
</script>

<style scoped>
.llm-stats-panel .filter-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-group label {
  font-size: 13px;
  color: #606266;
  white-space: nowrap;
}
.filter-actions {
  display: flex;
  gap: 8px;
}
.summary-cards {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.summary-card {
  flex: 1;
  min-width: 150px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
}
.summary-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
.summary-value {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
}
.table-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 8px;
}
.progress-track {
  height: 8px;
  background: #f0f2f5;
  border-radius: 4px;
  overflow: hidden;
  margin-top: 6px;
}
.progress-fill {
  height: 100%;
  background: #67c23a;
  border-radius: 4px;
  transition: width .3s;
}
.progress-fill.progress-warn {
  background: #e6a23c;
}
.rate-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rate-cell .progress-track {
  flex: 1;
  margin-top: 0;
}
.rate-text {
  font-size: 12px;
  color: #606266;
  min-width: 44px;
  text-align: right;
}
.ok-text { color: #67c23a; }
.err-text { color: #f56c6c; }
</style>
