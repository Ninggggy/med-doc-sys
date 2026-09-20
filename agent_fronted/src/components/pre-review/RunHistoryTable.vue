<template>
  <div>
    <div class="toolbar">
      <span class="label">反馈模式</span>
      <el-radio-group v-model="feedbackLoopFilter" size="mini">
        <el-radio-button label="all">全部</el-radio-button>
        <el-radio-button label="feedback_optimize">反馈优化</el-radio-button>
        <el-radio-button label="feedback_only">仅记录反馈</el-radio-button>
      </el-radio-group>

      <span class="label">章节范围</span>
      <el-radio-group v-model="sectionFilterMode" size="mini">
        <el-radio-button label="current">当前章节</el-radio-button>
        <el-radio-button label="all">全部章节</el-radio-button>
      </el-radio-group>
    </div>

    <el-table
      v-if="filteredRuns.length"
      :data="filteredRuns"
      size="mini"
      border
      height="220"
      highlight-current-row
      @current-change="$emit('select-run', $event)"
    >
      <el-table-column prop="version_no" label="版本" width="64" />
      <el-table-column prop="create_time" label="创建时间" min-width="132" />
      <el-table-column label="当前章节结论" min-width="120">
        <template slot-scope="{ row }">{{ currentSectionDigest(row).conclusion || '-' }}</template>
      </el-table-column>
      <el-table-column label="当前章节风险" width="100">
        <template slot-scope="{ row }">{{ currentSectionDigest(row).risk_level || '-' }}</template>
      </el-table-column>
      <el-table-column label="当前章节反馈" width="96">
        <template slot-scope="{ row }">{{ currentSectionDigest(row).has_feedback ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="反馈模式" width="100">
        <template slot-scope="{ row }">{{ feedbackLoopLabel(row.feedback_loop_mode) }}</template>
      </el-table-column>

      <el-table-column label="问题 A" width="84">
        <template slot-scope="{ row }">{{ metricPercent(reviewAccuracy(row)) }}</template>
      </el-table-column>
      <el-table-column label="问题 R" width="84">
        <template slot-scope="{ row }">{{ metricPercent(reviewRecall(row)) }}</template>
      </el-table-column>
      <el-table-column label="问题 F1" width="84">
        <template slot-scope="{ row }">{{ metricPercent(reviewF1(row)) }}</template>
      </el-table-column>

      <el-table-column label="检索 A" width="84">
        <template slot-scope="{ row }">{{ metricPercent(retrievalAccuracy(row)) }}</template>
      </el-table-column>
      <el-table-column label="检索 R" width="84">
        <template slot-scope="{ row }">{{ metricPercent(retrievalRecall(row)) }}</template>
      </el-table-column>
      <el-table-column label="检索 F1" width="84">
        <template slot-scope="{ row }">{{ metricPercent(retrievalF1(row)) }}</template>
      </el-table-column>

      <el-table-column label="反馈采纳率" width="96">
        <template slot-scope="{ row }">{{ metricPercent((((row.metrics || {}).feedback || {}).feedback_acceptance_rate)) }}</template>
      </el-table-column>
      <el-table-column label="人工修改率" width="96">
        <template slot-scope="{ row }">{{ metricPercent((((row.metrics || {}).feedback || {}).manual_modification_rate)) }}</template>
      </el-table-column>
      <el-table-column label="误报下降率" width="96">
        <template slot-scope="{ row }">{{ metricPercent((((row.metrics || {}).trajectory || {}).false_positive_reduction_rate)) }}</template>
      </el-table-column>
    </el-table>

    <div v-else class="empty">当前没有符合筛选条件的运行记录。</div>
  </div>
</template>

<script>
export default {
  name: 'RunHistoryTable',
  props: {
    runs: {
      type: Array,
      default: () => [],
    },
    activeSectionId: {
      type: String,
      default: '',
    },
    metricPercent: {
      type: Function,
      required: true,
    },
  },
  data() {
    return {
      feedbackLoopFilter: 'all',
      sectionFilterMode: 'current',
    };
  },
  computed: {
    filteredRuns() {
      let result = Array.isArray(this.runs) ? [...this.runs] : [];
      const activeSectionId = String(this.activeSectionId || '').trim();
      if (this.sectionFilterMode === 'current' && activeSectionId) {
        result = result.filter((row) => {
          const reviewedSectionIds = Array.isArray(row && row.reviewed_section_ids) ? row.reviewed_section_ids : [];
          return reviewedSectionIds.includes(activeSectionId);
        });
      }
      if (this.feedbackLoopFilter === 'all') {
        return result;
      }
      return result.filter((row) => String((row && row.feedback_loop_mode) || 'feedback_optimize') === this.feedbackLoopFilter);
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
    reviewAccuracy(row) {
      const review = ((row || {}).metrics || {}).review || {};
      const problemDetection = (((row || {}).metrics || {}).problem_detection) || review.problem_detection || {};
      return this.pickNumber(problemDetection.accuracy, review.issue_accuracy, review.accuracy);
    },
    reviewRecall(row) {
      const review = ((row || {}).metrics || {}).review || {};
      const problemDetection = (((row || {}).metrics || {}).problem_detection) || review.problem_detection || {};
      return this.pickNumber(problemDetection.recall, review.issue_recall, review.recall, review.finding_recall);
    },
    reviewF1(row) {
      const review = ((row || {}).metrics || {}).review || {};
      const problemDetection = (((row || {}).metrics || {}).problem_detection) || review.problem_detection || {};
      return this.pickNumber(problemDetection.f1, review.issue_f1, review.f1, review.finding_f1);
    },
    retrievalAccuracy(row) {
      const retrieval = ((row || {}).metrics || {}).retrieval || {};
      return this.pickNumber(retrieval.accuracy, retrieval.approved_exact_match_rate);
    },
    retrievalRecall(row) {
      const retrieval = ((row || {}).metrics || {}).retrieval || {};
      return this.pickNumber(retrieval.recall, retrieval.approved_recall);
    },
    retrievalF1(row) {
      const retrieval = ((row || {}).metrics || {}).retrieval || {};
      return this.pickNumber(retrieval.f1, retrieval.approved_f1);
    },
    feedbackLoopLabel(value) {
      return String(value || 'feedback_optimize') === 'feedback_only' ? '仅记录反馈' : '反馈优化';
    },
    currentSectionDigest(row) {
      const activeSectionId = String(this.activeSectionId || '').trim();
      const digestMap = row && typeof row.section_result_digest === 'object' ? row.section_result_digest : {};
      if (activeSectionId && digestMap[activeSectionId]) {
        return digestMap[activeSectionId];
      }
      if (activeSectionId) {
        return { conclusion: '', risk_level: '', has_feedback: false };
      }
      const firstKey = Object.keys(digestMap)[0];
      return firstKey ? digestMap[firstKey] || {} : {};
    },
  },
};
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.label {
  color: #5a667a;
  font-size: 12px;
}

.empty {
  color: #8a94a6;
  font-size: 12px;
  line-height: 1.6;
}
</style>
