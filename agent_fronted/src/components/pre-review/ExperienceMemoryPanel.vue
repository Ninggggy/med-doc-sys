<template>
  <div class="section-block">
    <div class="section-title">历史经验</div>
    <div class="rule-summary">
      <span>总数 {{ summary.total || 0 }}</span>
      <span>章节作用域 {{ summary.section_scope_count || 0 }}</span>
      <span>产品作用域 {{ summary.product_scope_count || 0 }}</span>
      <span>项目作用域 {{ summary.project_scope_count || 0 }}</span>
    </div>

    <div v-if="items.length">
      <div v-for="item in items" :key="item.experience_id || item.content" class="issue-card">
        <div class="issue-title">{{ item.experience_type || "经验" }}</div>
        <div class="muted">作用域：{{ item.scope_type || "-" }} / {{ item.scope_key || "-" }}</div>
        <div class="muted">使用次数：{{ item.usage_count || 0 }}，成功次数：{{ item.success_count || 0 }}</div>
        <div class="list-item">{{ item.content || "-" }}</div>
        <div v-if="item.trigger_conditions && item.trigger_conditions.length" class="muted">
          触发条件：{{ item.trigger_conditions.join(" / ") }}
        </div>
        <div
          v-if="item.payload && item.payload.high_frequency_doc_ids && item.payload.high_frequency_doc_ids.length"
          class="muted"
        >
          高频资料：{{ item.payload.high_frequency_doc_ids.join(" / ") }}
        </div>
      </div>
    </div>
    <div v-else class="empty">当前章节还没有沉淀历史经验。</div>
  </div>
</template>

<script>
export default {
  name: "ExperienceMemoryPanel",
  props: {
    detail: { type: Object, default: () => ({}) },
  },
  computed: {
    items() {
      return Array.isArray(this.detail && this.detail.items) ? this.detail.items : [];
    },
    summary() {
      return this.detail && typeof this.detail.summary === "object" ? this.detail.summary : {};
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

.section-title {
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

.empty {
  color: #94a3b8;
  font-size: 13px;
  line-height: 1.8;
}
</style>
