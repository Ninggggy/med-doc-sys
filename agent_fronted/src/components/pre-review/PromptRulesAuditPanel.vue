<template>
  <div class="section-block">
    <div class="section-title">Prompt 规则审查</div>
    <div class="rule-summary">
      <span>总数 {{ summary.total || 0 }}</span>
      <span>Seed {{ summary.seed_count || 0 }}</span>
      <span>Feedback Patch {{ summary.feedback_patch_count || 0 }}</span>
    </div>

    <div class="prompt-grid">
      <div class="prompt-pane">
        <div class="sub-title">当前运行命中的 Prompt 规则</div>
        <div v-if="tracePromptRuleGroups.length">
          <div v-for="group in tracePromptRuleGroups" :key="`trace-${group.task}`" class="issue-card">
            <div class="issue-title">{{ taskTypeLabel(group.task) }}</div>
            <div v-for="(item, index) in group.items" :key="`${group.task}-${index}`" class="list-item">
              {{ item.rule_code || item.rule_name || "-" }}
              <span class="muted">({{ item.source_type || "-" }})</span>
            </div>
          </div>
        </div>
        <div v-else class="empty">当前运行没有命中额外 Prompt 规则。</div>
      </div>

      <div class="prompt-pane">
        <div class="sub-title">当前章节可用 Prompt 规则</div>
        <div v-if="promptRuleGroups.length">
          <div v-for="group in promptRuleGroups" :key="group.task" class="issue-card">
            <div class="issue-title">
              {{ taskTypeLabel(group.task) }}
              <span class="muted">({{ group.items.length }})</span>
            </div>
            <div
              v-for="item in group.items"
              :key="item.rule_id || `${group.task}-${item.rule_code}`"
              class="list-item"
            >
              <strong>{{ item.rule_code || item.rule_name || "-" }}</strong>
              <span class="muted"> / {{ item.source_type || "-" }} / priority {{ item.priority || 0 }}</span>
              <div class="rule-text">{{ item.rule_text || "-" }}</div>
            </div>
          </div>
        </div>
        <div v-else class="empty">当前章节没有可用 Prompt 规则。</div>
      </div>
    </div>
  </div>
</template>

<script>
function groupByTask(items) {
  const buckets = {};
  (Array.isArray(items) ? items : []).forEach((item) => {
    const task = String((item && item.task_type) || "unknown").trim() || "unknown";
    if (!buckets[task]) {
      buckets[task] = [];
    }
    buckets[task].push(item);
  });
  return Object.keys(buckets).map((task) => ({ task, items: buckets[task] }));
}

export default {
  name: "PromptRulesAuditPanel",
  props: {
    selectedPromptRules: { type: Object, default: () => ({ planner: [], retrieval_evaluator: [], reviewer: [] }) },
    promptRulesDetail: { type: Object, default: () => ({}) },
  },
  computed: {
    summary() {
      return this.promptRulesDetail && typeof this.promptRulesDetail.summary === "object"
        ? this.promptRulesDetail.summary
        : {};
    },
    promptRuleGroups() {
      return groupByTask(this.promptRulesDetail && this.promptRulesDetail.items);
    },
    tracePromptRuleGroups() {
      const groups = [];
      const source = this.selectedPromptRules && typeof this.selectedPromptRules === "object"
        ? this.selectedPromptRules
        : {};
      Object.keys(source).forEach((task) => {
        const rows = Array.isArray(source[task]) ? source[task] : [];
        if (rows.length) {
          groups.push({ task, items: rows });
        }
      });
      return groups;
    },
  },
  methods: {
    taskTypeLabel(value) {
      const mapping = {
        planner: "Planner",
        retrieval_evaluator: "Retrieval Evaluator",
        reviewer: "Reviewer",
        feedback_analyzer: "Feedback Analyzer",
        feedback_optimizer: "Feedback Optimizer",
        meta_reflector: "Meta Reflector",
      };
      return mapping[String(value || "").trim()] || value || "-";
    },
  },
};
</script>

<style scoped>
.section-block {
  border: 1px solid #eef1f6;
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}

.section-title,
.sub-title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 10px;
}

.rule-summary {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #64748b;
  margin-bottom: 10px;
}

.prompt-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.prompt-pane {
  border: 1px solid #eef1f6;
  border-radius: 8px;
  padding: 12px;
  background: #fafcff;
}

.issue-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px;
  background: #f8fafc;
  margin-bottom: 10px;
}

.issue-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 6px;
}

.list-item,
.muted {
  font-size: 13px;
  line-height: 1.8;
  color: #475569;
}

.muted {
  color: #64748b;
}

.rule-text {
  margin-top: 6px;
  color: #334155;
  line-height: 1.7;
}

.empty {
  color: #94a3b8;
  font-size: 13px;
  line-height: 1.8;
}
</style>
