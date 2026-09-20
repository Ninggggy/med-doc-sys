<template>
  <el-drawer title="解析问题核对" :visible.sync="visible" size="94%" :before-close="close" append-to-body>
    <div class="parse-review-panel">
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-alert v-if="readiness && readiness.reference_usage" :title="readiness.reference_usage.message" type="info" :closable="false" />
      <div class="toolbar">
        <el-button :loading="loading" :disabled="saving" @click="sourceTarget ? openSource(sourceTarget.source_kind, sourceTarget.doc_id) : open()">重新检查</el-button>
        <span v-if="sourceTarget">{{ sourceTarget.source_kind === 'reference' ? '本文件核对；不代表本次审评已采用该参考资料' : '本文件核对；全部审评输入是否就绪仍以就绪检查为准' }}</span>
        <span v-else-if="readiness">{{ readiness.ready ? '解析就绪，可返回启动审评' : `存在 ${readiness.blocking_count} 项阻塞，请逐项处理` }}</span>
      </div>
      <el-table v-if="!sourceTarget" :data="blockingPage" size="mini" max-height="210" @row-click="loadSource">
        <el-table-column prop="file_name" label="资料" min-width="160" />
        <el-table-column prop="page" label="页码" width="70" />
        <el-table-column prop="message" label="问题" min-width="260" />
        <el-table-column label="处理" width="90"><template slot-scope="scope"><el-button type="text" :disabled="saving" @click.stop="loadSource(scope.row)">去核对</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="!sourceTarget" :current-page.sync="blockingIndex" :page-size="20" :total="(readiness && readiness.blocking_issues || []).length" layout="total, prev, pager, next" />
      <el-alert v-if="context && !context.issues.length" title="本文件没有已定位的待处理问题；这不代替原件完整性人工验收。" type="info" :closable="false" />
      <el-alert v-if="context && context.revision_error" :title="context.revision_error.message" type="warning" :closable="false" />
      <div v-if="context && context.items && context.items.length" class="toolbar">
        <el-button :disabled="saving || loading || dirty || !context.items.some(i => i.issue_key === selectedKey)" @click="revoke(false)">撤销本项已保存修订</el-button>
        <el-button :disabled="saving || loading || dirty" @click="revoke(true)">撤销本文件全部区域修订</el-button>
        <span>仅撤销有效覆盖层，历史记录和原件保留；相关问题将重新核查。</span>
      </div>
      <div v-if="context" class="editor">
        <div class="original">
          <p>原件：{{ context.file_name }} · 第 {{ selected && selected.page || '?' }} 页</p>
          <el-alert v-if="imageError" :title="imageError" type="warning" :closable="false" />
          <el-button v-if="selected && selected.page" type="text" :loading="imageLoading" @click="loadImage">重读原页</el-button>
          <div v-if="preview" class="image-frame">
            <img :src="preview.image_data_url" alt="待核对的原始PDF页面" />
            <div v-if="regionStyle" class="region" :style="regionStyle" aria-label="问题区域" />
          </div>
        </div>
        <div class="correction">
          <p>原始解析状态保留；人工修订另存，不覆盖原件或历史报告。</p>
          <el-select :value="selectedKey" placeholder="选择本文件的问题" style="width:100%" @change="selectIssue">
            <el-option v-for="issue in context.issues" :key="issue.issue_key" :value="issue.issue_key" :label="`${issue.resolved ? '已保存处理' : '待处理'} · 第${issue.page || '?'}页 · ${issue.message}`" />
          </el-select>
          <template v-if="selected">
            <p>{{ selected.message }} · {{ selected.code }}</p>
            <details><summary>查看本页识别正文</summary><pre>{{ sourceChunk.text || sourceChunk.raw_text || '无可用正文' }}</pre></details>
            <details v-if="recoveryRows.length" class="recovery-evidence">
              <summary>查看局部复核证据（{{ recoveryRows.length }}处，含未处理区域）</summary>
              <p>只读候选，不代表识别正确。定位仅改变原图高亮，不改变本项修订范围或填写内容。</p>
              <el-button v-if="evidenceFocus" size="mini" @click="evidenceFocus=null">返回本项修订范围</el-button>
              <el-table :data="recoveryPage" size="mini" max-height="330">
                <el-table-column label="位置与状态" min-width="180"><template slot-scope="scope">
                  <div>{{ scope.row.status }}{{ scope.row.reused ? '（复用结果）' : '' }}</div>
                  <div>{{ scope.row.location }}</div><div>{{ scope.row.reason }}</div>
                  <el-button type="text" :disabled="!scope.row.box" @click="evidenceFocus=scope.row.box">定位此证据</el-button>
                </template></el-table-column>
                <el-table-column label="旧文本 / 当前识别 / 局部候选" min-width="240"><template slot-scope="scope">
                  <div v-if="scope.row.embedded">旧文本层（仅供核验）：{{ scope.row.embedded }}</div>
                  <div>原识别：{{ scope.row.primary || '无可用文字' }}</div>
                  <div>候选：{{ scope.row.candidates || '无可用候选；不能视为无文字' }}</div>
                  <details v-if="scope.row.fragmentCandidates.length" class="fragment-candidate-evidence">
                    <summary>查看候选的分离来源（尚未确认）</summary>
                    <div v-for="(candidate, ci) in scope.row.fragmentCandidates" :key="ci">
                      <div>{{ candidate.text }}</div>
                      <div v-if="candidate.incomplete">部分来源位置无法可靠定位，请核对完整原区域。</div>
                      <el-button v-for="(box, bi) in candidate.boxes" :key="bi" type="text" @click="evidenceFocus=box">来源片段{{ bi + 1 }}：{{ box.join(', ') }}</el-button>
                    </div>
                  </details>
                  <details v-if="scope.row.sharedCandidates" class="shared-crop-evidence">
                    <summary>查看关联识别块完整候选（只读）</summary>
                    <pre>{{ scope.row.sharedCandidates }}</pre>
                    <el-button type="text" :disabled="!scope.row.sharedBox" @click="evidenceFocus=scope.row.sharedBox">定位关联识别块</el-button>
                  </details>
                  <details v-if="scope.row.cellCandidates.length" class="cell-candidate-evidence">
                    <summary>查看归属待确认的单元格</summary>
                    <div v-for="(candidate, ci) in scope.row.cellCandidates" :key="ci">
                      <div>{{ candidate.text }}</div>
                      <el-button v-for="(box, bi) in candidate.boxes" :key="bi" type="text" @click="evidenceFocus=box">候选单元格{{ bi + 1 }}：{{ box.join(', ') }}</el-button>
                    </div>
                  </details>
                </template></el-table-column>
              </el-table>
              <el-pagination :current-page.sync="evidencePageIndex" :page-size="20" :total="recoveryRows.length" layout="total, prev, pager, next" />
            </details>
            <div v-if="tableRepairChoices.length" class="table-repair-navigation">
              <el-alert title="本字段涉及表格，不能用正文覆盖。请进入完整表格修订，并在关联问题中逐项确认字段归属；无法唯一对应时仍需重新解析或更换原件。" type="info" :closable="false" />
              <el-button v-for="issue in tableRepairChoices" :key="issue.issue_key" type="text" :disabled="saving || loading" @click="selectIssue(issue.issue_key)">转到完整表格核对：{{ issue.message || issue.code }}</el-button>
            </div>
            <el-alert v-if="!actions.length && !tableRepairChoices.length" :title="selected.resolved ? '该问题已由有效修订处理；如需更改，请选择原已保存修订项。' : '该问题需重新解析、更换清晰原件或使用现有数值核对入口，不能忽略后继续。'" type="warning" :closable="false" />
            <template v-if="actions.length">
              <el-select v-model="draft.action" placeholder="选择处理方式">
                <el-option v-for="action in actions" :key="action.value" :value="action.value" :label="action.label" />
              </el-select>
              <el-input v-if="draft.action === 'correct_text'" v-model="draft.text" type="textarea" :rows="8" placeholder="逐字核对原页，填写该区域的完整文字" />
              <el-checkbox v-if="draft.action === 'correct_text'" v-model="draft.numeric_text_verified">已逐字核对本区域全部数值、单位及符号；区域外问题仍保留</el-checkbox>
              <div v-if="['correct_text','correct_table'].includes(draft.action) && selected.continuation_field_options">
                <p>仅当本区域开头是上一页字段的无标题续文时选择；须对照前页核对，不要在原文中补造标题。选择只作用于首个明确标题之前的文字。</p>
                <select v-model.number="draft.continuation_item_no" class="field-target" aria-label="无标题续文所属字段">
                  <option :value="null">无续文／未指定（不自动猜测）</option>
                  <option v-for="field in selected.continuation_field_options" :key="field.item_no" :value="field.item_no">{{ field.item_no }}. {{ field.title }}</option>
                </select>
              </div>
              <template v-if="draft.action === 'correct_text' && selected.field_options">
                <el-alert title="逐字核对上方完整正文，再指定所属字段。多个字段须按原文顺序逐段分配，不能遗漏或重复；此前人工字段仍保留。" type="warning" :closable="false" />
                <select v-if="!draft.field_assignments" v-model.number="draft.target_item_no" class="field-target" aria-label="明确选择文字所属字段">
                  <option :value="null" disabled>明确选择文字所属字段</option>
                  <option v-for="field in selected.field_options" :key="field.item_no" :value="field.item_no">{{ field.item_no }}. {{ field.title }}</option>
                </select>
                <el-button v-if="!draft.field_assignments" size="mini" @click="draft.field_assignments=[{text:draft.text,target_item_no:null}]">本区域包含多个字段，逐段分配</el-button>
                <template v-else>
                  <div v-for="(part,index) in draft.field_assignments" :key="index">
                    <el-input v-model="part.text" type="textarea" :rows="2" :placeholder="`第 ${index+1} 段原文`" />
                    <select v-model.number="part.target_item_no" class="field-target" :aria-label="`第 ${index+1} 段所属字段`">
                      <option :value="null" disabled>此段所属字段</option>
                      <option v-for="field in selected.field_options" :key="field.item_no" :value="field.item_no">{{ field.item_no }}. {{ field.title }}</option>
                    </select>
                    <el-button type="text" @click="draft.field_assignments.splice(index,1)">删除此段</el-button>
                  </div>
                  <el-button size="mini" :disabled="draft.field_assignments.length>=50" @click="draft.field_assignments.push({text:'',target_item_no:null})">添加下一段</el-button>
                  <el-button size="mini" @click="draft.field_assignments=null;draft.all_text_verified=false">改为单字段</el-button>
                  <el-checkbox v-model="draft.all_text_verified">已核对各段完整覆盖上方正文，且归属正确</el-checkbox>
                </template>
              </template>
              <template v-if="draft.action === 'correct_table'">
                <p>定义完整表格，包括空白格。行、列从1开始；合并格只保留起始单元格。</p>
                <el-select v-if="draft.tables.length" v-model="tableCursor" placeholder="选择区域内的独立表格"><el-option v-for="(entry, index) in draft.tables" :key="entry.table_index" :value="index" :label="`原页表格 ${entry.table_index + 1}（共 ${draft.tables.length} 个，需全部核对）`" /></el-select>
                <el-checkbox v-if="draft.tables.length" v-model="draft.tables[tableCursor].verified">已逐格核对当前独立表格</el-checkbox>
                <label>行数 <el-input-number v-model="editingTable.row_count" :min="1" :max="200" size="mini" /></label>
                <label>列数 <el-input-number v-model="editingTable.column_count" :min="1" :max="200" size="mini" /></label>
                <el-table :data="editingTable.cells" max-height="300" size="mini">
                  <el-table-column label="行 / 列" width="140"><template slot-scope="s"><input :value="s.row.row + 1" type="number" min="1" aria-label="单元格行" @input="s.row.row = Number($event.target.value) - 1" /><input :value="s.row.column + 1" type="number" min="1" aria-label="单元格列" @input="s.row.column = Number($event.target.value) - 1" /></template></el-table-column>
                  <el-table-column label="跨行 / 跨列" width="140"><template slot-scope="s"><input v-model.number="s.row.rowspan" type="number" min="1" aria-label="跨行数" /><input v-model.number="s.row.colspan" type="number" min="1" aria-label="跨列数" /></template></el-table-column>
                  <el-table-column label="内容"><template slot-scope="s"><el-input v-model="s.row.text" type="textarea" :rows="2" /></template></el-table-column>
                  <el-table-column width="65"><template slot-scope="s"><el-button type="text" @click="editingTable.cells.splice(s.$index, 1)">删除格</el-button></template></el-table-column>
                </el-table>
                <el-button size="mini" @click="editingTable.cells.push({row:0,column:0,rowspan:1,colspan:1,text:''})">添加单元格</el-button>
                <p>保存时服务端检查缺格、重叠及越界，不能用确认跳过缺失内容。</p>
                <template v-if="selected.repair_bbox_pdf">
                  <el-alert title="此问题跨越表格边界：请核对高亮完整区域，表格填在上方，表外文字填在下方，不要重复填写。" type="warning" :closable="false" />
                  <el-input v-model="draft.outside_text" type="textarea" :rows="4" placeholder="完整区域内的表外文字；确实没有时留空并勾选核对" />
                  <el-checkbox v-model="draft.outside_text_verified">已对照原页核对全部表外文字（包括确实为空）</el-checkbox>
                  <el-checkbox v-model="draft.numeric_text_verified">已逐字核对本区域表外全部数值、单位及符号；区域外问题仍保留</el-checkbox>
                </template>
              </template>
                <details v-if="['correct_text','correct_table'].includes(draft.action) && relatedRepairIssues.length">
                  <summary>本次完整修订范围内的其他问题（须逐项核对，不自动解除）</summary>
                  <div v-for="issue in relatedRepairIssues" :key="issue.issue_key">
                    <el-checkbox :value="draft.related_issues.some(entry=>entry.issue_key===issue.issue_key)" @change="toggleRelatedIssue(issue.issue_key,$event)">{{ issue.message }}（{{ issue.code }}）</el-checkbox>
                    <el-input v-if="draft.related_issues.some(entry=>entry.issue_key===issue.issue_key)" v-model="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).reason" type="textarea" :rows="2" maxlength="2000" placeholder="逐项说明：本次补录或修订如何解决该问题" />
                    <template v-if="issue.field_options && draft.related_issues.some(entry=>entry.issue_key===issue.issue_key)">
                      <el-checkbox v-if="expandedTableReviewAvailable(issue)" :value="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).review_scope==='complete_repair_region'" @change="setRelatedReviewScope(issue.issue_key,$event)">旧问题框只覆盖局部；改为逐字核对高亮完整区域的全部表格字段及表外正文</el-checkbox>
                      <select v-if="!draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_assignments" v-model.number="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).target_item_no" aria-label="关联问题所属字段">
                        <option :value="null">请选择，不自动猜测归属</option>
                        <option v-for="option in issue.field_options" :key="option.item_no" :value="option.item_no">{{ option.item_no }}. {{ option.title }}</option>
                      </select>
                      <el-input v-model="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).reviewed_text" type="textarea" :rows="2" maxlength="100000" placeholder="从本次修订正文复制该问题区域对应的完整文字；须唯一属于所选字段" />
                      <el-button v-if="!draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_assignments" type="text" @click="splitRelatedIssue(issue.issue_key,true)">关联区域包含多个字段，逐段分配</el-button>
                      <template v-else>
                        <p>上方保留整个问题区域正文；下方按原文顺序逐段填写并选择归属。每段须在本次完整修订中唯一对应，不能遗漏、重复或改动段内空白。</p>
                        <div v-for="(part,index) in draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_assignments" :key="index" class="related-field-part">
                          <select v-if="mixedTableReviewAvailable(issue)" v-model="part.source_kind" :aria-label="`关联区域第${index+1}段来源类型`" @change="part.source_kind==='outside_text' && (part.table_index=null)">
                            <option :value="null">明确选择文字来源类型</option>
                            <option value="table">表格内文字</option>
                            <option value="outside_text">完整表外正文中的文字</option>
                          </select>
                          <select v-if="(relatedTableSources(issue).length>1 || mixedTableReviewAvailable(issue)) && part.source_kind!=='outside_text'" v-model.number="part.table_index" :aria-label="`关联区域第${index+1}段来源表格`">
                            <option :value="null">明确选择原页来源表格</option>
                            <option v-for="source in relatedTableSources(issue)" :key="source.index" :value="source.index">原页表格 {{ source.index+1 }} · {{ source.box.join(', ') }}</option>
                          </select>
                          <el-input v-model="part.reviewed_text" type="textarea" :rows="2" :placeholder="`关联区域第${index+1}段完整文字`" />
                          <select v-model.number="part.target_item_no" :aria-label="`关联区域第${index+1}段所属字段`">
                            <option :value="null">请选择，不自动猜测归属</option>
                            <option v-for="option in issue.field_options" :key="option.item_no" :value="option.item_no">{{ option.item_no }}. {{ option.title }}</option>
                          </select>
                          <el-button type="text" :disabled="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_assignments.length<=2" @click="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_assignments.splice(index,1)">删除关联段</el-button>
                        </div>
                        <el-button type="text" :disabled="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_assignments.length>=50" @click="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_assignments.push({target_item_no:null,reviewed_text:'',table_index:null,source_kind:null})">添加关联下一段</el-button>
                        <el-button type="text" @click="splitRelatedIssue(issue.issue_key,false)">关联区域改为单字段</el-button>
                      </template>
                      <el-checkbox v-model="draft.related_issues.find(entry=>entry.issue_key===issue.issue_key).field_text_verified">已对照原页核对整个问题区域的文字和字段归属</el-checkbox>
                    </template>
                  </div>
                </details>
              <el-select v-if="draft.action === 'irrelevant_region'" v-model="draft.non_text_kind" placeholder="非文字区域类型"><el-option label="装饰" value="decoration" /><el-option label="无正文印章区域" value="stamp" /><el-option label="空白" value="blank" /></el-select>
              <el-input v-model="draft.reason" type="textarea" :rows="3" maxlength="2000" placeholder="必填：说明本项核对依据；缺失文字不能只点确认" />
              <div class="toolbar"><el-button type="primary" :loading="saving" :disabled="!dirty || !draft.action || !draft.reason.trim() || !preview || imageLoading" @click="save">保存本项修订</el-button><span>{{ dirty ? '有未保存修改' : '已保存 / 未修改' }}</span></div>
            </template>
          </template>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script>
