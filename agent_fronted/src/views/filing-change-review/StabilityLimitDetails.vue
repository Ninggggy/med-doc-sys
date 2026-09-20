<template>
  <details class="stability-limit-details">
    <summary>查看超限及待确认明细</summary>
    <el-select v-model="indicator" clearable filterable placeholder="全部指标" size="small" aria-label="筛选异常指标">
      <el-option v-for="name in indicators" :key="name" :label="name" :value="name" />
    </el-select>
    <section v-for="group in groups" :key="group.key">
      <h4>{{ group.label }}：记录总数 {{ group.reported }}，已保存明细 {{ group.all.length }}</h4>
      <el-alert v-if="group.incomplete" title="历史明细不完整：仅可查看已保存记录，缺失明细不能自动补齐。" type="warning" :closable="false" />
      <p v-if="!group.filtered.length">当前筛选下没有已保存明细。</p>
      <p v-else>当前显示 {{ (pages[group.key] - 1) * 20 + 1 }}–{{ Math.min(pages[group.key] * 20, group.filtered.length) }} / {{ group.filtered.length }} 条</p>
      <div v-for="(detail, i) in pageRows(group)" :key="`${group.key}-${pages[group.key]}-${i}`" class="limit-entry">
        <b>[{{ group.label }}] {{ detail.indicator || '未知指标' }}</b>；{{ detail.context || '-' }}
        <div>检测结果：{{ detail.result_text || '-' }}；可接受标准：{{ detail.limit_text || '-' }}</div>
        <div>结果单位：{{ detail.result_unit || '未标注' }}；标准单位：{{ detail.limit_unit || '未标注' }}</div>
        <div>原因：{{ detail.reason_text || detail.reason || '历史未记录' }}</div>
        <div v-if="detail.unit_note">{{ detail.unit_note }}</div>
        <div>来源：{{ detail.source_file_name || detail.source_doc_id || '历史未记录' }}</div>
        <details v-if="detail.source_position"><summary>原表位置</summary><pre>{{ JSON.stringify(detail.source_position, null, 2) }}</pre></details>
        <details v-if="detail.numeric_verification && detail.numeric_verification.length"><summary>OCR数值候选与核验位置</summary>
          <div v-for="(check, ni) in detail.numeric_verification" :key="ni">
            第{{ check.page || '?' }}页；主识别：{{ check.primary }}；局部复核：{{ check.secondary || '未取得' }}；
            {{ check.status === 'verified' ? '候选一致（仍可人工核对）' : '待人工核对' }}
            <pre>{{ JSON.stringify(check.bbox_pdf || check.bbox_pixel) }}</pre>
          </div>
        </details>
        <div v-for="(component, ci) in (detail.components || [])" :key="ci" class="component-entry">
          {{ component.item || '未识别组成项' }}：{{ componentStatus(component) }}；
          结果 {{ component.result || '缺失' }}；标准 {{ component.limit || '缺失' }}；
          {{ component.reason_text || component.reason || '原因未记录' }}
        </div>
      </div>
      <el-pagination v-if="group.filtered.length" :current-page.sync="pages[group.key]" :page-size="20" :total="group.filtered.length" layout="total, prev, pager, next" />
    </section>
  </details>
</template>

<script>
export default {
  name: 'StabilityLimitDetails',
  props: { check: { type: Object, default: () => ({}) } },
  data() { return { indicator: '', pages: { out_of_spec: 1, undecidable: 1 } }; },
  computed: {
    groups() {
      return [['out_of_spec', '已超限'], ['undecidable', '待确认']].map(([key, label]) => {
        const all = Array.isArray(this.check[`${key}_details`]) ? this.check[`${key}_details`] : [];
        const rawCount = Number(this.check[`${key}_count`]);
        const reported = Number.isFinite(rawCount) && rawCount >= 0 ? rawCount : all.length;
        return { key, label, all, reported, incomplete: reported > all.length,
          filtered: this.indicator ? all.filter(row => (row.indicator || '未知指标') === this.indicator) : all };
      });
    },
    indicators() { return [...new Set(this.groups.flatMap(group => group.all.map(row => row.indicator || '未知指标')))]; }
  },
  watch: {
    check() { this.indicator = ''; this.resetPages(); },
    indicator() { this.resetPages(); }
  },
  methods: {
    resetPages() { this.pages.out_of_spec = 1; this.pages.undecidable = 1; },
    pageRows(group) { return group.filtered.slice((this.pages[group.key] - 1) * 20, this.pages[group.key] * 20); },
    componentStatus(component) { return component.within_standard === false ? '已超限' : component.within_standard === true ? '未超限' : '待确认'; }
  }
};
</script>

<style scoped>
.stability-limit-details { margin-top: 8px; color: #303133; }
.limit-entry { padding: 8px 0; border-bottom: 1px solid #dcdfe6; overflow-wrap: anywhere; }
.component-entry { margin: 4px 0 0 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
