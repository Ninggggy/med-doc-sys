<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">备案变更审评报告</h2>
        <div class="page-desc">项目：{{ projectId }}</div>
      </div>
    </div>
    <el-card>
      <div slot="header">报告内容 · 审评轮次 {{ (report || {}).run_id || "未记录" }}</div>
      <el-empty v-if="!report" description="暂无报告" />
      <div v-else>
        <div class="report-content markdown-preview" v-html="renderMarkdown(report.report_content)"></div>
      </div>
    </el-card>
  </div>
</template>

<script>
import { getFilingProjectReport } from "@/api/filingChangeReview";
import MarkdownIt from "markdown-it";

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });

export default {
  name: "FilingChangeReviewReport",
  data() {
    return {
      report: null,
    };
  },
  computed: {
    projectId() {
      return this.$route.params.projectId;
    },
  },
  async mounted() {
    try {
      const res = await getFilingProjectReport(this.projectId, this.$route.query.run_id || "");
      this.report = (res && res.data) || null;
    } catch (_) {
      this.report = null;
    }
  },
  methods: {
    renderMarkdown(source) {
      const text = String(source || "").trim();
      if (!text) return '<div class="page-desc">暂无报告内容</div>';
      return md.render(text);
    },
  },
};
</script>

<style scoped>
.report-content {
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.7;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
}
.markdown-preview :deep(h1), .markdown-preview :deep(h2), .markdown-preview :deep(h3) { margin: 10px 0 8px; }
.markdown-preview :deep(p) { margin: 0 0 8px; }
.markdown-preview :deep(ul), .markdown-preview :deep(ol) { margin: 0 0 8px 20px; }
</style>
