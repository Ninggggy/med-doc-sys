<template>
  <div class="panel audit-panel">
    <div class="panel-head">
      <div class="panel-title">执行审计</div>
      <el-button size="mini" type="primary" :loading="loadingAblation" @click="$emit('run-ablation')">
        执行消融实验
      </el-button>
    </div>

    <div class="audit-summary">
      <span>总数 {{ summary.total || 0 }}</span>
      <span>完成 {{ summary.completed_count || 0 }}</span>
      <span>失败 {{ summary.failed_count || 0 }}</span>
      <span v-if="summary.p52_optimize_event_total">P52 优化 {{ summary.p52_optimize_event_total || 0 }}</span>
      <span v-if="summary.p52_meta_reflection_event_total">P52 元反思 {{ summary.p52_meta_reflection_event_total || 0 }}</span>
      <span v-if="summary.p52_ablation_event_total">P52 消融 {{ summary.p52_ablation_event_total || 0 }}</span>
    </div>
    <div class="audit-summary muted-line">
      <span>Envelope 分布 {{ breakdownText(envelopeBreakdown) }}</span>
      <span>Protocol 分布 {{ breakdownText(protocolBreakdown) }}</span>
    </div>

    <div v-if="comparisonRunId" class="compare-box">
      <div class="compare-head">
        <div>
          <div class="compare-title">同章节上一次运行对比</div>
          <div class="muted">对比 run：{{ comparisonRunId }}</div>
        </div>
        <div class="compare-badges">
          <span class="badge">变化阶段 {{ diffSummary.changed_stage_count || 0 }}</span>
          <span class="badge">变化字段 {{ diffSummary.changed_field_count || 0 }}</span>
        </div>
      </div>
      <div v-if="diffStages.length">
        <div
          v-for="stageDiff in diffStages"
          :key="`diff-${stageDiff.stage}`"
          class="diff-card"
          :class="{ changed: stageDiff.changed }"
        >
          <div class="diff-head">
            <strong>{{ stageDiff.stage || '-' }}</strong>
            <span class="diff-status">{{ stageDiff.changed ? '已变化' : '无变化' }}</span>
          </div>
          <div v-if="stageDiff.changed && stageDiff.changes && stageDiff.changes.length">
            <div v-for="change in stageDiff.changes" :key="`${stageDiff.stage}-${change.field}`" class="diff-row">
              <div class="diff-field">{{ fieldLabel(change.field) }}</div>
              <div class="diff-values">
                <div><strong>当前：</strong>{{ formatValue(change.current) }}</div>
                <div><strong>上一轮：</strong>{{ formatValue(change.previous) }}</div>
              </div>
            </div>
          </div>
          <div v-else class="muted">当前阶段与上一轮没有可见差异。</div>
        </div>
      </div>
      <div v-else class="empty">当前没有可展示的阶段级差异。</div>
    </div>

    <div v-if="optimizeDiff.enabled" class="compare-box replay-box">
      <div class="compare-head">
        <div>
          <div class="compare-title">正式 Optimize 与 Replay 对比</div>
          <div class="muted">对比当前 replay 结果与历史正式 optimize 记录。</div>
        </div>
        <div class="compare-badges">
          <span class="badge">变化字段 {{ optimizeDiff.changedFieldCount }}</span>
        </div>
      </div>
      <div v-if="optimizeDiff.items.length">
        <div v-for="item in optimizeDiff.items" :key="`opt-${item.field}`" class="diff-row">
          <div class="diff-field">{{ fieldLabel(item.field) }}</div>
          <div class="diff-values">
            <div><strong>当前：</strong>{{ formatValue(item.current) }}</div>
            <div><strong>正式：</strong>{{ formatValue(item.formal) }}</div>
          </div>
        </div>
      </div>
      <div v-else class="muted">当前 replay 与正式 optimize 没有差异。</div>
    </div>

    <div class="compare-box ablation-box">
      <div class="compare-head">
        <div>
          <div class="compare-title">消融实验</div>
          <div class="muted">{{ isP52Ablation ? 'P52 专用链路能力对比，直接比较 5.1 映射、检索补证、规则过滤和 reviewer 综合判断。' : '基于当前 run 和章节自动生成 replay case 并比较不同 feature flags。' }}</div>
        </div>
        <div class="compare-badges">
          <span class="badge">Case {{ generatedCaseCount }}</span>
          <span class="badge">Variant {{ variantCount }}</span>
        </div>
      </div>
      <div v-if="hasAblation">
        <div v-if="isP52Ablation" class="audit-summary muted-line">
          <span>能力焦点 {{ p52CapabilityFocusText }}</span>
        </div>
        <div class="audit-summary">
          <span>最佳变体 {{ bestVariantLabel }}</span>
          <span>总分 {{ percentText(bestOverallScore) }}</span>
          <span>Delta {{ deltaText(bestOverallDelta) }}</span>
        </div>
        <div class="variant-grid">
          <div v-for="variant in ablationVariants" :key="variant.variant_id" class="variant-card">
            <div class="variant-head">
              <strong>{{ variant.label || variant.variant_id || '-' }}</strong>
              <span class="badge">{{ percentText(variant.metrics && variant.metrics.overall_score) }}</span>
            </div>
            <div class="muted">{{ variant.description || '-' }}</div>
            <div class="audit-row">
              <span><strong>Review：</strong>{{ percentText(variant.metrics && variant.metrics.review && variant.metrics.review.composite_score) }}</span>
              <span><strong>Retrieval：</strong>{{ percentText(variant.metrics && variant.metrics.retrieval && variant.metrics.retrieval.composite_score) }}</span>
            </div>
            <div class="audit-row single">
              <strong>Feature Flags：</strong>{{ featureFlagText(variant.feature_flags) }}
            </div>
            <div v-if="variant.delta_vs_baseline" class="audit-row single">
              <strong>相对基线：</strong>
              总分 {{ deltaText(variant.delta_vs_baseline.overall_delta) }} /
              Review {{ deltaText(variant.delta_vs_baseline.review_delta) }} /
              Retrieval {{ deltaText(variant.delta_vs_baseline.retrieval_delta) }} /
              建议 {{ variant.delta_vs_baseline.recommendation || '-' }}
            </div>
          </div>
        </div>
      </div>
      <div v-else class="empty">当前还没有消融实验结果。</div>
    </div>

    <div v-if="items.length">
      <div v-for="item in items" :key="item.audit_id || `${item.stage}-${item.create_time}`" class="audit-card">
        <div class="audit-head">
          <div>
            <div class="audit-stage">{{ item.stage || '-' }}</div>
            <div class="muted">{{ item.agent_name || '-' }} / {{ item.create_time || '-' }}</div>
          </div>
          <div class="audit-status" :class="statusClass(item.status)">{{ item.status || '-' }}</div>
        </div>
        <div class="audit-row">
          <span><strong>任务：</strong>{{ versionValue(item, 'task_type') }}</span>
          <span><strong>Prompt 版本：</strong>{{ versionValue(item, 'prompt_version_id') }}</span>
        </div>
        <div class="audit-row">
          <span><strong>Envelope：</strong>{{ versionValue(item, 'envelope_version') }}</span>
          <span><strong>Protocol：</strong>{{ versionValue(item, 'protocol_version') }}</span>
        </div>
        <div class="audit-row">
          <span><strong>记忆治理版本：</strong>{{ versionValue(item, 'memory_governance_version') }}</span>
          <span><strong>模板名：</strong>{{ versionValue(item, 'template_name') }}</span>
        </div>
        <div class="audit-row">
          <span><strong>模板模式：</strong>{{ versionValue(item, 'template_mode') }}</span>
          <span><strong>尝试次数：</strong>{{ item.attempt_no || 1 }}</span>
        </div>
        <div class="audit-row single">
          <strong>生效规则：</strong>{{ activeRuleText(item) }}
        </div>
        <div v-if="item.error_message" class="error-box">{{ item.error_message }}</div>
      </div>
    </div>
    <div v-else class="empty">当前没有可展示的执行审计记录。</div>
  </div>
