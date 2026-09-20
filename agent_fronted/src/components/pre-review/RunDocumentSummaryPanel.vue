<template>
  <div v-if="summary" class="block">
    <div class="title">全文汇总结果</div>
    <div class="meta-row">
      <span>已审评章节 {{ reviewedSectionTotal }}</span>
      <span>待审评章节 {{ pendingSectionTotal }}</span>
      <span>章节总数 {{ sectionTotal }}</span>
    </div>

    <div class="subtitle">章节结论分布</div>
    <el-table
      v-if="chapterRows.length"
      :data="chapterRows"
      size="mini"
      border
      max-height="240"
      @row-click="onSelect"
    >
      <el-table-column prop="section_id" label="章节" width="120" />
      <el-table-column prop="section_name" label="标题" min-width="140" />
      <el-table-column label="状态" width="84">
        <template slot-scope="{ row }">
          <span>{{ row.status === "reviewed" ? "已审评" : "待审评" }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="risk_level" label="风险" width="84" />
      <el-table-column label="结论" min-width="180" show-overflow-tooltip>
        <template slot-scope="{ row }">
          {{ row.conclusion || row.pre_review_conclusion || "-" }}
        </template>
      </el-table-column>
    </el-table>
    <div v-else class="empty">当前还没有章节级汇总结果。</div>

    <div v-if="pendingSections.length" class="subtitle">待审评章节</div>
    <div v-if="pendingSections.length" class="pending-list">
      <span v-for="item in pendingSections" :key="item" class="pending-chip">{{ item }}</span>
    </div>
  </div>
</template>

<script>
export default {
  name: "RunDocumentSummaryPanel",
  props: {
    summary: {
      type: Object,
      default: null,
    },
  },
  computed: {
    reviewedSectionTotal() {
      return Number(this.summary && this.summary.reviewed_section_total) || 0;
    },
    pendingSectionTotal() {
      return Number(this.summary && this.summary.pending_section_total) || 0;
    },
    sectionTotal() {
      return Number(this.summary && this.summary.section_total) || 0;
    },
    chapterRows() {
      const rows = this.summary && this.summary.chapter_conclusion_summary;
      return Array.isArray(rows) ? rows : [];
    },
    pendingSections() {
      const items = this.summary && this.summary.pending_sections;
      return Array.isArray(items) ? items : [];
    },
  },
  methods: {
    onSelect(row) {
      if (row && row.section_id) {
        this.$emit("select-section", row.section_id);
      }
    },
  },
};
</script>

<style scoped>
.block {
  border: 1px solid #eef1f6;
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: #2d3648;
}

.subtitle {
  margin: 12px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #2d3648;
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
  color: #5a667a;
  font-size: 12px;
}

.empty {
  color: #8a94a6;
  font-size: 12px;
  line-height: 1.6;
}

.pending-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pending-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: #f7f9fc;
  border: 1px solid #e6ebf2;
  color: #5a667a;
  font-size: 12px;
}
</style>
