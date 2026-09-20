<template>
  <div class="block">
    <div class="pane-head">
      <div>
        <div class="title">审评结论</div>
        <div class="muted">{{ currentBrowseSectionLabel || "-" }}</div>
      </div>
      <el-button size="mini" type="primary" :disabled="!canOpenDetail" @click="$emit('open-detail')">
        查看详细结果
      </el-button>
    </div>

    <div v-if="review" class="content">
      <div class="summary-banner">
        <div class="summary-main">
          <div class="summary-status-row">
            <el-tag size="mini" :type="conclusionTagType">{{ conclusionText }}</el-tag>
          </div>
          <div class="summary-text">{{ coreConclusionText }}</div>
        </div>
        <div class="summary-aside">
          <div class="summary-aside-label">模型执行</div>
          <div class="summary-aside-value">{{ llmExecutionLabel }}</div>
        </div>
      </div>

      <div v-if="chapterRoleText || coreReviewQuestionText" class="profile-card">
        <div v-if="chapterRoleText" class="profile-line">
          <strong>本章作用：</strong>{{ chapterRoleText }}
        </div>
        <div v-if="coreReviewQuestionText" class="profile-line">
          <strong>核心审评问题：</strong>{{ coreReviewQuestionText }}
        </div>
        <div v-if="mustAnswerPreview" class="profile-line">
          <strong>必须回答的问题：</strong>{{ mustAnswerPreview }}
        </div>
        <div v-if="mustAnswerCoverageSummary" class="profile-line">
          <strong>必答问题覆盖：</strong>{{ mustAnswerCoverageSummary }}
        </div>
        <div v-if="topPendingMustAnswerText" class="profile-line">
          <strong>当前待补强：</strong>{{ topPendingMustAnswerText }}
        </div>
      </div>

      <div class="fact-grid">
        <div class="fact-card">
          <div class="fact-label">关键规则</div>
          <div class="fact-value">{{ primaryRuleText || "当前没有提取到可直接展示的关键规则。" }}</div>
        </div>
        <div class="fact-card">
          <div class="fact-label">关键事实</div>
          <div class="fact-value">{{ primaryBasisText || "当前没有提取到可直接展示的关键事实。" }}</div>
        </div>
      </div>

      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-label">判断项</div>
          <div class="stat-value">{{ judgmentOverview.total }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">已支持</div>
          <div class="stat-value">{{ judgmentOverview.supported }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">待补充/未通过</div>
          <div class="stat-value">{{ judgmentOverview.risky }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">实体与数据点</div>
          <div class="stat-value">{{ extractedFactCount }}</div>
        </div>
      </div>

      <div v-if="riskyTaskVerdictItems.length" class="focus-section">
        <div class="sub-title">当前优先关注</div>
        <div
          v-for="(item, index) in riskyTaskVerdictItems.slice(0, 3)"
          :key="issueKey(item, index)"
          class="judgment-card"
        >
          <div class="judgment-head">
            <div class="judgment-title">{{ item.problem || item.task_question || `判断项 ${index + 1}` }}</div>
            <el-tag size="mini" type="warning">{{ judgmentStatusText(item.status) }}</el-tag>
          </div>
          <div class="judgment-row"><strong>规则：</strong>{{ item.basis || item.rule_requirement || "-" }}</div>
          <div class="judgment-row"><strong>事实：</strong>{{ item.material_fact || "-" }}</div>
          <div class="judgment-row"><strong>判断理由：</strong>{{ item.reason || item.judgment_reason || "-" }}</div>
        </div>
      </div>

      <div v-else-if="supportedTaskVerdictItems.length" class="focus-section">
        <div class="sub-title">当前已形成的直接支撑</div>
        <div class="judgment-card supported-card">
          <div class="judgment-head">
            <div class="judgment-title">{{ supportedTaskVerdictItems[0].problem || supportedTaskVerdictItems[0].task_question || "-" }}</div>
            <el-tag size="mini" type="success">{{ judgmentStatusText(supportedTaskVerdictItems[0].status) }}</el-tag>
          </div>
          <div class="judgment-row"><strong>规则：</strong>{{ supportedTaskVerdictItems[0].basis || supportedTaskVerdictItems[0].rule_requirement || "-" }}</div>
          <div class="judgment-row"><strong>事实：</strong>{{ supportedTaskVerdictItems[0].material_fact || "-" }}</div>
          <div class="judgment-row"><strong>判断理由：</strong>{{ supportedTaskVerdictItems[0].reason || supportedTaskVerdictItems[0].judgment_reason || "-" }}</div>
        </div>
      </div>

      <div class="hint">
        这里展示的是当前章节的摘要性审评判断。逐项规则、证据、推理过程和反馈入口请进入“查看详细结果”。
      </div>
    </div>

    <div v-else class="empty">当前章节还没有可展示的审评结果。</div>
  </div>
</template>

<script>

export default {
  name: "PreReviewSessionReviewPanel",
  props: {
    review: { type: Object, default: null },
    currentBrowseSectionLabel: { type: String, default: "" },
    reviewConclusionLabel: { type: Function, required: true },
    feedbackForm: { type: Object, required: true },
    approvedEvidenceGroups: { type: Array, default: () => [] },
    submittingFeedback: { type: Boolean, default: false },
    feedbackLoopMode: { type: String, default: "feedback_optimize" },
    canOpenDetail: { type: Boolean, default: false },
  },
  computed: {
    conclusionText() {
      const value = this.review && (this.review.pre_review_conclusion || this.review.conclusion);
      return this.reviewConclusionLabel(value || "-");
    },
    conclusionTagType() {
      const value = String((this.review && (this.review.pre_review_conclusion || this.review.conclusion)) || "").trim().toLowerCase();
      if (value === "supported") {
        return "success";
      }
      if (value === "unsupported") {
        return "danger";
      }
      if (value === "insufficient_information") {
        return "warning";
      }
      return "info";
    },
    llmExecution() {
      return this.review && typeof this.review.llm_execution === "object" ? this.review.llm_execution : {};
    },
    llmExecutionLabel() {
      if (this.llmExecution.used_default_fallback) {
        return "默认回退";
      }
      if (this.llmExecution.used_model_output) {
        return "大模型输出";
      }
      return "-";
    },
    taskVerdictItems() {
      const verdictRows = Array.isArray(this.review && this.review.task_verdicts) ? this.review.task_verdicts : [];
      const reasoningRows = Array.isArray(this.review && this.review.reasoning_chain_items) ? this.review.reasoning_chain_items : [];
      const reasoningMap = {};
      reasoningRows.forEach((item) => {
        const key = String((item && item.task_code) || "").trim();
        if (key) {
          reasoningMap[key] = item;
        }
      });
      return verdictRows
        .filter((item) => item && typeof item === "object")
        .map((item) => {
          const taskCode = String(item.task_code || "").trim();
          const reasoning = reasoningMap[taskCode] || {};
          return {
            task_code: taskCode,
            status: String(item.status || "").trim().toLowerCase(),
            task_question: String(item.task_question || "").trim(),
            problem: String(item.problem || item.issue || "").trim(),
            basis: String(item.basis || "").trim(),
            reason: String(item.reason || "").trim(),
            rule_requirement: String(reasoning.rule_requirement || "").trim(),
            material_fact: String(reasoning.material_fact || "").trim(),
            judgment_reason: String(reasoning.judgment_reason || "").trim(),
          };
        })
        .filter((item) => item.task_question || item.problem || item.basis || item.reason || item.material_fact);
    },
    judgmentOverview() {
      return {
        total: this.taskVerdictItems.length,
        supported: this.taskVerdictItems.filter((item) => item.status === "supported").length,
        risky: this.taskVerdictItems.filter((item) => item.status && item.status !== "supported").length,
      };
    },
    riskyTaskVerdictItems() {
      return this.taskVerdictItems.filter((item) => item.status && item.status !== "supported");
    },
    supportedTaskVerdictItems() {
      return this.taskVerdictItems.filter((item) => item.status === "supported");
    },
    primaryJudgmentItem() {
      if (this.riskyTaskVerdictItems.length) {
        return this.riskyTaskVerdictItems[0];
      }
      if (this.supportedTaskVerdictItems.length) {
        return this.supportedTaskVerdictItems[0];
      }
      return null;
    },
    coreConclusionText() {
      if (this.reviewerOutline.summary) {
        const suffix = this.topPendingMustAnswerText ? ` 当前仍需重点补强：${this.topPendingMustAnswerText}` : "";
        return `${this.reviewerOutline.summary}${suffix}`;
      }
      const primary = this.primaryJudgmentItem;
      if (primary) {
        const focus = primary.problem || primary.task_question || "-";
        if (this.judgmentOverview.risky > 0) {
          return `当前共识别 ${this.judgmentOverview.total} 个判断项，其中 ${this.judgmentOverview.risky} 个仍需补充论证或证据支撑，当前优先关注“${focus}”。`;
        }
        return `当前共识别 ${this.judgmentOverview.total} 个判断项，已形成对本章核心问题的直接支撑，暂无明确不符合项。`;
      }
      return String((this.review && (this.review.section_summary || this.review.summary)) || "").trim() || "当前没有可展示的核心判断。";
    },
    primaryRuleText() {
      if (this.primaryJudgmentItem && (this.primaryJudgmentItem.basis || this.primaryJudgmentItem.rule_requirement)) {
        return this.primaryJudgmentItem.basis || this.primaryJudgmentItem.rule_requirement;
      }
      const rules = Array.isArray(this.review && this.review.linked_rules) ? this.review.linked_rules : [];
      return rules.slice(0, 2).join("；");
    },
    primaryBasisText() {
      if (this.primaryJudgmentItem && this.primaryJudgmentItem.material_fact) {
        return this.primaryJudgmentItem.material_fact;
      }
      const factBasis = this.review && typeof this.review.fact_basis === "object" ? this.review.fact_basis : {};
      const facts = Array.isArray(factBasis.explicit_in_text) ? factBasis.explicit_in_text : [];
      return facts.slice(0, 2).join("；");
    },
    taskDefinition() {
      return this.review && typeof this.review.task_definition === "object" ? this.review.task_definition : {};
    },
    reviewerOutline() {
      return this.review && typeof this.review.reviewer_outline === "object" ? this.review.reviewer_outline : {};
    },
    sectionReviewProfile() {
      return this.review && typeof this.review.section_review_profile === "object" ? this.review.section_review_profile : {};
    },
    chapterRoleText() {
      return String(this.reviewerOutline.chapter_role || this.sectionReviewProfile.chapter_role || this.taskDefinition.chapter_role || "").trim();
    },
    coreReviewQuestionText() {
      return String(
        this.reviewerOutline.core_review_question ||
        this.sectionReviewProfile.core_review_question ||
        this.taskDefinition.core_review_question ||
        this.taskDefinition.review_goal ||
        ""
      ).trim();
    },
    mustAnswerQuestions() {
      const values = Array.isArray(this.reviewerOutline.must_answer_questions) && this.reviewerOutline.must_answer_questions.length
        ? this.reviewerOutline.must_answer_questions
        : (Array.isArray(this.sectionReviewProfile.must_answer_questions) && this.sectionReviewProfile.must_answer_questions.length
          ? this.sectionReviewProfile.must_answer_questions
          : this.taskDefinition.must_answer_questions);
      return Array.isArray(values) ? values.map((item) => String(item || "").trim()).filter(Boolean) : [];
    },
    mustAnswerCoverage() {
      const rows = Array.isArray(this.review && this.review.must_answer_coverage) ? this.review.must_answer_coverage : [];
      return rows
        .filter((item) => item && typeof item === "object" && String(item.question || "").trim())
        .map((item) => ({
          question: String(item.question || "").trim(),
          coverage_status: String(item.coverage_status || "").trim().toLowerCase() || "uncovered",
        }));
    },
    mustAnswerCoverageOverview() {
      return {
        total: this.mustAnswerCoverage.length,
        covered: this.mustAnswerCoverage.filter((item) => item.coverage_status === "covered").length,
        partial: this.mustAnswerCoverage.filter((item) => item.coverage_status === "partial").length,
        uncovered: this.mustAnswerCoverage.filter((item) => item.coverage_status === "uncovered").length,
      };
    },
    mustAnswerPreview() {
      return this.mustAnswerQuestions.slice(0, 3).join("；");
    },
    mustAnswerCoverageSummary() {
      if (!this.mustAnswerCoverageOverview.total) {
        return "";
      }
      return `共 ${this.mustAnswerCoverageOverview.total} 项，已覆盖 ${this.mustAnswerCoverageOverview.covered} 项，部分覆盖 ${this.mustAnswerCoverageOverview.partial} 项，未覆盖 ${this.mustAnswerCoverageOverview.uncovered} 项`;
    },
    topPendingMustAnswerText() {
      return this.mustAnswerCoverage
        .filter((item) => item.coverage_status !== "covered")
        .slice(0, 2)
        .map((item) => item.question)
        .filter(Boolean)
        .join("；");
    },
    extractedFactCount() {
      const entityCount = Array.isArray(this.review && this.review.medical_key_entities) ? this.review.medical_key_entities.length : 0;
      const dataCount = Array.isArray(this.review && this.review.medical_key_data_points) ? this.review.medical_key_data_points.length : 0;
      return entityCount + dataCount;
    },
  },
  methods: {
    issueKey(item, index) {
      const base = [
        String((item && item.task_code) || "").trim(),
        String((item && item.task_question) || "").trim(),
        String((item && item.problem) || "").trim(),
      ].filter(Boolean).join("|");
      return base || `issue_${index}`;
    },
    judgmentStatusText(status) {
      const mapping = {
        supported: "满足",
        unsupported: "不满足",
        insufficient_information: "信息不足",
        issue: "存在问题",
        question: "待补充",
        missing: "证据不足",
      };
      return mapping[String(status || "").trim().toLowerCase()] || status || "-";
    },
  },
};
</script>

<style scoped>
.block {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 14px;
  background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
}

.pane-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.title,
.sub-title {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.content {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 14px;
}

.muted,
.empty,
.hint,
.profile-line,
.judgment-row {
  color: #64748b;
  font-size: 12px;
  line-height: 1.75;
}

.summary-banner {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px;
  gap: 12px;
  border: 1px solid #dbeafe;
  border-radius: 12px;
  background: linear-gradient(135deg, #eff6ff, #ffffff);
  padding: 14px;
}

.summary-status-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.summary-text {
  font-size: 14px;
  line-height: 1.8;
  color: #0f172a;
}

.summary-aside {
  border-left: 1px solid #dbeafe;
  padding-left: 12px;
}

.summary-aside-label,
.fact-label,
.stat-label {
  font-size: 12px;
  color: #64748b;
}

.summary-aside-value,
.stat-value {
  margin-top: 6px;
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}

.profile-card,
.fact-card,
.judgment-card,
.stat-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #ffffff;
}

.profile-card {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fact-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.fact-card {
  padding: 12px;
}

.fact-value {
  margin-top: 6px;
  font-size: 13px;
  color: #334155;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.stat-card {
  padding: 12px;
  background: #f8fafc;
}

.stat-card.success {
  border-color: #bbf7d0;
  background: #f8fffb;
}

.stat-card.warning {
  border-color: #fde68a;
  background: #fffaf0;
}

.focus-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.judgment-card {
  padding: 12px;
  background: #f8fafc;
}

.judgment-card.supported-card {
  border-color: #bbf7d0;
  background: #f8fffb;
}

.judgment-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.judgment-title {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.6;
}

.hint {
  border-top: 1px solid #eef2f7;
  padding-top: 12px;
}

@media (max-width: 1200px) {
  .summary-banner,
  .fact-grid,
  .stats-row {
    grid-template-columns: 1fr;
  }

  .summary-aside {
    border-left: none;
    border-top: 1px solid #dbeafe;
    padding-left: 0;
    padding-top: 12px;
  }
}
</style>
