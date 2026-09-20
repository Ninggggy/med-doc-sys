<template>
  <div v-if="detail" class="block">
    <div class="header-row">
      <div>
        <div class="title">检索评估概览</div>
        <div class="muted">已评估反馈数：{{ metricNumber(detail.metrics && detail.metrics.evaluated_feedback_count) }}</div>
      </div>
      <div class="actions">
        <el-button
          v-if="hasTraceArtifact"
          size="mini"
          type="primary"
          plain
          :loading="loadingArtifact"
          @click="$emit('view-trace-artifact')"
        >
          查看 trace 原文件
        </el-button>
      </div>
    </div>

    <div class="metric-row">
      <span>Precision：{{ metricPercent(detail.metrics && detail.metrics.precision) }}</span>
      <span>Recall：{{ metricPercent(detail.metrics && detail.metrics.recall) }}</span>
      <span>F1：{{ metricPercent(detail.metrics && detail.metrics.f1) }}</span>
    </div>

    <div v-if="traceArtifact && traceArtifact.file_name" class="artifact-tip">
      <div class="muted">artifact 文件：{{ traceArtifact.file_name }}</div>
      <div class="muted artifact-path" :title="traceArtifact.file_path || ''">
        artifact 路径：{{ traceArtifact.file_path || '-' }}
      </div>
    </div>

    <div class="two-col">
      <div class="inner-block">
        <div class="subtitle">来源分布</div>
        <div v-if="sourceEntries.length">
          <div v-for="item in sourceEntries" :key="item.key" class="list-item">
            {{ item.label }}：{{ metricNumber(item.count) }}
          </div>
        </div>
        <div v-else class="empty">当前没有可展示的来源分布。</div>
      </div>

      <div class="inner-block">
        <div class="subtitle">错误分布</div>
        <div v-if="errorEntries.length">
          <div v-for="item in errorEntries" :key="item.key" class="issue-card">
            <div class="issue-title">{{ item.label }}</div>
            <div class="muted">次数：{{ metricNumber(item.count) }}</div>
          </div>
        </div>
        <div v-else class="empty">当前没有可展示的检索错误分布。</div>
      </div>
    </div>
  </div>
</template>

<script>
const ERROR_LABELS = {
  query_miss: 'Query 漏召',
  retrieval_scope_error: '检索范围错误',
  retrieval_ranking_error: '检索排序错误',
  historical_experience_missing: '历史经验缺失',
  section_fact_extraction_error: '章节事实抽取错误',
  focus_point_miss: '审评规则遗漏',
  evidence_interpretation_error: '证据理解错误',
  over_inference: '过度推断',
  under_identification: '识别不足',
  wrong_severity: '风险等级错误',
  wording_not_actionable: '表述不可执行',
  missing_regulatory_basis: '缺失法规依据',
  unhelpful_question_to_applicant: '对申请人问题无帮助',
};

export default {
  name: 'RetrievalTracePanel',
  props: {
    detail: {
      type: Object,
      default: () => ({ metrics: {}, source_breakdown: {}, error_breakdown: {} }),
    },
    metricPercent: {
      type: Function,
      default: (value) => {
        const num = Number(value);
        return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : '-';
      },
    },
    metricNumber: {
      type: Function,
      default: (value) => {
        const num = Number(value);
        return Number.isFinite(num) ? `${num}` : '-';
      },
    },
    traceArtifact: { type: Object, default: null },
    loadingArtifact: { type: Boolean, default: false },
  },
  computed: {
    hasTraceArtifact() {
      return !!(this.traceArtifact && this.traceArtifact.persisted && this.traceArtifact.file_path);
    },
    sourceEntries() {
      const sourceBreakdown = this.detail && this.detail.source_breakdown && typeof this.detail.source_breakdown === 'object'
        ? this.detail.source_breakdown
        : {};
      return Object.keys(sourceBreakdown)
        .map((key) => ({ key, label: key, count: sourceBreakdown[key] }))
        .sort((a, b) => Number(b.count || 0) - Number(a.count || 0));
    },
    errorEntries() {
      const errorBreakdown = this.detail && this.detail.error_breakdown && typeof this.detail.error_breakdown === 'object'
        ? this.detail.error_breakdown
        : {};
      return Object.keys(errorBreakdown)
        .map((key) => ({ key, label: ERROR_LABELS[key] || key, count: errorBreakdown[key] }))
        .sort((a, b) => Number(b.count || 0) - Number(a.count || 0));
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

.header-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title,
.issue-title,
.subtitle {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.subtitle {
  margin-bottom: 8px;
}

.muted,
.list-item,
.empty {
  font-size: 12px;
  line-height: 1.7;
  color: #64748b;
}

.metric-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 12px;
  font-size: 12px;
  color: #334155;
}

.artifact-tip {
  margin-top: 12px;
  padding: 10px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.artifact-path {
  word-break: break-all;
}

.two-col {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.inner-block {
  border: 1px solid #eef1f6;
  border-radius: 8px;
  padding: 10px;
  background: #fff;
}

.issue-card {
  border: 1px solid #edf2f7;
  border-radius: 8px;
  background: #f8fafc;
  padding: 10px;
  margin-bottom: 8px;
}

@media (max-width: 900px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}
</style>