</template>

<script>
export default {
  name: 'ExecutionAuditPanel',
  props: {
    detail: { type: Object, default: () => ({}) },
    feedbackOptimizeResult: { type: Object, default: () => ({}) },
    feedbackHistory: { type: Object, default: () => ({}) },
    ablationDetail: { type: Object, default: () => ({}) },
    loadingAblation: { type: Boolean, default: false },
  },
  computed: {
    items() {
      return Array.isArray(this.detail && this.detail.items) ? this.detail.items : [];
    },
    summary() {
      return this.detail && typeof this.detail.summary === 'object' ? this.detail.summary : {};
    },
    envelopeBreakdown() {
      return this.detail && typeof this.detail.envelope_breakdown === 'object' ? this.detail.envelope_breakdown : {};
    },
    protocolBreakdown() {
      return this.detail && typeof this.detail.protocol_breakdown === 'object' ? this.detail.protocol_breakdown : {};
    },
    comparison() {
      return this.detail && typeof this.detail.comparison === 'object' ? this.detail.comparison : {};
    },
    comparisonRunId() {
      return String((this.comparison && this.comparison.compare_run_id) || '').trim();
    },
    diffSummary() {
      const diff = this.comparison && typeof this.comparison.diff === 'object' ? this.comparison.diff : {};
      return diff && typeof diff.summary === 'object' ? diff.summary : {};
    },
    diffStages() {
      const diff = this.comparison && typeof this.comparison.diff === 'object' ? this.comparison.diff : {};
      return Array.isArray(diff.stage_diffs) ? diff.stage_diffs : [];
    },
    formalOptimizeEntry() {
      const rows = Array.isArray(this.feedbackHistory && this.feedbackHistory.signal_entries)
        ? this.feedbackHistory.signal_entries
        : [];
      const currentKey = String((this.feedbackOptimizeResult && this.feedbackOptimizeResult.feedback_key) || '').trim();
      if (currentKey) {
        const matched = rows.find((item) => String((item && item.feedback_key) || '').trim() === currentKey);
        if (matched) {
          return matched;
        }
      }
      return rows.find((item) => {
        const status = String((item && item.feedback_optimize_status) || '').trim();
        return !!status || String((item && item.analysis_kind) || '').trim() === 'feedback_optimize';
      }) || null;
    },
    optimizeDiff() {
      const current = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult === 'object'
        ? this.feedbackOptimizeResult
        : {};
      const formal = this.formalOptimizeEntry && typeof this.formalOptimizeEntry === 'object'
        ? this.formalOptimizeEntry
        : null;
      if (!formal || !Object.keys(current).length) {
        return { enabled: false, changedFieldCount: 0, items: [] };
      }
      const currentPatches = Array.isArray(current.patch_result && current.patch_result.patches)
        ? current.patch_result.patches.map((item) => item.patch_id || item.rule_code || item.target_agent || '-')
        : [];
      const formalPatches = Array.isArray(formal.patch_result && formal.patch_result.patches)
        ? formal.patch_result.patches.map((item) => item.patch_id || item.rule_code || item.target_agent || '-')
        : [];
      const items = [
        { field: 'feedback_optimize_status', current: current.feedback_optimize_status || '-', formal: formal.feedback_optimize_status || '-' },
        { field: 'candidate_register_status', current: current.candidate_register_status || '-', formal: formal.candidate_register_status || '-' },
        { field: 'replay_status', current: current.replay_status || '-', formal: formal.replay_status || '-' },
        { field: 'primary_error_type', current: (current.analysis_result && current.analysis_result.primary_error_type) || '-', formal: (formal.analysis_result && formal.analysis_result.primary_error_type) || '-' },
        { field: 'patch_ids', current: currentPatches, formal: formalPatches },
        { field: 'optimize_evaluation_verdict', current: (current.optimize_evaluation && current.optimize_evaluation.overall_verdict) || '-', formal: (formal.optimize_evaluation && formal.optimize_evaluation.overall_verdict) || '-' },
      ].filter((item) => JSON.stringify(item.current) !== JSON.stringify(item.formal));
      return {
        enabled: true,
        changedFieldCount: items.length,
        items,
      };
    },
    ablationSummary() {
      return this.ablationDetail && typeof this.ablationDetail.summary === 'object' ? this.ablationDetail.summary : {};
    },
    ablationVariants() {
      return Array.isArray(this.ablationDetail && this.ablationDetail.variants) ? this.ablationDetail.variants : [];
    },
    hasAblation() {
      return !!(this.ablationVariants.length || Number(this.ablationSummary.variant_count || 0));
    },
    isP52Ablation() {
      return String((this.ablationDetail && this.ablationDetail.workflow_mode) || '').trim() === 'p52_ablation_v1';
    },
    generatedCaseCount() {
      return Number(this.ablationDetail && this.ablationDetail.generated_case_count) || Number(this.ablationSummary.case_count || 0);
    },
    variantCount() {
      return Number(this.ablationSummary.variant_count || this.ablationVariants.length || 0);
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
      return Number((best && best.metrics && best.metrics.overall_score) || this.ablationSummary.best_overall_score || 0);
    },
    bestOverallDelta() {
      const best = this.bestVariant;
      return Number(best && best.delta_vs_baseline && best.delta_vs_baseline.overall_delta) || 0;
    },
    p52CapabilityFocusText() {
      if (!this.isP52Ablation) {
        return '-';
      }
      return '5.1 映射 / 定向检索 / 规则过滤 / reviewer 综合判断';
    },
  },
  methods: {
    versionValue(item, key) {
      const snapshot = item && typeof item.version_snapshot === 'object' ? item.version_snapshot : {};
      return this.formatValue(snapshot[key]);
    },
    activeRuleText(item) {
      const snapshot = item && typeof item.version_snapshot === 'object' ? item.version_snapshot : {};
      return this.formatValue(snapshot.active_rule_codes || snapshot.active_rule_ids || []);
    },
    breakdownText(value) {
      if (!value || typeof value !== 'object') {
        return '-';
      }
      const entries = Object.entries(value)
        .filter(([, count]) => Number(count) > 0)
        .map(([key, count]) => `${key}:${count}`);
      return entries.length ? entries.join(' / ') : '-';
    },
    statusClass(value) {
      const status = String(value || '').trim();
      if (status === 'completed' || status === 'success') {
        return 'ok';
      }
      if (status === 'failed' || status === 'error') {
        return 'failed';
      }
      return 'running';
    },
    featureFlagText(flags) {
      if (!flags || typeof flags !== 'object') {
        return '-';
      }
      const entries = Object.entries(flags).map(([key, value]) => `${key}:${value ? 'on' : 'off'}`);
      return entries.length ? entries.join(' / ') : '-';
    },
    formatValue(value) {
      if (Array.isArray(value)) {
        return value.length ? value.join(' / ') : '-';
      }
      if (value && typeof value === 'object') {
        const keys = Object.keys(value);
        return keys.length ? JSON.stringify(value) : '-';
      }
      if (value === null || value === undefined || value === '') {
        return '-';
      }
      return String(value);
    },
    percentText(value) {
      const num = Number(value);
      return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : '-';
    },
    deltaText(value) {
      const num = Number(value);
      if (!Number.isFinite(num)) {
        return '-';
      }
      const prefix = num > 0 ? '+' : '';
      return `${prefix}${(num * 100).toFixed(1)}%`;
    },
    fieldLabel(field) {
      const mapping = {
        prompt_version_id: 'Prompt 版本',
        active_rule_codes: '生效规则',
        task_type: '任务类型',
        envelope_version: 'Envelope 版本',
        protocol_version: 'Protocol 版本',
        template_name: '模板名',
        template_mode: '模板模式',
        memory_governance_version: '记忆治理版本',
        feedback_optimize_status: '优化状态',
        candidate_register_status: '候选注册状态',
        replay_status: 'Replay 状态',
        primary_error_type: '主错误类型',
        patch_ids: 'Patch 列表',
        optimize_evaluation_verdict: '优化评价',
      };
      return mapping[field] || field || '-';
    },
  },
};
</script>

<style scoped>
.audit-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-head,
.compare-head,
.audit-head,
.variant-head,
.diff-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.panel-title,
.compare-title,
.audit-stage {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.audit-summary,
.audit-row,
.diff-row {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  flex-wrap: wrap;
  font-size: 12px;
  color: #475569;
}

.audit-row.single {
  justify-content: flex-start;
}

.muted,
.muted-line {
  color: #94a3b8;
  font-size: 12px;
}

.compare-box,
.audit-card,
.variant-card,
.diff-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}

.diff-card.changed,
.variant-card {
  background: #f8fafc;
}

.compare-badges {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.badge,
.audit-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #e2e8f0;
  color: #334155;
}

.audit-status.ok {
  background: #dcfce7;
  color: #166534;
}

.audit-status.failed {
  background: #fee2e2;
  color: #991b1b;
}

.audit-status.running {
  background: #dbeafe;
  color: #1d4ed8;
}

.diff-values,
.variant-grid {
  display: grid;
  gap: 8px;
}

.error-box {
  margin-top: 8px;
  padding: 8px 10px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  color: #b91c1c;
  font-size: 12px;
  line-height: 1.6;
}

.empty {
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.6;
}
</style>
