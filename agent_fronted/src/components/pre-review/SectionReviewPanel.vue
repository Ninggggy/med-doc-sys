<template>
  <div v-if="review" class="block grow">
    <div class="title">当前章节审评结果</div>
    <div class="muted">
      {{ review.section_name || currentBrowseSectionLabel }}
      <span v-if="review.section_id">（{{ review.section_id }}）</span>
    </div>

    <div class="summary">
      <div><strong>辅助审评结论：</strong>{{ reviewConclusionLabel(review.pre_review_conclusion || review.conclusion) }}</div>
      <div v-if="review.section_summary"><strong>章节摘要：</strong>{{ review.section_summary }}</div>
      <div v-else-if="review.summary"><strong>章节摘要：</strong>{{ review.summary }}</div>
    </div>

    <div class="subtitle">审评规则对照</div>
    <div v-if="focusPointReviewRows.length">
      <div v-for="(item, index) in focusPointReviewRows" :key="'focus-review-' + index" class="issue-card">
        <div class="issue-title">{{ item.focus_point }}</div>
        <div class="muted"><strong>状态：</strong>{{ item.status }}</div>
        <div class="muted"><strong>说明：</strong>{{ item.reason }}</div>
      </div>
    </div>
    <div v-else class="empty">当前没有章节审评规则对照结果。</div>

    <div class="subtitle">事实依据</div>
    <div v-if="displayFactBasis.length">
      <div v-for="(item, index) in displayFactBasis" :key="'fact-' + index" class="issue-card">
        <div class="muted">{{ item }}</div>
      </div>
    </div>
    <div v-else class="empty">当前没有结构化事实依据。</div>

    <div class="subtitle">规则依据</div>
    <div v-if="displayLinkedRules.length">
      <div v-for="(item, index) in displayLinkedRules" :key="'rule-' + index" class="list-item">{{ index + 1 }}. {{ item }}</div>
    </div>
    <div v-else class="empty">当前没有结构化规则依据。</div>

    <div class="subtitle">已支持要点</div>
    <div v-if="normalizedSupportedPoints.length">
      <div v-for="(item, index) in normalizedSupportedPoints" :key="'supported-' + index" class="list-item">{{ index + 1 }}. {{ item }}</div>
    </div>
    <div v-else class="empty">当前没有已支持要点。</div>

    <div class="subtitle">不支持要点</div>
    <div v-if="normalizedUnsupportedPoints.length">
      <div v-for="(item, index) in normalizedUnsupportedPoints" :key="'unsupported-' + index" class="issue-card">
        <div class="issue-title">问题 {{ index + 1 }}</div>
        <div class="muted">{{ item }}</div>
      </div>
    </div>
    <div v-else class="empty">当前没有不支持要点。</div>

    <div class="subtitle">缺失要点</div>
    <div v-if="normalizedMissingPoints.length">
      <div v-for="(item, index) in normalizedMissingPoints" :key="'missing-' + index" class="list-item">{{ index + 1 }}. {{ item }}</div>
    </div>
    <div v-else class="empty">当前没有缺失要点。</div>

    <div class="subtitle">风险提示</div>
    <div v-if="normalizedRiskPoints.length">
      <div v-for="(item, index) in normalizedRiskPoints" :key="'risk-' + index" class="list-item">{{ index + 1 }}. {{ item }}</div>
    </div>
    <div v-else class="empty">当前没有风险提示。</div>

    <div class="subtitle">问题清单</div>
    <div v-if="normalizedQuestions.length">
      <div v-for="(item, index) in normalizedQuestions" :key="'question-' + index" class="issue-card">
        <div class="issue-title">{{ item.issue || `问题 ${index + 1}` }}</div>
        <div class="muted"><strong>依据：</strong>{{ item.basis || "-" }}</div>
        <div class="muted"><strong>建议动作：</strong>{{ item.requested_action || "-" }}</div>
      </div>
    </div>
    <div v-else class="empty">当前没有问题清单。</div>

    <div class="subtitle">证据引用</div>
    <div v-if="normalizedEvidenceRefs.length">
      <div v-for="(item, index) in normalizedEvidenceRefs" :key="'evidence-ref-' + index" class="list-item">{{ index + 1 }}. {{ item }}</div>
    </div>
    <div v-else class="empty">当前没有结构化证据引用。</div>

    <div class="subtitle">检索证据</div>
    <div v-if="selectedEvidenceGroups.length">
      <div v-for="group in selectedEvidenceGroups" :key="group.source" class="nested-block">
        <div class="issue-title">{{ group.source }}</div>
        <div v-for="(item, index) in group.items" :key="group.source + '-' + index" class="issue-card">
          <div class="muted"><strong>资料名称：</strong>{{ item.file_name || item.title || "-" }}</div>
          <div class="muted"><strong>标题：</strong>{{ item.title || "-" }}</div>
          <div class="muted"><strong>章节：</strong>{{ item.section_path_text || item.section_name || "-" }}</div>
          <div class="muted">
            <strong>分数：</strong>{{ formatScore(item.score) }}
            <span v-if="item.vector_score != null || item.lexical_score != null">
              （向量 {{ formatScore(item.vector_score) }} / 词法 {{ formatScore(item.lexical_score) }}）
            </span>
          </div>
          <el-tooltip effect="dark" placement="top-start">
            <div slot="content" class="tooltip-content">{{ item.content || item.doc_summary || "-" }}</div>
            <div class="chunk-preview">{{ previewText(item.content || item.doc_summary || "-") }}</div>
          </el-tooltip>
          <div class="feedback-row">
            <span class="muted strong">证据反馈</span>
            <el-select
              :value="evidenceFeedbackValue(item.evidence_id)"
              size="mini"
              style="width: 160px"
              @change="updateEvidenceFeedback(item.evidence_id, $event)"
            >
              <el-option label="未判断" value="" />
              <el-option label="正确" value="correct" />
              <el-option label="错误" value="incorrect" />
              <el-option label="部分正确" value="partial" />
            </el-select>
          </div>
        </div>
      </div>
    </div>
    <div v-else class="empty">当前没有检索证据。</div>

    <div class="subtitle">当前运行命中的 Prompt 规则</div>
    <div v-if="hasSelectedPromptRules">
      <div v-if="selectedPlannerRules.length" class="nested-block">
        <div class="issue-title">Planner</div>
        <div v-for="(item, index) in selectedPlannerRules" :key="'planner-rule-' + index" class="issue-card">
          <div class="muted"><strong>规则：</strong>{{ item.rule_name || item.rule_code || "-" }}</div>
          <div class="muted"><strong>模板：</strong>{{ item.template_name || "-" }}</div>
          <div class="muted"><strong>优先级：</strong>{{ item.priority != null ? item.priority : "-" }}</div>
          <div class="muted"><strong>作用域：</strong>{{ item.scope_type || "-" }}</div>
          <div class="muted"><strong>来源：</strong>{{ item.source_type || "-" }}</div>
        </div>
      </div>
      <div v-if="selectedRetrievalEvaluatorRules.length" class="nested-block">
        <div class="issue-title">Retrieval Evaluator</div>
        <div v-for="(item, index) in selectedRetrievalEvaluatorRules" :key="'retrieval-rule-' + index" class="issue-card">
          <div class="muted"><strong>规则：</strong>{{ item.rule_name || item.rule_code || "-" }}</div>
          <div class="muted"><strong>模板：</strong>{{ item.template_name || "-" }}</div>
          <div class="muted"><strong>优先级：</strong>{{ item.priority != null ? item.priority : "-" }}</div>
          <div class="muted"><strong>作用域：</strong>{{ item.scope_type || "-" }}</div>
          <div class="muted"><strong>来源：</strong>{{ item.source_type || "-" }}</div>
        </div>
      </div>
      <div v-if="selectedReviewerRules.length" class="nested-block">
        <div class="issue-title">Reviewer</div>
        <div v-for="(item, index) in selectedReviewerRules" :key="'reviewer-rule-' + index" class="issue-card">
          <div class="muted"><strong>规则：</strong>{{ item.rule_name || item.rule_code || "-" }}</div>
          <div class="muted"><strong>模板：</strong>{{ item.template_name || "-" }}</div>
          <div class="muted"><strong>优先级：</strong>{{ item.priority != null ? item.priority : "-" }}</div>
          <div class="muted"><strong>作用域：</strong>{{ item.scope_type || "-" }}</div>
          <div class="muted"><strong>来源：</strong>{{ item.source_type || "-" }}</div>
        </div>
      </div>
    </div>
    <div v-else class="empty">当前运行没有命中的 Prompt 规则。</div>

    <div class="subtitle">反馈输入</div>
    <div class="feedback-grid">
      <div>
        <div class="muted strong">结论反馈</div>
        <el-select v-model="feedbackForm.conclusion_feedback" size="small" style="width: 180px">
          <el-option label="正确" value="correct" />
          <el-option label="错误" value="incorrect" />
          <el-option label="部分正确" value="partial" />
        </el-select>
      </div>
      <div>
        <div class="muted strong">检索反馈</div>
        <el-select v-model="feedbackForm.retrieval_feedback" size="small" style="width: 180px">
          <el-option label="未判断" value="unknown" />
          <el-option label="正确" value="correct" />
          <el-option label="错误" value="incorrect" />
          <el-option label="部分正确" value="partial" />
        </el-select>
      </div>
      <div>
        <div class="muted strong">反馈类型</div>
        <el-select v-model="feedbackForm.decision" size="small" style="width: 180px">
          <el-option label="正确" value="valid" />
          <el-option label="误报" value="false_positive" />
          <el-option label="漏报" value="missed" />
        </el-select>
      </div>
    </div>

    <div style="margin-top: 12px">
      <div class="muted" style="margin-bottom: 6px;">
        当前反馈回路模式：{{ feedbackLoopMode === "feedback_only" ? "仅记录反馈" : "进入优化闭环" }}
      </div>
      <el-switch
        v-model="feedbackForm.enableOptimize"
        active-text="进入反馈优化"
        inactive-text="仅记录反馈"
        :disabled="feedbackLoopLocked"
      />
    </div>

    <el-input
      v-model="feedbackForm.feedback_text"
      type="textarea"
      :rows="4"
      placeholder="输入针对当前章节结论、证据或规则使用的反馈意见。"
      style="margin-top: 12px"
    />

    <div class="actions" style="margin-top: 12px">
      <el-button
        size="mini"
        type="primary"
        :disabled="!selectedRun || !review"
        :loading="submittingFeedback"
        @click="$emit('submit-feedback')"
      >
        {{ feedbackForm.enableOptimize ? "提交反馈优化" : "保存反馈" }}
      </el-button>
    </div>

    <div
      v-if="feedbackForm.enableOptimize && (feedbackOptimizeResult.feedback_key || feedbackOptimizeResult.feedback_optimize_status)"
      class="block nested-block"
    >
      <div class="title">反馈优化结果</div>
      <div class="muted">feedback_key: {{ feedbackOptimizeResult.feedback_key || "-" }}</div>
      <div class="muted"><strong>优化状态：</strong>{{ feedbackOptimizeResult.feedback_optimize_status || "-" }}</div>
      <div class="muted"><strong>候选注册：</strong>{{ feedbackOptimizeResult.candidate_register_status || "-" }}</div>
      <div class="muted"><strong>回放状态：</strong>{{ feedbackOptimizeResult.replay_status || "-" }}</div>
      <div v-if="feedbackOptimizeResult.error_message" class="muted"><strong>错误：</strong>{{ feedbackOptimizeResult.error_message }}</div>

      <div class="subtitle">分析结果</div>
      <pre class="json-view">{{ formattedFeedbackAnalysis }}</pre>

      <div class="subtitle">当前 Patch</div>
      <div v-if="currentPatchCandidates.length">
        <div v-for="(patch, index) in currentPatchCandidates" :key="'current-patch-' + index" class="issue-card">
          <div class="issue-title">{{ patch.patch_type || `patch_${index + 1}` }}</div>
          <div class="muted"><strong>目标 Agent：</strong>{{ patch.target_agent || "-" }}</div>
          <div class="muted"><strong>触发条件：</strong>{{ patch.trigger_condition || "-" }}</div>
          <div class="muted"><strong>Patch 内容：</strong>{{ patch.patch_content || "-" }}</div>
        </div>
      </div>
      <pre class="json-view">{{ formattedFeedbackPatch }}</pre>
    </div>

    <div class="block nested-block">
      <div class="title">反馈历史</div>
      <div class="muted">
        反馈 {{ feedbackHistorySummary.feedback_total }} 条
        优化 {{ feedbackHistorySummary.optimize_event_total }} 条
        Patch {{ feedbackHistorySummary.patch_total }} 条
        Accepted {{ feedbackHistorySummary.accepted_patch_total }} 条
      </div>

      <div class="subtitle">反馈记录</div>
      <div v-if="feedbackHistoryEntries.length">
        <div v-for="item in feedbackHistoryEntries" :key="'feedback-history-' + item.id" class="issue-card">
          <div class="issue-title">{{ item.feedback_type || "feedback" }}</div>
          <div class="muted"><strong>时间：</strong>{{ item.create_time || "-" }}</div>
          <div class="muted"><strong>操作人：</strong>{{ item.operator || "-" }}</div>
          <div class="muted"><strong>反馈内容：</strong>{{ item.feedback_text || item.suggestion || "-" }}</div>
        </div>
      </div>
      <div v-else class="empty">当前章节还没有反馈记录。</div>

      <div class="subtitle">优化记录</div>
      <div v-if="feedbackOptimizeEvents.length">
        <div v-for="(item, index) in feedbackOptimizeEvents" :key="'optimize-history-' + index" class="issue-card">
          <div class="issue-title">{{ item.feedback_key || `optimize_${index + 1}` }}</div>
          <div class="muted"><strong>模式：</strong>{{ item.chain_mode || "-" }}</div>
          <div class="muted"><strong>反馈类型：</strong>{{ item.decision || item.feedback_type || "-" }}</div>
          <div class="muted"><strong>优化状态：</strong>{{ item.feedback_optimize_status || "-" }}</div>
          <div class="muted"><strong>候选注册：</strong>{{ item.candidate_register_status || "-" }}</div>
          <div class="muted"><strong>回放状态：</strong>{{ item.replay_status || "-" }}</div>
          <div v-if="item.error_message" class="muted"><strong>错误：</strong>{{ item.error_message }}</div>
        </div>
      </div>
      <div v-else class="empty">当前章节还没有反馈优化记录。</div>
    </div>

    <div class="block nested-block">
      <div class="title">章节历史 Patch</div>
      <div v-if="historicalPatchCandidates.length">
        <div v-for="(patch, index) in historicalPatchCandidates" :key="patch.patch_id || 'history-patch-' + index" class="issue-card">
          <div class="issue-title">{{ patch.patch_type || `patch_${index + 1}` }}</div>
          <div class="muted"><strong>Run：</strong>{{ patch.run_id || "-" }}</div>
          <div class="muted"><strong>目标 Agent：</strong>{{ patch.target_agent || "-" }}</div>
          <div class="muted"><strong>状态 / 版本：</strong>{{ patch.status || "-" }} / {{ patch.version || "-" }}</div>
          <div class="muted"><strong>触发条件：</strong>{{ patch.trigger_condition || "-" }}</div>
          <div class="muted"><strong>Patch 内容：</strong>{{ patch.patch_content || "-" }}</div>
          <div class="muted"><strong>更新时间：</strong>{{ patch.update_time || patch.create_time || "-" }}</div>
        </div>
      </div>
      <div v-else class="empty">当前章节还没有历史 Patch。</div>
    </div>
  </div>
