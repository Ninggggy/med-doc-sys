<template>
  <div class="toolbar-shell">
    <div class="toolbar-row">
      <div class="actions">
        <el-button type="success" size="small" :loading="running" @click="$emit('run-all')">进行完整辅助审评</el-button>
        <el-button
          size="small"
          :loading="runningSection"
          :disabled="!currentBrowseSectionId"
          @click="$emit('run-section')"
        >
          审评当前章节
        </el-button>
        <span v-if="sectionActionHint" class="section-action-hint">{{ sectionActionHint }}</span>
        <el-button
          size="small"
          :loading="runningModule"
          :disabled="!currentBrowseScopeSectionId"
          @click="$emit('run-module')"
        >
          审评当前模块
        </el-button>
        <el-button size="small" :disabled="!hasReviewResult" @click="$emit('open-review-result')">查看审评结论</el-button>
        <el-button
          size="small"
          :loading="exportingReviewConclusions"
          :disabled="!canExportReviewConclusions"
          @click="$emit('export-review-conclusions')"
        >
          导出审评结论
        </el-button>
        <el-button size="mini" :disabled="!activeDocId" @click="$emit('open-preview')">查看原文件</el-button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: "PreReviewActionToolbar",
  props: {
    running: { type: Boolean, default: false },
    runningSection: { type: Boolean, default: false },
    runningModule: { type: Boolean, default: false },
    currentBrowseSectionId: { type: String, default: "" },
    currentBrowseScopeSectionId: { type: String, default: "" },
    activeDocId: { type: String, default: "" },
    hasReviewResult: { type: Boolean, default: false },
    canExportReviewConclusions: { type: Boolean, default: false },
    exportingReviewConclusions: { type: Boolean, default: false },
    sectionActionHint: { type: String, default: "" },
  },
};
</script>

<style scoped>
.toolbar-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.section-action-hint {
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
}
</style>
