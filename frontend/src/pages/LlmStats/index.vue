<template>
  <div>
    <!-- ══ PageHeader：面包屑（对齐 SkillManager 规范）══ -->
    <div class="page-header">
      <div class="page-header-row">
        <div class="breadcrumb" style="margin-bottom:0;">
          <div class="breadcrumb-item">话术智能体运营</div>
          <div class="breadcrumb-item active"><span class="crumb-dot"></span>模型调用统计</div>
        </div>
      </div>
    </div>

    <!-- 查询面板（共享组件，SkillManager 弹窗同款） -->
    <div class="panel-wrap">
      <LlmStatsPanel />
    </div>
  </div>
</template>

<script setup>
import LlmStatsPanel from '@/components/LlmStatsPanel.vue'
</script>

<style scoped>
.page-header {
  padding: 12px 16px 0;
}
.page-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.panel-wrap {
  margin: 12px 16px 16px;
}
</style>