</template>

<script>
export default {
  name: "SectionReviewPanel",
  props: {
    review: { type: Object, default: null },
    currentBrowseSectionLabel: { type: String, default: "" },
    normalizedSupportedPoints: { type: Array, default: () => [] },
    normalizedUnsupportedPoints: { type: Array, default: () => [] },
    normalizedMissingPoints: { type: Array, default: () => [] },
    normalizedRiskPoints: { type: Array, default: () => [] },
    normalizedQuestions: { type: Array, default: () => [] },
    normalizedEvidenceRefs: { type: Array, default: () => [] },
    selectedEvidenceGroups: { type: Array, default: () => [] },
    selectedPromptRules: {
      type: Object,
      default: () => ({ planner: [], retrieval_evaluator: [], reviewer: [] }),
    },
    feedbackForm: { type: Object, required: true },
    selectedRun: { type: Object, default: null },
    submittingFeedback: { type: Boolean, default: false },
    feedbackOptimizeResult: {
      type: Object,
      default: () => ({
        feedback_key: "",
        analysis_result: null,
        patch_result: null,
        feedback_optimize_status: "",
        candidate_register_status: "",
        replay_status: "",
        error_message: "",
      }),
    },
    currentPatchCandidates: { type: Array, default: () => [] },
    historicalPatchCandidates: { type: Array, default: () => [] },
    formattedFeedbackAnalysis: { type: String, default: "{}" },
    formattedFeedbackPatch: { type: String, default: "{}" },
    feedbackHistory: {
      type: Object,
      default: () => ({
        feedback_entries: [],
        optimize_events: [],
        patch_entries: [],
        summary: {},
      }),
    },
    reviewConclusionLabel: { type: Function, required: true },
    feedbackLoopMode: { type: String, default: "feedback_optimize" },
    feedbackLoopLocked: { type: Boolean, default: false },
  },
  computed: {
    displayFocusPoints() {
      const review = this.review || {};
      const raw = Array.isArray(review.focus_points)
        ? review.focus_points
        : Array.isArray(review.concern_points)
          ? review.concern_points
          : [];
      return raw.map((item) => String(item || "").trim()).filter(Boolean);
    },
    displayFactBasis() {
      const review = this.review || {};
      const basis = review.fact_basis && typeof review.fact_basis === "object" ? review.fact_basis : {};
      const explicit = Array.isArray(basis.explicit_in_text) ? basis.explicit_in_text : [];
      const inferred = Array.isArray(basis.inferred_from_evidence || basis.supported_by_evidence)
        ? (basis.inferred_from_evidence || basis.supported_by_evidence)
        : [];
      const unsupported = Array.isArray(basis.not_stated_or_uncertain) ? basis.not_stated_or_uncertain : [];
      return []
        .concat(explicit, inferred, unsupported)
        .map((item) => String(item || "").trim())
        .filter(Boolean);
    },
    displayLinkedRules() {
      const review = this.review || {};
      const raw = Array.isArray(review.linked_rules) ? review.linked_rules : [];
      return raw.map((item) => String(item || "").trim()).filter(Boolean);
    },
    focusPointReviewRows() {
      const positives = this.normalizedSupportedPoints || [];
      const negatives = []
        .concat(this.normalizedUnsupportedPoints || [])
        .concat(this.normalizedMissingPoints || [])
        .concat(this.normalizedRiskPoints || []);
      return this.displayFocusPoints.map((focusPoint) => {
        const negative = negatives.find((item) => String(item || "").includes(focusPoint));
        if (negative) {
          return { focus_point: focusPoint, status: "未满足 / 待补充", reason: negative };
        }
        const positive = positives.find((item) => String(item || "").includes(focusPoint));
        if (positive) {
          return { focus_point: focusPoint, status: "已支持", reason: positive };
        }
        return {
          focus_point: focusPoint,
          status: this.reviewConclusionLabel((this.review && this.review.pre_review_conclusion) || "-"),
          reason: (this.review && this.review.section_summary) || "当前没有直接命中的结构化结论说明。",
        };
      });
    },
    feedbackHistoryEntries() {
      return Array.isArray(this.feedbackHistory && this.feedbackHistory.feedback_entries)
        ? this.feedbackHistory.feedback_entries
        : [];
    },
    feedbackOptimizeEvents() {
      return Array.isArray(this.feedbackHistory && this.feedbackHistory.optimize_events)
        ? this.feedbackHistory.optimize_events
        : [];
    },
    feedbackHistorySummary() {
      const summary = this.feedbackHistory && typeof this.feedbackHistory.summary === "object"
        ? this.feedbackHistory.summary
        : {};
      return {
        feedback_total: Number(summary.feedback_total || 0),
        optimize_event_total: Number(summary.optimize_event_total || 0),
        patch_total: Number(summary.patch_total || 0),
        accepted_patch_total: Number(summary.accepted_patch_total || 0),
      };
    },
    selectedPlannerRules() {
      return Array.isArray(this.selectedPromptRules && this.selectedPromptRules.planner)
        ? this.selectedPromptRules.planner
        : [];
    },
    selectedRetrievalEvaluatorRules() {
      return Array.isArray(this.selectedPromptRules && this.selectedPromptRules.retrieval_evaluator)
        ? this.selectedPromptRules.retrieval_evaluator
        : [];
    },
    selectedReviewerRules() {
      return Array.isArray(this.selectedPromptRules && this.selectedPromptRules.reviewer)
        ? this.selectedPromptRules.reviewer
        : [];
    },
    hasSelectedPromptRules() {
      return Boolean(
        this.selectedPlannerRules.length ||
        this.selectedRetrievalEvaluatorRules.length ||
        this.selectedReviewerRules.length
      );
    },
  },
  methods: {
    evidenceFeedbackValue(evidenceId) {
      const store = this.feedbackForm && this.feedbackForm.evidence_feedback_map && typeof this.feedbackForm.evidence_feedback_map === "object"
        ? this.feedbackForm.evidence_feedback_map
        : {};
      return store[evidenceId] || "";
    },
    updateEvidenceFeedback(evidenceId, value) {
      if (!this.feedbackForm || typeof this.feedbackForm !== "object") {
        return;
      }
      if (!this.feedbackForm.evidence_feedback_map || typeof this.feedbackForm.evidence_feedback_map !== "object") {
        this.$set(this.feedbackForm, "evidence_feedback_map", {});
      }
      this.$set(this.feedbackForm.evidence_feedback_map, evidenceId, value || "");
    },
    previewText(value) {
      const text = String(value || "").trim();
      return text.length > 160 ? `${text.slice(0, 160)}...` : text || "-";
    },
    formatScore(value) {
      const num = Number(value);
      return Number.isFinite(num) ? num.toFixed(4) : "-";
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

.grow {
  flex: 1;
  overflow: auto;
}

.nested-block {
  margin-top: 16px;
  background: #fafcff;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: #2d3648;
}

.subtitle {
  margin: 12px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: #2d3648;
}

.summary {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #4b5565;
  margin-top: 8px;
}

.list-item,
.muted,
.empty {
  color: #4b5565;
  font-size: 12px;
  line-height: 1.6;
}

.strong {
  font-weight: 600;
}

.issue-card {
  margin-top: 8px;
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  padding: 10px;
}

.issue-title {
  font-weight: 600;
  color: #2d3648;
  margin-bottom: 6px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.feedback-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.feedback-row {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.chunk-preview {
  margin-top: 6px;
  padding: 8px;
  border-radius: 6px;
  background: #f7f9fc;
  color: #4b5565;
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
}

.tooltip-content {
  max-width: 480px;
  white-space: pre-wrap;
  line-height: 1.6;
}

.json-view {
  margin: 0;
  padding: 10px;
  border: 1px solid #eef1f6;
  border-radius: 6px;
  background: #f7f9fc;
  color: #2d3648;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
