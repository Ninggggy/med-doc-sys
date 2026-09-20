<template>
  <div class="metrics-card">
    <div class="panel-head">
      <div class="title">运行指标</div>
      <el-button size="mini" type="primary" :loading="loadingAblation" @click="$emit('run-ablation')">
        执行消融实验
      </el-button>
    </div>

    <div class="metric-row">
      <span>问题发现 A / R / F1</span>
      <strong>{{ metricPercent(reviewAccuracy) }} / {{ metricPercent(reviewRecall) }} / {{ metricPercent(reviewF1) }}</strong>
    </div>

    <div class="metric-row">
      <span>检索证据 A / R / F1</span>
      <strong>{{ metricPercent(retrievalAccuracy) }} / {{ metricPercent(retrievalRecall) }} / {{ metricPercent(retrievalF1) }}</strong>
    </div>

    <div class="metric-row">
      <span>结论匹配率</span>
      <strong>{{ metricPercent(conclusionMatchRate) }}</strong>
    </div>

    <div class="metric-row">
      <span>反馈采纳率 / 人工修改率</span>
      <strong>{{ metricPercent(feedback.feedback_acceptance_rate) }} / {{ metricPercent(feedback.manual_modification_rate) }}</strong>
    </div>

    <div class="metric-row">
      <span>误报下降率</span>
      <strong>{{ metricPercent(trajectory.false_positive_reduction_rate) }}</strong>
    </div>

    <div class="metric-grid">
      <div class="metric-chip">正确发现 {{ metricNumber(reviewCorrectIssueCount) }}</div>
      <div class="metric-chip">错报 {{ metricNumber(reviewFalsePositiveIssueCount) }}</div>
      <div class="metric-chip">漏报 {{ metricNumber(reviewMissedIssueCount) }}</div>
      <div class="metric-chip">检索 TP / FP / FN {{ metricNumber(retrievalTruePositiveCount) }} / {{ metricNumber(retrievalFalsePositiveCount) }} / {{ metricNumber(retrievalFalseNegativeCount) }}</div>
      <div class="metric-chip">反馈总数 {{ metricNumber(feedback.feedback_count || review.feedback_total) }}</div>
      <div class="metric-chip">优化事件 {{ metricNumber(feedback.feedback_optimize_count) }}</div>
      <div class="metric-chip">优化成功 {{ metricNumber(feedback.feedback_optimize_success_count) }}</div>
      <div class="metric-chip">优化失败 {{ metricNumber(feedback.feedback_optimize_failed_count) }}</div>
      <div class="metric-chip">候选注册成功 {{ metricNumber(feedback.candidate_register_success_count) }}</div>
      <div class="metric-chip">候选注册失败 {{ metricNumber(feedback.candidate_register_failed_count) }}</div>
      <div class="metric-chip">Replay 通过 {{ metricNumber(feedback.replay_pass_count) }}</div>
      <div class="metric-chip">Replay 失败 {{ metricNumber(feedback.replay_failed_count) }}</div>
    </div>

    <div class="ablation-box">
      <div class="sub-title">消融实验</div>
      <div v-if="hasAblation">
        <div v-if="isP52Ablation" class="metric-row">
          <span>P52 链路能力对比</span>
          <strong>{{ p52CapabilityFocusText }}</strong>
        </div>
        <div class="metric-row">
          <span>案例数 / 变体数</span>
          <strong>{{ metricNumber(generatedCaseCount) }} / {{ metricNumber(variantCount) }}</strong>
        </div>
        <div class="metric-row">
          <span>最佳变体</span>
          <strong>{{ bestVariantLabel }}</strong>
        </div>
        <div class="metric-row">
          <span>最佳总分</span>
          <strong>{{ metricPercent(bestOverallScore) }}</strong>
        </div>
        <div class="metric-row">
          <span>相对基线 Delta</span>
          <strong>{{ deltaPercent(bestOverallDelta) }}</strong>
        </div>
        <div v-if="topRanking.length" class="ranking-list">
          <div v-for="item in topRanking" :key="item.variant_id" class="ranking-item">
            <span>{{ item.label || item.variant_id || '-' }}</span>
            <strong>{{ metricPercent(item.overall_score) }}</strong>
          </div>
        </div>
        <div v-if="isP52Ablation && ablationVariants.length" class="p52-capability-list">
          <div v-for="variant in ablationVariants" :key="`p52-cap-${variant.variant_id}`" class="p52-capability-card">
            <div class="metric-row">
              <span>{{ variant.label || variant.variant_id || '-' }}</span>
              <strong>{{ metricPercent(variant.metrics && variant.metrics.overall_score) }}</strong>
            </div>
            <div class="muted">{{ variant.description || '-' }}</div>
            <div class="muted">能力开关：{{ p52FeatureFlagText(variant.feature_flags) }}</div>
            <div v-if="variant.delta_vs_baseline" class="muted">
              相对完整链路：总分 {{ deltaPercent(variant.delta_vs_baseline.overall_delta) }}，
              Review {{ deltaPercent(variant.delta_vs_baseline.review_delta) }}，
              Retrieval {{ deltaPercent(variant.delta_vs_baseline.retrieval_delta) }}
            </div>
          </div>
        </div>
      </div>
      <div v-else class="empty">当前还没有消融实验结果。点击上方按钮后，会基于当前轮次和章节生成回放案例执行对比。</div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'RunMetricsPanel',
  props: {
    metrics: {
      type: Object,
      default: () => ({ review: {}, retrieval: {}, feedback: {}, trajectory: {} }),
    },
    ablationDetail: {
      type: Object,
      default: () => ({}),
    },
    loadingAblation: {
      type: Boolean,
      default: false,
    },
    metricPercent: {
      type: Function,
      required: true,
    },
    metricNumber: {
      type: Function,
      required: true,
    },
  },
  computed: {
    review() {
      return this.metrics && typeof this.metrics.review === 'object' ? this.metrics.review : {};
    },
    problemDetection() {
      if (this.metrics && typeof this.metrics.problem_detection === 'object' && Object.keys(this.metrics.problem_detection).length) {
        return this.metrics.problem_detection;
      }
      return this.review && typeof this.review.problem_detection === 'object' ? this.review.problem_detection : {};
    },
    retrieval() {
      return this.metrics && typeof this.metrics.retrieval === 'object' ? this.metrics.retrieval : {};
    },
    feedback() {
      return this.metrics && typeof this.metrics.feedback === 'object' ? this.metrics.feedback : {};
    },
    trajectory() {
      return this.metrics && typeof this.metrics.trajectory === 'object' ? this.metrics.trajectory : {};
    },
    reviewAccuracy() {
      return this.pickNumber(this.problemDetection.accuracy, this.review.issue_accuracy, this.review.accuracy);
    },
    reviewRecall() {
      return this.pickNumber(this.problemDetection.recall, this.review.issue_recall, this.review.recall, this.review.finding_recall);
    },
    reviewF1() {
      return this.pickNumber(this.problemDetection.f1, this.review.issue_f1, this.review.f1, this.review.finding_f1);
    },
    conclusionMatchRate() {
      return this.pickNumber(this.review.review_accuracy, this.review.conclusion_match_rate);
    },
    retrievalAccuracy() {
      return this.pickNumber(this.retrieval.accuracy, this.retrieval.approved_exact_match_rate);
    },
    retrievalRecall() {
      return this.pickNumber(this.retrieval.recall, this.retrieval.approved_recall);
    },
    retrievalF1() {
      return this.pickNumber(this.retrieval.f1, this.retrieval.approved_f1);
    },
    reviewCorrectIssueCount() {
      return this.pickNumber(this.problemDetection.correct_issue_count, this.review.correct_issue_count);
    },
    reviewFalsePositiveIssueCount() {
      return this.pickNumber(this.problemDetection.false_positive_issue_count, this.review.false_positive_issue_count);
    },
    reviewMissedIssueCount() {
      return this.pickNumber(this.problemDetection.missed_issue_count, this.review.missed_issue_count);
    },
    retrievalTruePositiveCount() {
      return this.pickNumber(this.retrieval.true_positive_count);
    },
    retrievalFalsePositiveCount() {
      return this.pickNumber(this.retrieval.false_positive_count);
    },
    retrievalFalseNegativeCount() {
      return this.pickNumber(this.retrieval.false_negative_count);
    },
    ablationSummary() {
      return this.ablationDetail && typeof this.ablationDetail.summary === 'object' ? this.ablationDetail.summary : {};
    },
    ablationVariants() {
      return Array.isArray(this.ablationDetail && this.ablationDetail.variants) ? this.ablationDetail.variants : [];
    },
    ranking() {
      return Array.isArray(this.ablationDetail && this.ablationDetail.ranking) ? this.ablationDetail.ranking : [];
    },
    hasAblation() {
      return !!(this.ablationVariants.length || this.ranking.length || Number(this.ablationSummary.variant_count || 0));
    },
    isP52Ablation() {
      return String((this.ablationDetail && this.ablationDetail.workflow_mode) || '').trim() === 'p52_ablation_v1';
    },
    variantCount() {
      return Number(this.ablationSummary.variant_count || this.ablationVariants.length || 0);
    },
    generatedCaseCount() {
      return Number(this.ablationDetail && this.ablationDetail.generated_case_count) || Number(this.ablationSummary.case_count || 0);
    },
    bestVariant() {
      const bestId = String(this.ablationSummary.best_variant_id || '').trim();
      return this.ablationVariants.find((item) => String(item.variant_id || '').trim() === bestId)
        || this.ablationVariants[0]
        || null;
    },
    bestVariantLabel() {
      const best = this.bestVariant;
      return best ? (best.label || best.variant_id || '-') : '-';
    },
    bestOverallScore() {
      const best = this.bestVariant;
      return this.pickNumber(best && best.metrics && best.metrics.overall_score, this.ablationSummary.best_overall_score);
    },
    bestOverallDelta() {
      const best = this.bestVariant;
      return this.pickNumber(best && best.delta_vs_baseline && best.delta_vs_baseline.overall_delta, 0);
    },
    topRanking() {
      return this.ranking.slice(0, 3);
    },
    p52CapabilityFocusText() {
      if (!this.isP52Ablation) {
        return '-';
      }
      return '5.1 映射 / 定向检索 / 规则过滤 / reviewer 综合判断';
    },
  },
  methods: {
    pickNumber(...values) {
      for (const value of values) {
        const num = Number(value);
        if (Number.isFinite(num)) {
          return num;
        }
      }
      return NaN;
    },
    deltaPercent(value) {
      const num = Number(value);
      if (!Number.isFinite(num)) {
        return '-';
      }
      const prefix = num > 0 ? '+' : '';
      return `${prefix}${(num * 100).toFixed(1)}%`;
    },
    p52FeatureFlagText(flags) {
      if (!flags || typeof flags !== 'object') {
        return '-';
      }
      return Object.keys(flags)
        .map((key) => `${key}:${flags[key] ? '开' : '关'}`)
        .join(' / ');
    },
  },
};
</script>

<style scoped>
.metrics-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.title,
.sub-title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.metric-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  line-height: 1.8;
  color: #475569;
}

.metric-row strong {
  color: #0f172a;
  font-weight: 600;
  text-align: right;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}

.metric-chip {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  color: #475569;
}

.ablation-box {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ranking-list {
  display: grid;
  gap: 8px;
}

.ranking-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
  font-size: 12px;
  color: #475569;
}

.p52-capability-list {
  display: grid;
  gap: 8px;
}

.p52-capability-card {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
  padding: 10px;
}

.muted {
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
}

.empty {
  font-size: 12px;
  line-height: 1.6;
  color: #94a3b8;
}
</style>
