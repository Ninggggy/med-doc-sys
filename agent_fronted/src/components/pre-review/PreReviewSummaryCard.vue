<template>
  <div v-if="summary || currentReview || project" class="block">
    <div class="title">{{ labels.cardTitle }}</div>

    <div class="meta-list" :class="{ compact: compactMetaOnly }">
      <div class="meta-row">
        <span class="meta-label">{{ labels.projectId }}</span>
        <span class="meta-value">{{ projectIdText }}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">{{ labels.projectName }}</span>
        <span class="meta-value">{{ projectNameText }}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">{{ labels.registrationScope }}</span>
        <span class="meta-value">{{ registrationScopeText }}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">{{ labels.registrationClass }}</span>
        <span class="meta-value">{{ registrationClassText }}</span>
      </div>
      <div class="meta-row feedback-row">
        <span class="meta-label">{{ labels.feedbackLoop }}</span>
        <el-switch
          :value="feedbackLoopEnabled"
          :active-text="labels.feedbackOn"
          :inactive-text="labels.feedbackOff"
          @input="$emit('update:feedback-loop-enabled', $event)"
        />
      </div>
      <div class="meta-tip">{{ labels.feedbackLoopTip }}</div>
    </div>

    <div v-if="summary && !compactMetaOnly" class="summary-grid">
      <div class="summary-item">
        <div class="summary-label">{{ labels.reviewedSectionTotal }}</div>
        <div class="summary-value">{{ summary.reviewed_section_total || 0 }}</div>
      </div>
      <div class="summary-item">
        <div class="summary-label">{{ labels.pendingSectionTotal }}</div>
        <div class="summary-value">{{ summary.pending_section_total || 0 }}</div>
      </div>
      <div class="summary-item">
        <div class="summary-label">{{ labels.sectionTotal }}</div>
        <div class="summary-value">{{ summary.section_total || 0 }}</div>
      </div>
    </div>

    <div v-if="currentReview && !compactMetaOnly" class="current-review">
      <div class="subtitle">{{ labels.currentSectionConclusion }}</div>
      <div class="list-item">{{ labels.sectionName }}{{ currentSectionLabel || "-" }}</div>
      <div class="list-item">{{ labels.conclusion }}{{ reviewConclusionLabel(currentReview.pre_review_conclusion || currentReview.conclusion) }}</div>
      <div v-if="currentSummary" class="list-item">{{ labels.summary }}{{ currentSummary }}</div>
    </div>

    <div v-if="summaryText && !compactMetaOnly" class="overall-text">
      <div class="subtitle">{{ labels.documentOverview }}</div>
      <div class="list-item">{{ summaryText }}</div>
    </div>
  </div>
</template>

<script>

const TEXT = {
  cardTitle: "\u9879\u76ee\u4e0e\u8fd0\u884c\u4fe1\u606f",
  projectId: "\u9879\u76ee ID\uff1a",
  projectName: "\u9879\u76ee\u540d\u79f0\uff1a",
  registrationScope: "\u6ce8\u518c\u8303\u56f4\uff1a",
  registrationClass: "\u6ce8\u518c\u5206\u7c7b\uff1a",
  feedbackLoop: "\u53cd\u9988\u4f18\u5316\u56de\u8def",
  feedbackOn: "\u5f00\u542f",
  feedbackOff: "\u5173\u95ed",
  feedbackLoopTip: "\u7528\u4e8e\u6d88\u878d\u5b9e\u9a8c\uff0c\u51b3\u5b9a\u672c\u6b21\u8fd0\u884c\u540e\u7684\u53cd\u9988\u662f\u5426\u8fdb\u5165\u4f18\u5316\u95ed\u73af\u3002",
  reviewedSectionTotal: "\u5df2\u5ba1\u7ae0\u8282",
  pendingSectionTotal: "\u5f85\u5ba1\u7ae0\u8282",
  sectionTotal: "\u7ae0\u8282\u603b\u6570",
  currentSectionConclusion: "\u5f53\u524d\u7ae0\u8282\u7ed3\u8bba",
  sectionName: "\u7ae0\u8282\uff1a",
  conclusion: "\u7ed3\u8bba\uff1a",
  summary: "\u6458\u8981\uff1a",
  documentOverview: "\u5168\u6587\u6982\u89c8",
};

