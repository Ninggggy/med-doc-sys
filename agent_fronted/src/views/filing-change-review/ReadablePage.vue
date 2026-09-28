<template>
  <div class="readable-page">
    <template v-if="elements.length">
      <section v-for="(element,index) in elements" :key="element.region_id + ':' + index" class="readable-region">
        <button v-if="element.bbox_pdf && element.bbox_pdf.length===4" class="locate-region" @click="$emit('locate',element.bbox_pdf)">定位区域 {{ index+1 }}</button>
        <table v-if="element.kind==='table'" class="readable-table"><tbody>
          <tr v-for="(row,ri) in tableRows(element)" :key="ri"><td v-for="(value,ci) in row" :key="ci">{{ value }}</td></tr>
        </tbody></table>
        <dl v-else-if="element.kind==='field'"><dt>{{ element.label }}</dt><dd>{{ element.value || '未识别到值' }}</dd></dl>
        <p v-else>{{ element.text }}</p>
      </section>
    </template>
    <p v-else class="legacy-body">{{ chunk.effective_text || chunk.text || chunk.raw_text || '无可用正文' }}</p>
  </div>
</template>
<script>
export default {
  props: {chunk: {type:Object,default:()=>({})}},
  computed: {elements() { return this.chunk.readable_elements || []; }},
  methods: {tableRows(element) { const t=(this.chunk.tables || [])[element.table_index] || {};return t.raw_rows || t.rows || []; }}
};
</script>
<style scoped>
.readable-page {line-height:1.7; overflow-wrap:anywhere;}
.readable-region {padding:10px 0;border-bottom:1px solid #e8edf3;}
.locate-region {float:right;color:#376b9a;border:0;background:transparent;cursor:pointer;font-size:12px;}
dl {display:grid;grid-template-columns:minmax(80px,130px) 1fr;gap:12px;margin:4px 0;}
dt {font-weight:600;color:#44546a;} dd {margin:0;white-space:pre-wrap;}
p {margin:4px 0;white-space:pre-wrap;}
.readable-table {width:100%;border-collapse:collapse;table-layout:fixed;}
td {border:1px solid #ccd5df;padding:7px;vertical-align:top;white-space:pre-wrap;}
</style>