import { getFilingParseReadiness, getFilingParseReview, getFilingParseReviewPage, saveFilingParseReview } from '@/api/filingChangeReview';
import { recoveryEvidence } from '@/utils/ocrRecoveryEvidence';
const clone = value => JSON.parse(JSON.stringify(value));
const emptyDraft = () => ({ action:'', reason:'', text:'', target_item_no:null, continuation_item_no:null, field_assignments:null, all_text_verified:false, numeric_text_verified:false, non_text_kind:'', outside_text:'', outside_text_verified:false, related_issues:[], tables:[], table:{row_count:1,column_count:1,cells:[{row:0,column:0,rowspan:1,colspan:1,text:''}]} });
export default {
  name:'ParseReviewPanel',
  props:{ projectId:{type:String,required:true} },
  data() { return {visible:false,readiness:null,sourceTarget:null,blockingIndex:1,context:null,selectedKey:'',tableCursor:0,evidencePageIndex:1,evidenceFocus:null,draft:emptyDraft(),snapshot:JSON.stringify(emptyDraft()),loading:false,saving:false,error:'',preview:null,imageError:'',imageLoading:false,epoch:0,imageEpoch:0,disposed:false}; },
  computed:{
    fieldTouchesTable() {
      if(!this.selected || !this.selected.field_options) return false;
      const b=this.selected.bbox_pdf;
      return Array.isArray(b) && b.length===4 && b.every(Number.isFinite)
        && (this.sourceChunk.tables || []).some(t=>Array.isArray(t.bbox_pdf) && t.bbox_pdf.length===4
          && b[0]<t.bbox_pdf[2] && b[2]>t.bbox_pdf[0] && b[1]<t.bbox_pdf[3] && b[3]>t.bbox_pdf[1]);
    },
    tableRepairChoices() {
      if(!this.fieldTouchesTable || this.context.editable===false || this.selected.resolved) return [];
      const b=this.selected.bbox_pdf;
      return this.context.issues.filter(issue=>{
        const box=issue.repair_bbox_pdf || issue.bbox_pdf;
        return issue.issue_key!==this.selectedKey && issue.chunk_index===this.selected.chunk_index
          && (!issue.resolved || (this.context.items || []).some(item=>item.issue_key===issue.issue_key))
          && (issue.repair_bbox_pdf || ['table_review_required','table_structure_unresolved'].includes(issue.code))
          && Array.isArray(box) && box.length===4 && box.every(Number.isFinite)
          && b[0]>=box[0] && b[1]>=box[1] && b[2]<=box[2] && b[3]<=box[3];
      });
    },
    relatedRepairIssues() {
      if(!this.selected || !this.context) return [];
      const box=this.selected.repair_bbox_pdf || this.selected.bbox_pdf;
      if(!Array.isArray(box) || box.length!==4) return [];
      return this.context.issues.filter(issue=>{
        const b=issue.bbox_pdf, saved=this.draft.related_issues.some(entry=>entry.issue_key===issue.issue_key);
        return issue.issue_key!==this.selectedKey && (!issue.resolved || saved) && issue.chunk_index===this.selected.chunk_index
          && (['ocr_quality','ocr_coverage','ocr_empty','text_assembly_unresolved'].includes(issue.code)
            || (this.context.source_kind==='application_form' && (this.draft.action==='correct_text'
              || (this.draft.action==='correct_table'
                && Array.isArray(b) && b.length===4 && b.every(Number.isFinite)
                && ((this.selected.repair_bbox_pdf && !((this.context.original_chunks[this.selected.chunk_index] || {}).tables || []).some(t=>
                  Array.isArray(t.bbox_pdf) && b[0]<t.bbox_pdf[2] && b[2]>t.bbox_pdf[0] && b[1]<t.bbox_pdf[3] && b[3]>t.bbox_pdf[1]))
                  || ((this.context.original_chunks[this.selected.chunk_index] || {}).tables || []).some(t=>
                    Array.isArray(t.bbox_pdf) && b[0]>=t.bbox_pdf[0] && b[1]>=t.bbox_pdf[1] && b[2]<=t.bbox_pdf[2] && b[3]<=t.bbox_pdf[3])
                  || this.crossTableReviewAvailable(issue) || this.mixedTableReviewAvailable(issue) || this.expandedTableReviewAvailable(issue))))
              && ['field_region_crossing','field_region_unassigned'].includes(issue.code)))
          && Array.isArray(b) && b.length===4 && b.every(Number.isFinite)
          && b[0]>=box[0] && b[1]>=box[1] && b[2]<=box[2] && b[3]<=box[3];
      });
    },
    editingTable() { return ((this.draft.tables || [])[this.tableCursor] || {}).table || this.draft.table; },
    dirty() { return JSON.stringify(this.draft) !== this.snapshot; },
    blockingPage() { return ((this.readiness || {}).blocking_issues || []).slice((this.blockingIndex-1)*20,this.blockingIndex*20); },
    selected() { return this.context && this.context.issues.find(i => i.issue_key === this.selectedKey); },
    sourceChunk() { return this.context && this.selected && this.context.original_chunks[this.selected.chunk_index] || {}; },
    recoveryRows() { return recoveryEvidence(this.sourceChunk, this.selected && this.selected.code); },
    recoveryPage() { return this.recoveryRows.slice((this.evidencePageIndex-1)*20,this.evidencePageIndex*20); },
    actions() {
      if (!this.selected || this.context.editable === false || !Number.isInteger(this.selected.chunk_index)) return [];
      if(this.selected.resolved && !(this.context.items || []).some(i=>i.issue_key===this.selectedKey)) return [];
      const code=this.selected.code, result=[];
      if(code==='ocr_quality' && this.selected.confirm_allowed === true) result.push({value:'confirm',label:'原文已完整且正确，逐项确认'});
      if(this.selected.field_options && this.context.source_kind==='application_form' && !this.fieldTouchesTable) result.push({value:'correct_text',label:'修订文字并明确所属字段'});
      if(code==='numeric_uncertain' && this.selected.numeric_text_review_allowed===true) result.push({value:'correct_text',label:'完整修订正文并核对数值'});
      if(['ocr_quality','ocr_coverage','ocr_empty','text_assembly_unresolved'].includes(code) && !this.selected.repair_bbox_pdf) result.push({value:'correct_text',label:'补录或修订区域文字'});
      if(['table_review_required','table_structure_unresolved'].includes(code) || this.selected.repair_bbox_pdf) result.push({value:'correct_table',label:'修订表格结构与内容'});
      if(code==='ocr_coverage') result.push({value:'irrelevant_region',label:'确认确属无正文的非文字区域'});
      return result;
    },
    regionStyle() {
      const b=this.evidenceFocus || (this.selected && (this.selected.repair_bbox_pdf || this.selected.bbox_pdf)), p=this.preview && this.preview.page_bbox;
      if(!b || b.length!==4 || !p || p[2]<=p[0] || p[3]<=p[1]) return null;
      return {left:`${100*(b[0]-p[0])/(p[2]-p[0])}%`,top:`${100*(b[1]-p[1])/(p[3]-p[1])}%`,width:`${100*(b[2]-b[0])/(p[2]-p[0])}%`,height:`${100*(b[3]-b[1])/(p[3]-p[1])}%`};
    },
  },
  watch:{
    dirty(value) { this.$emit('dirty-change',value || this.saving); },
    saving(value) { this.$emit('dirty-change',value || this.dirty); },
    projectId() { this.epoch++; this.imageEpoch++; this.visible=false; this.context=null; this.readiness=null; this.preview=null; this.draft=emptyDraft(); this.snapshot=JSON.stringify(this.draft); this.loading=false; this.saving=false; this.$emit('dirty-change',false); },
  },
  beforeDestroy() { this.disposed=true; this.epoch++; this.imageEpoch++; },
  methods:{
    expandedTableReviewAvailable(issue) {
      const b=issue && issue.bbox_pdf,scope=this.selected && this.selected.repair_bbox_pdf;
      if(!this.context || this.context.source_kind!=='application_form' || this.draft.action!=='correct_table'
        || !Array.isArray(b) || !Array.isArray(scope) || b.length!==4 || scope.length!==4
        || !b.every(Number.isFinite) || !scope.every(Number.isFinite)
        || b.every((value,index)=>value===scope[index])
        || b[0]<scope[0] || b[1]<scope[1] || b[2]>scope[2] || b[3]>scope[3]) return false;
      const tables=(((this.context.original_chunks || [])[issue.chunk_index] || {}).tables || []).filter(t=>
        Array.isArray(t.bbox_pdf) && t.bbox_pdf.length===4 && t.bbox_pdf.every(Number.isFinite)
        && scope[0]<t.bbox_pdf[2] && scope[2]>t.bbox_pdf[0] && scope[1]<t.bbox_pdf[3] && scope[3]>t.bbox_pdf[1]);
      return tables.length>0 && tables.every(({bbox_pdf:t})=>scope[0]<=t[0] && scope[1]<=t[1] && scope[2]>=t[2] && scope[3]>=t[3]);
    },
    setRelatedReviewScope(key, expanded) {
      const issue=this.context && this.context.issues.find(i=>i.issue_key===key);
      if(expanded && !this.expandedTableReviewAvailable(issue)) return;
      this.draft.related_issues=this.draft.related_issues.map(entry=>entry.issue_key!==key ? entry : {
        ...entry,review_scope:expanded?'complete_repair_region':null,field_text_verified:false,
      });
      if(expanded && !this.draft.related_issues.find(entry=>entry.issue_key===key).field_assignments) this.splitRelatedIssue(key,true);
    },
    mixedTableReviewAvailable(issue) {
      const expanded=this.draft.related_issues.some(entry=>entry.issue_key===issue.issue_key && entry.review_scope==='complete_repair_region');
      if(expanded && !this.expandedTableReviewAvailable(issue)) return false;
      const scope=this.selected && this.selected.repair_bbox_pdf,b=expanded ? scope : issue && issue.bbox_pdf;
      const sources=this.relatedTableSources(issue);
      return Array.isArray(b) && Array.isArray(scope) && b.length===4 && scope.length===4
        && b.every((value,index)=>Number.isFinite(value) && value===scope[index]) && sources.length>0
        && sources.every(({box})=>b[0]<=box[0] && b[1]<=box[1] && b[2]>=box[2] && b[3]>=box[3]);
    },
    relatedTableSources(issue) {
      const expanded=issue && this.draft.related_issues.some(entry=>entry.issue_key===issue.issue_key && entry.review_scope==='complete_repair_region');
      const b=expanded && this.expandedTableReviewAvailable(issue) ? this.selected.repair_bbox_pdf : issue && issue.bbox_pdf;
      if(!this.context || !Array.isArray(b) || b.length!==4 || !b.every(Number.isFinite)) return [];
      return (((this.context.original_chunks || [])[issue.chunk_index] || {}).tables || [])
        .map((table,index)=>({index,box:table.bbox_pdf}))
        .filter(source=>Array.isArray(source.box) && source.box.length===4 && source.box.every(Number.isFinite)
          && b[0]<source.box[2] && b[2]>source.box[0] && b[1]<source.box[3] && b[3]>source.box[1]);
    },
    crossTableReviewAvailable(issue) {
      const sources=this.relatedTableSources(issue),b=issue.bbox_pdf;
      return sources.length>1 && sources.every(({box})=>b[0]<=box[0] && b[1]<=box[1] && b[2]>=box[2] && b[3]>=box[3]);
    },
    splitRelatedIssue(key, multiple) {
      this.draft.related_issues=this.draft.related_issues.map(entry=>entry.issue_key!==key ? entry : {
        ...entry, target_item_no:null, field_text_verified:false,
        field_assignments:multiple ? [{target_item_no:null,reviewed_text:entry.reviewed_text || '',table_index:null,source_kind:null},{target_item_no:null,reviewed_text:'',table_index:null,source_kind:null}] : null,
      });
    },
    toggleRelatedIssue(key, checked) {
      if(!this.relatedRepairIssues.some(issue=>issue.issue_key===key)) return;
      this.draft.related_issues=this.draft.related_issues.filter(entry=>entry.issue_key!==key);
      if(checked) this.draft.related_issues.push({issue_key:key,reason:'',target_item_no:null,reviewed_text:'',field_text_verified:false});
    },
    valid(epoch, project) { return !this.disposed && epoch===this.epoch && project===this.projectId; },
    async canDiscard() {
      if(this.saving) { this.$message.warning('正在保存，请等待结果后再切换。'); return false; }
      if(!this.dirty) return true;
      try { await this.$confirm('解析核对有未保存修改，是否放弃本项输入？','未保存修改',{type:'warning'}); return true; } catch(_) { return false; }
    },
    async close(done) { if(!await this.canDiscard()) return; this.epoch++; this.imageEpoch++; this.draft=emptyDraft(); this.snapshot=JSON.stringify(this.draft); this.context=null; this.preview=null; this.loading=false; this.imageLoading=false; if(done) done(); else this.visible=false; },
    async open() {
      if(!await this.canDiscard()) return;
      const project=this.projectId, epoch=++this.epoch;
      this.visible=true; this.loading=true; this.error='';
      this.sourceTarget=null;
      this.context=null; this.preview=null; this.imageEpoch++; this.draft=emptyDraft(); this.snapshot=JSON.stringify(this.draft);
      try { const res=await getFilingParseReadiness(project); if(this.valid(epoch,project)) { this.readiness=res.data; this.blockingIndex=1; } }
      catch(e) { if(this.valid(epoch,project)) this.error=e.message || '读取解析状态失败，请重试'; }
      finally { if(this.valid(epoch,project)) this.loading=false; }
    },
    async openSource(kind, docId) { return this.loadSource({source_kind:kind,doc_id:docId}, true); },
    async loadSource(issue, direct = false) {
      if(!await this.canDiscard()) return;
      if(!['application_form','submission','reference'].includes(issue.source_kind) || !issue.doc_id) { this.error='该项请在申请表或资料目录中补齐、重新解析；暂不能在区域编辑器中处理。'; return; }
      const project=this.projectId, epoch=++this.epoch;
      if(direct === true) { this.visible=true; this.sourceTarget={source_kind:issue.source_kind,doc_id:issue.doc_id}; this.readiness=null; }
      this.loading=true; this.error=''; this.imageEpoch++; this.preview=null;
      this.context=null; this.draft=emptyDraft(); this.snapshot=JSON.stringify(this.draft);
      try { const res=await getFilingParseReview(project,issue.source_kind,issue.doc_id); if(!this.valid(epoch,project)) return; this.context=res.data; this.draft=emptyDraft(); this.snapshot=JSON.stringify(this.draft); const first=this.context.issues.find(i=>!i.resolved) || this.context.issues[0]; this.setIssue(issue.issue_key || (first && first.issue_key) || ''); }
      catch(e) { if(this.valid(epoch,project)) this.error=e.message || '读取核对内容失败'; }
      finally { if(this.valid(epoch,project)) this.loading=false; }
    },
    async selectIssue(key) { if(await this.canDiscard()) this.setIssue(key); },
    setIssue(key) {
      this.evidencePageIndex=1; this.evidenceFocus=null;
      this.tableCursor=0;
      this.selectedKey=key; this.error=''; const saved=(this.context.items || []).find(i=>i.issue_key===key);
      this.draft={...emptyDraft(),...clone(saved || {})};
      if(!saved && this.selected) {
        const ti=Number.isInteger(this.selected.repair_table_index) ? this.selected.repair_table_index : this.selected.table_index;
        const table=(this.sourceChunk.tables || [])[ti] || (this.sourceChunk.tables || []).find(t=>JSON.stringify(t.bbox_pdf)===JSON.stringify(this.selected.bbox_pdf));
        if(table && table.cells && table.cells.length) this.draft.table={row_count:Math.max(...table.cells.map(c=>c.row+c.rowspan)),column_count:Math.max(...table.cells.map(c=>c.column+c.colspan)),cells:clone(table.cells).map(c=>({row:c.row,column:c.column,rowspan:c.rowspan,colspan:c.colspan,text:c.text}))};
        if(this.selected.repair_table_indices) this.draft.tables=this.selected.repair_table_indices.map(index=>{
          const cells=clone(((this.sourceChunk.tables || [])[index] || {}).cells || []);
          return {table_index:index,verified:false,table:cells.length ? {row_count:Math.max(...cells.map(c=>c.row+c.rowspan)),column_count:Math.max(...cells.map(c=>c.column+c.colspan)),cells:cells.map(c=>({row:c.row,column:c.column,rowspan:c.rowspan,colspan:c.colspan,text:c.text}))} : emptyDraft().table};
        });
      }
      this.snapshot=JSON.stringify(this.draft); this.loadImage();
    },
    async loadImage() {
      const context=this.context, selected=this.selected, project=this.projectId, epoch=this.epoch, imageEpoch=++this.imageEpoch;
      this.preview=null; this.imageError=''; this.imageLoading=false;
      if(!context || !selected || !selected.page) { this.imageError='没有可靠页码，请查看原件并重新解析。'; return; }
      this.imageLoading=true;
      try { const res=await getFilingParseReviewPage(project,context.source_kind,context.doc_id,selected.page,context.source_identity); if(this.valid(epoch,project) && imageEpoch===this.imageEpoch) this.preview=res.data; }
      catch(e) { if(this.valid(epoch,project) && imageEpoch===this.imageEpoch) this.imageError=e.message || '原页读取失败'; }
      finally { if(this.valid(epoch,project) && imageEpoch===this.imageEpoch) this.imageLoading=false; }
    },
    async revoke(all) {
      if(this.saving || this.loading || this.dirty || !this.context || !(this.context.items || []).length) return;
      const project=this.projectId, epoch=this.epoch, context=this.context, key=this.selectedKey;
      const before=JSON.stringify(this.draft);
      try { await this.$confirm(all ? '撤销本文件全部已保存的区域修订？原件和历史记录保留，未处理问题将重新阻塞审评。' : '撤销本项已保存修订？原件和历史记录保留，相关问题将重新核查。','确认撤销',{type:'warning'}); }
      catch(_) { return; }
      if(!this.valid(epoch,project) || this.context!==context || this.saving || this.dirty) return;
      const items=all ? [] : context.items.filter(i=>i.issue_key!==key);
      this.saving=true; this.error='';
      try {
        const res=await saveFilingParseReview(project,context.source_kind,context.doc_id,{source_identity:context.source_identity,expected_revision:context.revision,items});
        if(!this.valid(epoch,project)) return;
        const resolved=res.data.resolved_issue_keys || [];
        this.context={...context,revision:res.data.revision,revision_error:null,items,issues:context.issues.map(i=>({...i,resolved:resolved.includes(i.issue_key)}))};
        if(JSON.stringify(this.draft)===before) this.setIssue(key);
        this.$message.success('修订已撤销，原件和历史记录保留'); this.$emit('saved');
        try { const status=await getFilingParseReadiness(project); if(this.valid(epoch,project)) this.readiness=status.data; }
        catch(_) { if(this.valid(epoch,project)) this.error='撤销已保存，但就绪状态刷新失败，请重新检查。'; }
      } catch(e) { if(this.valid(epoch,project)) this.error=e.message || '撤销失败，原修订仍保留'; }
      finally { if(this.valid(epoch,project)) this.saving=false; }
    },
    async save() {
      if(this.saving || !this.context || !this.selected || !this.preview) return;
      const project=this.projectId, epoch=this.epoch, context=this.context, key=this.selectedKey;
      const submitted=clone(this.draft), submittedSnapshot=JSON.stringify(this.draft);
      const items=(context.items || []).filter(i=>i.issue_key!==key).concat([{...submitted,issue_key:key}]);
      this.saving=true; this.error='';
      try {
        const res=await saveFilingParseReview(project,context.source_kind,context.doc_id,{source_identity:context.source_identity,expected_revision:context.revision,items});
        if(!this.valid(epoch,project)) return;
        const resolved=res.data.resolved_issue_keys || [key];
        this.context={...context,revision:res.data.revision,revision_error:null,items,issues:context.issues.map(i=>({...i,resolved:resolved.includes(i.issue_key)}))};
        // 保存期间继续输入保留为新草稿，不能被成功回包清除。
        this.snapshot=submittedSnapshot;
        this.$message.success('修订已保存；原始解析状态和历史报告保留'); this.$emit('saved');
        try { const status=await getFilingParseReadiness(project); if(this.valid(epoch,project)) this.readiness=status.data; }
        catch(_) { if(this.valid(epoch,project)) this.error='修订已保存，但就绪状态刷新失败，请重新检查。'; }
      } catch(e) { if(this.valid(epoch,project)) this.error=e.message || '保存失败，当前输入已保留'; }
      finally { if(this.valid(epoch,project)) this.saving=false; }
    },
  },
};
</script>
<style scoped>
.parse-review-panel {padding:16px;overflow:auto;height:calc(100vh - 100px);box-sizing:border-box;}
.toolbar {display:flex;gap:12px;align-items:center;margin:12px 0;}
.editor {display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:16px;}
.original,.correction {min-width:0;}
.image-frame {position:relative;line-height:0;}
.image-frame img {width:100%;height:auto;}
.region {position:absolute;border:2px solid #e65100;background:rgba(255,160,0,.12);box-sizing:border-box;pointer-events:none;}
pre {white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto;}
.correction .el-textarea,.correction .el-select {margin:8px 0;}
.field-target {width:100%;max-width:420px;min-height:40px;margin:8px 8px 8px 0;padding:8px 12px;border:1px solid #dcdfe6;border-radius:4px;background:#fff;color:#606266;font:inherit;}
.field-target:focus {outline:2px solid #409eff;outline-offset:1px;}
input[type=number] {width:48px;margin:2px;}
@media(max-width:900px) {.editor {grid-template-columns:1fr;}}
</style>