export default {
  name: "PreReviewSummaryCard",
  props: {
    summary: { type: Object, default: null },
    currentReview: { type: Object, default: null },
    currentSectionLabel: { type: String, default: "" },
    reviewConclusionLabel: { type: Function, required: true },
    project: { type: Object, default: null },
    feedbackLoopEnabled: { type: Boolean, default: true },
    compactMetaOnly: { type: Boolean, default: false },
  },
  computed: {
    labels() {
      return TEXT;
    },
    projectIdText() {
      return String((this.project && this.project.project_id) || "").trim() || "-";
    },
    projectNameText() {
      return String((this.project && this.project.project_name) || "").trim() || "-";
    },
    registrationScopeText() {
      return String((this.project && this.project.registration_scope) || "").trim() || "-";
    },
    registrationClassText() {
      return String(
        (this.project && (this.project.registration_leaf || this.project.registration_class || this.project.registration_description)) || ""
      ).trim() || "-";
    },
    currentSummary() {
      return String((this.currentReview && (this.currentReview.section_summary || this.currentReview.summary)) || "").trim();
    },
    summaryText() {
      if (!this.summary || typeof this.summary !== "object") {
        return "";
      }
      const reviewed = Number(this.summary.reviewed_section_total) || 0;
      const pending = Number(this.summary.pending_section_total) || 0;
      const total = Number(this.summary.section_total) || (reviewed + pending);
      const chapterSummaries = Array.isArray(this.summary.chapter_conclusion_summary)
        ? this.summary.chapter_conclusion_summary
        : [];
      const conclusionCounts = {};
      chapterSummaries.forEach((item) => {
        if (!item || item.status !== "reviewed") {
          return;
        }
        const conclusion = String(item.conclusion || "").trim();
        if (!conclusion) {
          return;
        }
        conclusionCounts[conclusion] = Number(conclusionCounts[conclusion] || 0) + 1;
      });
      const parts = [];
      if (total > 0) {
        parts.push(`\u5df2\u5ba1 ${reviewed}/${total} \u7ae0\uff0c\u5f85\u5ba1 ${pending} \u7ae0`);
      } else if (reviewed || pending) {
        parts.push(`\u5df2\u5ba1 ${reviewed} \u7ae0\uff0c\u5f85\u5ba1 ${pending} \u7ae0`);
      }
      const labels = [
        ["supported", "\u652f\u6301"],
        ["unsupported", "\u4e0d\u652f\u6301"],
        ["insufficient_information", "\u4fe1\u606f\u4e0d\u8db3"],
        ["partially_supported", "\u90e8\u5206\u652f\u6301"],
      ];
      const conclusionSummary = labels
        .filter(([key]) => Number(conclusionCounts[key] || 0) > 0)
        .map(([key, label]) => `${label} ${conclusionCounts[key]} \u7ae0`);
      if (conclusionSummary.length) {
        parts.push(`\u7ed3\u8bba\u5206\u5e03\uff1a${conclusionSummary.join("\uff1b")}`);
      }
      return parts.join("\uff1b");
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
  color: #1e293b;
}

.subtitle {
  margin: 12px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}

.meta-list {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  background: #fafcff;
}

.meta-list.compact {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
}

.meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
}

.meta-row:last-of-type {
  margin-bottom: 0;
}

.meta-label {
  color: #64748b;
  white-space: nowrap;
}

.meta-value {
  flex: 1;
  text-align: right;
  color: #0f172a;
  word-break: break-all;
}

.feedback-row {
  align-items: center;
}

.meta-list.compact .feedback-row,
.meta-list.compact .meta-tip {
  grid-column: 1 / -1;
}

.meta-tip {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.summary-item {
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  padding: 10px;
  background: #fafcff;
}

.summary-label {
  font-size: 12px;
  color: #64748b;
}

.summary-value {
  margin-top: 6px;
  font-size: 22px;
  font-weight: 600;
  color: #0f172a;
}

.list-item {
  font-size: 12px;
  line-height: 1.7;
  color: #475569;
}

.overall-text {
  margin-top: 12px;
}

@media (max-width: 900px) {
  .summary-grid {
    grid-template-columns: 1fr;
  }

  .meta-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .meta-value {
    text-align: left;
  }
}
</style>
