<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">仪表盘</h2>
        <div class="page-desc">查看知识库规模、辅助审评项目状态与整体准确率</div>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :sm="12" :md="8">
        <el-card class="k-card metric-card">
          <div class="metric-label">知识库文件总数</div>
          <div class="metric-value">{{ cards.knowledge }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="8">
        <el-card class="k-card metric-card">
          <div class="metric-label">辅助审评项目总数</div>
          <div class="metric-value">{{ cards.projects }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="8">
        <el-card class="k-card metric-card">
          <div class="metric-label">平均准确率</div>
          <div class="metric-value">{{ cards.accuracy }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="k-card">
      <div slot="header">系统用户流程</div>
      <el-steps :active="4" finish-status="success" align-center>
        <el-step title="知识入库" description="上传 PDF/Word/TXT 等文件" />
        <el-step title="知识解析" description="分块、摘要、关键词索引" />
        <el-step title="按章节辅助审评" description="申报资料结构化审查" />
        <el-step title="反馈优化" description="人工标注提升审评质量" />
      </el-steps>
    </el-card>
  </div>
</template>

<script>
import { queryKnowledge } from "@/api/knowledge";
import { dashboardSummary } from "@/api/prereview";

export default {
  name: "Dashboard",
  data() {
    return {
      cards: { knowledge: 0, projects: 0, accuracy: "-" },
    };
  },
  async mounted() {
    try {
      const [k, p] = await Promise.all([queryKnowledge({ page: 1, page_size: 1 }), dashboardSummary()]);
      this.cards.knowledge = k.data.total || 0;
      this.cards.projects = p.data.project_total || 0;
      this.cards.accuracy =
        p.data.avg_accuracy === null || p.data.avg_accuracy === undefined ? "-" : `${(p.data.avg_accuracy * 100).toFixed(1)}%`;
    } catch (_) {
      this.cards.accuracy = "-";
    }
  },
};
</script>

<style scoped>
.metric-card {
  margin-bottom: 12px;
}

.metric-label {
  color: var(--sub);
  font-size: 13px;
}

.metric-value {
  margin-top: 8px;
  font-size: 30px;
  font-weight: 700;
  color: #0d47a1;
}
</style>
