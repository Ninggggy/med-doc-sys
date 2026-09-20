<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">备案变更审评会话</h2>
        <div class="page-desc">项目：{{ project.project_name || "-" }}</div>
        <div class="page-desc">任务状态：{{ reviewTask.status || "idle" }} {{ reviewTask.message ? `| ${reviewTask.message}` : "" }}</div>
      </div>
      <div>
        <el-button @click="$refs.parseReviewPanel.open()">解析就绪检查 / 去核对</el-button>
        <el-button type="primary" :loading="running" :disabled="running || parseReviewDirty" @click="runReview">启动 AI 审评</el-button>
      </div>
    </div>

    <div v-for="(state, key) in nonFormParseTaskStates" :key="key" class="parse-summary">
      <el-alert :title="parseAttemptLabel(state)" :type="parseAttemptType(state)" :closable="false" show-icon />
      <el-button type="text" @click="openParseDetails(state, key)">查看详情</el-button>
    </div>
    <el-tabs v-model="activeTab">
      <el-tab-pane label="申请表" name="form">
        <el-alert v-if="formParseResolution.revision_error" :title="formParseResolution.revision_error.message" type="warning" :closable="false" />
        <el-alert v-else-if="formParseResolution.revision" :title="`原始解析状态保留；${formParseResolution.manually_reviewed ? '人工核对已完成' : '人工核对尚未完成'}（修订${formParseResolution.revision}）`" :type="formParseResolution.manually_reviewed ? 'success' : 'warning'" :closable="false" />
        <div v-if="currentFormParseState" class="parse-summary">
          <el-alert :title="parseAttemptLabel(currentFormParseState)" :closable="false" :type="parseAttemptType(currentFormParseState)" />
          <el-button type="text" @click="openParseDetails(currentFormParseState, '申请表本次解析')">查看详情</el-button>
        </div>
        <details v-if="((formParseAttempt.parse_diagnostics || {}).form_content_regions || []).length && formParseAttempt.content_status === 'failed'" class="form-attempt-evidence">
          <summary>查看本次未采用内容及位置（不属于此前有效结果）</summary>
          <div v-for="(region, ri) in formParseAttempt.parse_diagnostics.form_content_regions" :key="'attempt-region-' + ri">
            {{ region.text }} · {{ region.page ? '第 ' + region.page + ' 页 ' + region.bbox_pdf : region.coordinate_unit + ' ' + (region.table || region.paragraph || region.part || '') }} · {{ region.reason }}
          </div>
        </details>
        <el-card>
          <div slot="header">{{ formSchema.original_form_type || '药品注册申请表' }}</div>
          <div style="margin-bottom: 12px; display: flex; gap: 10px">
            <el-button type="primary" plain @click="formVisible = !formVisible">{{ formVisible ? "收起申请表" : "填写申请表" }}</el-button>
            <el-upload
              action="#"
              accept=".doc,.docx,.pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/pdf"
              :auto-upload="false"
              :show-file-list="false"
              :disabled="importingForm"
              :on-change="onFormFileChange"
            >
              <el-button :loading="importingForm">上传 Word/PDF 申请表并解析填充</el-button>
            </el-upload>
            <el-button type="primary" :loading="savingForm" @click="saveForm">保存申请表</el-button>
            <el-button :loading="importingForm" @click="reparseForm">重新解析申请表</el-button>
            <el-button :disabled="importingForm || savingForm || !formSource.source_file_id" @click="$refs.parseReviewPanel.openSource('application_form', formSource.source_file_id)">核对申请表原页</el-button>
          </div>
          <div class="meta-line">文件解析：{{ parseContentLabel(formParseStatus) }}。{{ formSource.source_file_name ? '有效原件：' + formSource.source_file_name : '尚无有效解析原件，人工填写内容可保存和使用。' }}</div>
          <div v-if="formSource.parse_diagnostics" class="meta-line">{{ parseDiagnosticsLabel(formSource.parse_diagnostics) }} <el-button type="text" @click="openParseDetails(attemptState(formSource), '申请表有效结果')">查看详情</el-button></div>
          <div v-if="((formSource.parse_diagnostics || {}).errors || []).some(x => x.text)" class="meta-line">诊断原文、页码和区域保留在上方详情中；需修订时请选择“核对申请表原页”。</div>
          <div class="meta-line">原表类型：{{ formSchema.original_form_type || "未识别" }}。保存仅记录修改，不代表所有字段已核实。</div>
          <div v-for="(note, ni) in formLegacyRoleNotes" :key="'legacy-role-' + ni" class="meta-line">{{ note.reason }} 原值：{{ note.value }}</div>
          <details v-if="(formSchema.unassigned_party_sources || []).length">
            <summary>主体角色待核对：查看未归属来源</summary>
            <div v-for="(candidate, ci) in formSchema.unassigned_party_sources" :key="ci">{{ candidate.status }} · {{ candidate.source_file }}<pre style="white-space: pre-wrap">{{ candidate.source_text }}</pre></div>
          </details>
          <el-collapse v-if="formVisible">
            <el-collapse-item v-for="(field, key) in renderFields" :key="key" :name="key">
              <template slot="title">{{ displayFieldTitle(key, field) }} · {{ formFieldStatus(field) }}</template>
              <fieldset :disabled="importingForm" style="border: 0; padding: 0; min-width: 0">
                <template v-if="field.field_type === 'input'">
                  <el-input v-model="field.value" @input="markFormEdited(field, 'value')" />
                </template>
                <template v-else-if="field.field_type === 'textarea'">
                  <el-input v-model="field.value" @input="markFormEdited(field, 'value')" type="textarea" :rows="3" />
                </template>
                <template v-else-if="field.field_type === 'radio'">
                  <el-radio-group v-model="field.value" @input="markFormEdited(field, 'value')">
                    <el-radio v-for="opt in field.options || []" :key="opt" :label="opt">{{ opt }}</el-radio>
                  </el-radio-group>
                  <el-input v-for="(subVal, subKey) in field.sub_fields || {}" :key="`${key}-${subKey}`"
                    v-model="field.sub_fields[subKey]" @input="markFormEdited(field, 'sub_fields.' + subKey)"
                    :placeholder="formatSubFieldLabel(subKey)" style="margin-top: 8px" />
                </template>
                <template v-else-if="field.field_type === 'radio_with_input'">
                  <el-radio-group v-model="field.value" @input="markFormEdited(field, 'value')">
                    <el-radio v-for="opt in field.options || []" :key="opt" :label="opt">{{ opt }}</el-radio>
                  </el-radio-group>
                  <el-input
                    v-for="(subVal, subKey) in field.sub_fields || {}"
                    :key="`${key}-${subKey}`"
                    v-model="field.sub_fields[subKey]" @input="markFormEdited(field, 'sub_fields.' + subKey)"
                    :placeholder="formatSubFieldLabel(subKey)"
                    style="margin-top: 8px"
                  />
                </template>
                <template v-else-if="field.field_type === 'group_checkbox'">
                  <el-select v-model="field.selected_values" multiple filterable allow-create default-first-option @change="markFormEdited(field, 'selected_values')" placeholder="填写原文剂型">
                    <el-option v-for="opt in field.selected_values || []" :key="opt" :label="opt" :value="opt" />
                  </el-select>
                </template>
                <template v-else-if="field.field_type === 'checkbox'">
                  <el-checkbox-group v-model="field.selected_values" @change="markFormEdited(field, 'selected_values')">
                    <el-checkbox v-for="opt in field.options || []" :key="opt" :label="opt">{{ opt }}</el-checkbox>
                  </el-checkbox-group>
                </template>
                <template v-else-if="field.field_type === 'tree_checkbox'">
                  <el-checkbox-group v-model="field.selected_values" @change="markFormEdited(field, 'selected_values')">
                    <el-checkbox
                      v-for="opt in flattenTreeOptions(field.options || [])"
                      :key="`${key}-${opt.code}`"
                      :label="opt.code"
                    >
                      {{ opt.code }} {{ opt.label }}
                    </el-checkbox>
                  </el-checkbox-group>
                  <el-input
                    v-if="field.sub_fields && Object.prototype.hasOwnProperty.call(field.sub_fields, 'other_description')"
                    v-model="field.sub_fields.other_description" @input="markFormEdited(field, 'sub_fields.other_description')"
                    type="textarea"
                    :rows="2"
                    placeholder="其他事项说明"
                    style="margin-top: 8px"
                  />
                </template>
                <template v-else-if="field.field_type === 'group'">
                  <el-form label-width="180px" size="small">
                    <el-form-item v-for="(subVal, subKey) in field.sub_fields || {}" :key="`${key}-${subKey}`" :label="formatSubFieldLabel(subKey)">
                      <template v-if="typeof subVal === 'object' && subVal && subVal.field_type === 'radio'">
                        <el-radio-group v-model="field.sub_fields[subKey].value" @change="markFormEdited(field, 'sub_fields.' + subKey)">
                          <el-radio v-for="opt in subVal.options || []" :key="opt" :label="opt">{{ opt }}</el-radio>
                        </el-radio-group>
                      </template>
                      <template v-else>
                        <el-input v-model="field.sub_fields[subKey]" @input="markFormEdited(field, 'sub_fields.' + subKey)" />
                      </template>
                    </el-form-item>
                  </el-form>
                </template>
                <template v-else-if="field.field_type === 'table'">
                  <div style="margin-bottom: 8px">
                    <el-button size="mini" @click="addTableRow(field)">新增一行</el-button>
                  </div>
                  <el-table :data="field.table_rows || []" border size="mini">
                    <el-table-column v-for="col in tableColumns(field)" :key="`${key}-${col}`" :label="formatSubFieldLabel(col)" min-width="130">
                      <template slot-scope="scope">
                        <el-input v-model="scope.row[col]" @input="markFormEdited(field, 'table_rows')" size="mini" />
                      </template>
                    </el-table-column>
                  </el-table>
                </template>
                <template v-else>
                  <el-input v-model="field.value" @input="markFormEdited(field, 'value')" type="textarea" :rows="2" />
                </template>
                <div class="meta-line">{{ formFieldStatus(field) }}</div>
                <div v-if="field.reported_value">本表填写有效期：{{ field.reported_value }}（变更前后关系需结合原文核对）</div>
                <details v-if="field.source_text || field.source_regions || field.value_sources"><summary>查看申请表原文来源</summary>
                  <div>{{ field.source_file || (field.manual_modified ? '当前值含人工修订，请查看逐项来源' : '申请表') }}</div><pre style="white-space: pre-wrap">{{ field.source_text || '该字段未识别到文字' }}</pre>
                  <div v-for="(region, ri) in field.source_regions || []" :key="ri">{{ region.page ? '第 ' + region.page + ' 页' : 'Word 表格 ' + region.table }} {{ region.bbox_pdf || '' }}</div>
                  <details v-if="field.recognition_evidence && field.recognition_evidence.length"><summary>查看局部识别及纠正依据</summary>
                    <div v-for="(evidence, ei) in field.recognition_evidence" :key="ei">
                      <div v-if="evidence.row != null">原表第 {{ evidence.row + 1 }} 行，第 {{ evidence.column + 1 }} 列</div>
                      <pre style="white-space: pre-wrap">{{ evidence.source_text || '该区域未识别到文字' }}</pre>
                      <div>{{ evidence.source_regions }}</div>
                    </div>
                  </details>
                  <div v-for="(choice, oi) in field.visual_choices || []" :key="'choice-' + oi">
                    {{ choice.name }}：{{ choice.selected ? '图标已选中' : '图标未选中' }}<div>{{ choice.source_regions }}</div>
                  </div>
                  <div v-for="(source, path) in field.value_sources || {}" :key="path">
                    {{ formatSubFieldLabel(path.replace('sub_fields.', '')) }}：{{ recognitionLabel(source.recognition_status) }} · {{ source.source_file || (source.recognition_status === 'manual' ? '人工填写' : '申请表') }}
                    <div>当前值：{{ pretty(source.value) }}<span v-if="source.parse_confidence != null">；置信度：{{ source.parse_confidence }}</span></div>
                    <pre style="white-space: pre-wrap">{{ source.source_text }}</pre>
                    <div>{{ source.source_regions }}</div>
                    <div v-if="source.recognition_status === 'previous_result'" class="meta-line">此前识别结果（本次未取得）。{{ parseDiagnosticsLabel(source.reparse_diagnostics || {}) }} 请重试识别后核对。</div>
                    <div v-for="(error, ei) in ((source.reparse_diagnostics || {}).errors || [])" :key="'retry-' + ei">本次失败范围：第 {{ error.page }} 页 · {{ error.bbox_pdf || '整页或范围未返回' }} · {{ error.coordinate_unit || '' }} · {{ error.message }}</div>
                    <details v-if="source.previous_source"><summary>此前识别的原文依据</summary><div>{{ source.previous_source.source_file }}</div><pre style="white-space: pre-wrap">{{ source.previous_source.source_text }}</pre><div>{{ source.previous_source.source_regions }}</div></details>
                    <details v-if="source.edited_from"><summary>人工修订前的依据</summary><div>{{ source.edited_from.source_file }}</div><pre style="white-space: pre-wrap">{{ source.edited_from.source_text }}</pre></details>
                  </div>
                </details>
                <div v-if="field.original_matter && field.original_matter.length">原表事项：{{ field.original_matter.map(x => x.code + ' ' + x.name + (x.selected === true ? '（已选）' : x.selected === false ? '（未选）' : '（待核对）')).join('；') }}。{{ field.mapping_note }}</div>
                <div v-for="(candidate, path) in field.candidates || {}" :key="path" class="meta-line">
                  <strong>存在冲突：{{ formatSubFieldLabel(path.replace('sub_fields.', '')) }}</strong>
                  <pre style="white-space: pre-wrap">新候选：{{ pretty(candidate.value) }}（{{ recognitionLabel(candidate.recognition_status) }}）</pre>
                  <details><summary>查看候选原文来源</summary><div>{{ candidate.source_file || '重新解析的申请表' }}</div><pre style="white-space: pre-wrap">{{ candidate.source_text }}</pre><div>{{ candidate.source_regions }}</div></details>
                  <el-button size="mini" @click="resolveFormCandidate(key, path, 'adopt')">采用候选值</el-button>
                  <el-button size="mini" @click="resolveFormCandidate(key, path, 'keep')">保留人工值</el-button>
                </div>
                <details v-if="field.value_history && field.value_history.length"><summary>查看历史原件结果（仅供参考）</summary>
                  <div v-for="(history, hi) in field.value_history" :key="hi">{{ formatSubFieldLabel(history.path.replace('sub_fields.', '')) }} · {{ history.source_file || (history.recognition_status === 'manual' ? '人工填写' : '原件来源未记录') }} · {{ recognitionLabel(history.recognition_status) }}<pre style="white-space: pre-wrap">{{ pretty(history.value) }}</pre><pre style="white-space: pre-wrap">{{ history.source_text }}</pre><div>{{ history.source_regions }}</div></div>
                </details>
                <details v-for="(issue, si) in field.semantic_issues || []" :key="'semantic-' + si" class="meta-line">
                  <summary>{{ issue.reason }}</summary>
                  <div>{{ field.source_file }}</div><pre style="white-space: pre-wrap">{{ issue.source_text || pretty(issue.candidates) }}</pre>
                  <div>{{ issue.source_regions || field.source_regions }}</div>
                </details>
                <div v-for="(check, ci) in field.cross_checks || []" :key="'check-' + ci">
                  其他材料核对：{{ check.status }} · {{ check.source_file }}<pre style="white-space: pre-wrap">{{ check.source_text }}</pre>
                </div>
              </fieldset>
            </el-collapse-item>
          </el-collapse>
          <el-empty v-else description="点击“填写申请表”展开全部表单项，可上传文档解析后自动填充" />
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="申报资料" name="submission">
        <el-card>
          <div slot="header">申报资料上传</div>
          <el-row :gutter="12">
            <el-col :span="7">
              <el-card shadow="never">
                <div slot="header">标准目录</div>
                <el-tree
                  ref="submissionTree"
                  class="submission-catalog"
                  highlight-current
                  :current-node-key="selectedSubmissionCategory || null"
                  :data="submissionTreeData"
                  node-key="id"
                  default-expand-all
                  :expand-on-click-node="false"
                  @node-click="onSubmissionTreeNodeClick"
                >
                  <span slot-scope="{ data }">
                    <span>{{ data.label }}</span>
                    <span v-if="data.id === selectedSubmissionCategory" class="selected-marker">✓ 已选</span>
                    <el-tag v-if="data.required_level === 'required'" size="mini" type="danger" style="margin-left: 6px">必传</el-tag>
                    <el-tag v-else-if="data.required_level === 'recommended'" size="mini" style="margin-left: 6px">建议</el-tag>
                  </span>
                </el-tree>
                <div style="margin-top: 10px">
                  <el-input v-model="naReasonInput" type="textarea" :rows="2" placeholder="当前目录不适用原因（可选）" />
                  <el-button size="mini" style="margin-top: 8px" @click="markCategoryNotApplicable">设置不适用</el-button>
                </div>
              </el-card>
            </el-col>
            <el-col :span="17">
              <div class="upload-target">当前目录：{{ selectedSubmissionLabel }} <el-button v-if="selectedSubmissionCategory" type="text" @click="clearSubmissionCategory">清除选择</el-button></div>
              <div style="margin-bottom: 10px; display: flex; gap: 8px; align-items: center">
                <el-upload action="#" :auto-upload="false" :on-change="onSubmissionFileChange" :file-list="submissionFileList" multiple>
                  <el-button>上传文件/批量上传</el-button>
                </el-upload>
                <el-upload
                  action="#"
                  :auto-upload="false"
                  :show-file-list="false"
                  :on-change="onSubmissionFileChange"
                  :attrs="{ webkitdirectory: true, directory: true }"
                >
                  <el-button>上传文件夹</el-button>
                </el-upload>
                <el-button type="primary" :loading="uploading" @click="uploadSubmissions">上传到当前目录</el-button>
                <el-button :loading="parsingBatch" :disabled="hasRunningSubmissionParse" @click="batchParseSubmissions">批量解析</el-button>
                <el-button :loading="checkingCompleteness" @click="runCompletenessCheck">完整性检查</el-button>
                <el-button :loading="comparingSubmissions" @click="compareSelectedSubmissions">自动比对差异</el-button>
              </div>
              <el-table :data="filteredSubmissions" border @selection-change="onSubmissionSelectionChange">
                <el-table-column type="selection" width="50" />
                <el-table-column prop="file_name" label="文件名" min-width="220" />
                <el-table-column label="分类核对" min-width="160"><template slot-scope="s">{{ s.row.auto_classified ? '自动分类' : '人工分类优先' }}<div v-if="s.row.classification_difference">正文识别：{{ (s.row.automatic_categories || []).join('、') }}；与选择不同，请核对</div></template></el-table-column>
                <el-table-column prop="material_category" label="一级分类" width="90" />
                <el-table-column label="必传" width="70">
                  <template slot-scope="scope">{{ scope.row.required_flag ? "是" : "否" }}</template>
                </el-table-column>
                <el-table-column label="解析状态" min-width="220">
                  <template slot-scope="scope">
                    <el-tag size="mini" :type="parseStatusType(scope.row.parse_status)">{{ parseContentLabel(scope.row.parse_status) }}</el-tag>
                    <div v-if="(scope.row.latest_attempt || {}).content_status === 'failed'">本次失败；此前结果如存在仍保留，不能视为本次成功。</div>
                    <div>{{ parseDiagnosticsLabel(scope.row.parse_diagnostics) }}</div>
                    <el-button type="text" @click="openFileParseDetails(scope.row)">查看详情</el-button>
                  </template>
                </el-table-column>
                <el-table-column label="索引状态" width="90"><template slot-scope="s">{{ indexStatusLabel(s.row.index_status) }}</template></el-table-column>
                <el-table-column label="参与审评" width="100">
                  <template slot-scope="scope">
                    <el-switch :value="!!scope.row.review_enabled" @change="toggleSubmissionReviewEnabled(scope.row, $event)" />
                  </template>
                </el-table-column>
                <el-table-column prop="not_applicable_reason" label="不适用原因" min-width="130" />
                <el-table-column label="操作" width="180">
                  <template slot-scope="scope">
                    <el-button
                      type="text"
                      :loading="isFilingParseTaskRunning(submissionParseTaskKey(scope.row))"
                      :disabled="parsingBatch || isFilingParseTaskRunning('submission-batch')"
                      @click="parseSubmission(scope.row)"
                    >解析</el-button>
                    <el-button type="text" @click="viewParsedMarkdown(scope.row)">查看解析结果</el-button>
                    <el-button type="text" :disabled="isFilingParseTaskRunning(submissionParseTaskKey(scope.row))" @click="$refs.parseReviewPanel.openSource('submission', scope.row.doc_id)">核对</el-button>
                    <el-button type="text" style="color: #d03050" @click="removeSubmission(scope.row)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-card v-if="completenessResult" shadow="never" style="margin-top: 10px">
                <div><b>完整性检查：</b>{{ completenessResult.pass ? "通过" : "未通过" }}</div>
                <div>文件缺失 {{ (completenessResult.missing_items || []).length }}；解析异常 {{ (completenessResult.parse_issues || []).length }}；内容缺失 {{ (completenessResult.content_missing || []).length }}；不适用 {{ (completenessResult.not_applicable || []).length }}</div>
                <div v-for="(x, i) in (completenessResult.parse_issues || []).concat(completenessResult.content_missing || [])" :key="`detail-${i}`">{{ x.message }}</div>
                <div style="margin-top: 6px">缺失资料：{{ (completenessResult.missing_items || []).length }}</div>
                <div v-for="item in (completenessResult.missing_items || [])" :key="`m-${item.code}`" class="meta-line">{{ item.message }}</div>
                <div style="margin-top: 6px">不适用问题：{{ (completenessResult.not_applicable_issues || []).length }}</div>
                <div v-for="item in (completenessResult.not_applicable_issues || [])" :key="`n-${item.code}`" class="meta-line">{{ item.message }}</div>
              </el-card>
            </el-col>
          </el-row>
        </el-card>
        <el-dialog title="初始材料与补充材料差异比对" :visible.sync="compareDialogVisible" width="78%">
          <div v-if="compareResult">
            <div class="meta-line">
              初始材料：{{ (compareResult.initial_doc || {}).file_name || "-" }}；补充材料：{{ (compareResult.supplement_doc || {}).file_name || "-" }}
            </div>
            <div class="meta-line" style="margin-bottom: 8px">
              相似度：{{ ((((compareResult.summary || {}).similarity) || 0) * 100).toFixed(2) }}%；
              差异总数：{{ (compareResult.summary || {}).diff_count || 0 }}；
              新增：{{ (compareResult.summary || {}).added_count || 0 }}；
              删除：{{ (compareResult.summary || {}).deleted_count || 0 }}；
              修改：{{ (compareResult.summary || {}).changed_count || 0 }}
            </div>
            <el-table :data="(compareResult.diff_items || [])" border size="mini" max-height="480">
              <el-table-column prop="type" label="差异类型" width="90" />
              <el-table-column prop="old_text" label="初始材料内容" min-width="280" show-overflow-tooltip />
              <el-table-column prop="new_text" label="补充材料内容" min-width="280" show-overflow-tooltip />
            </el-table>
          </div>
          <el-empty v-else description="暂无比对结果" />
        </el-dialog>
        <el-dialog :title="parsedMarkdownTitle || '解析结果'" :visible.sync="parsedDialogVisible" :before-close="closeNumericPreview" append-to-body width="80%">
          <el-alert v-if="parsedRevisionWarning" :title="parsedRevisionWarning" type="warning" :closable="false" show-icon />
          <el-button size="mini" :loading="downloadingNumericSource" :disabled="!numericDocId" @click="downloadNumericSource">下载原件核对</el-button>
          <template v-if="numericDraft.length">
            <el-alert title="请对照原件逐格核对。保存只供新分析使用，不改变下方原始识别或历史报告。未勾选的数值仍待确认。" type="warning" :closable="false" />
            <el-alert v-if="numericReview.stale" title="资料已重新解析，原人工确认不再自动应用，请重新核对。" type="warning" :closable="false" />
            <el-table :data="numericDraft.slice((numericPage - 1) * 20, numericPage * 20)" border size="mini">
              <el-table-column label="位置" width="130"><template slot-scope="s">第{{ s.row.page }}页 / 表{{ s.row.table }} / {{ s.row.row }}行{{ s.row.column }}列</template></el-table-column>
              <el-table-column prop="original_text" label="原始识别" min-width="100" />
              <el-table-column label="复核候选" min-width="140"><template slot-scope="s">{{ (s.row.candidates || []).map(x => x.secondary || '未完成核验').join('；') }}</template></el-table-column>
              <el-table-column label="确认值" min-width="110"><template slot-scope="s"><el-input v-model="s.row.value" size="mini" maxlength="2000" /></template></el-table-column>
              <el-table-column label="核对依据" min-width="140"><template slot-scope="s"><el-input v-model="s.row.reason" size="mini" maxlength="2000" placeholder="对照原页的依据" /></template></el-table-column>
              <el-table-column label="已核对" width="75"><template slot-scope="s"><el-checkbox v-model="s.row.checked" /></template></el-table-column>
            </el-table>
            <el-pagination :current-page.sync="numericPage" :page-size="20" :total="numericDraft.length" layout="total, prev, pager, next" />
            <el-button size="mini" type="primary" :loading="savingNumeric" :disabled="!numericDirty || !numericReview.source_attempt" @click="saveNumericConfirmations">保存人工确认</el-button>
            <span>{{ numericSaveError || (numericDirty ? '有未保存修改' : '已保存 / 未修改') }}</span>
          </template>
          <div class="markdown-preview parsed-markdown-dialog" v-html="renderMarkdown(parsedMarkdownContent)"></div>
        </el-dialog>
      </el-tab-pane>

      <el-tab-pane label="参考资料与规则" name="reference-rule">
        <el-row :gutter="14">
          <el-col :span="12">
            <el-card>
              <div slot="header">参考资料</div>
              <el-form label-width="92px" size="mini">
                <el-form-item label="药品类别">
                  <el-select v-model="referenceForm.drug_category" style="width: 100%">
                    <el-option v-for="x in referenceTaxonomy.drug_categories" :key="x" :label="x" :value="x" />
                  </el-select>
                </el-form-item>
                <el-form-item label="资料类型">
                  <el-select v-model="referenceForm.material_type" style="width: 100%">
                    <el-option v-for="x in referenceTaxonomy.material_types" :key="x" :label="x" :value="x" />
                  </el-select>
                </el-form-item>
                <el-form-item label="适用事项">
                  <el-select v-model="referenceForm.applicable_change_item" style="width: 100%">
                    <el-option v-for="x in referenceTaxonomy.change_items" :key="x" :label="x" :value="x" />
                  </el-select>
                </el-form-item>
              </el-form>
              <el-upload action="#" :auto-upload="false" :on-change="onReferenceFileChange" :file-list="referenceFileList" multiple>
                <el-button>选择参考资料</el-button>
              </el-upload>
              <el-button type="primary" :loading="uploadingReference" style="margin-top: 10px" @click="uploadReferenceMaterials">上传</el-button>
              <div style="margin-top: 10px; display: flex; gap: 8px">
                <el-select v-model="referenceFilters.drug_category" clearable placeholder="筛选药品类别" style="width: 140px">
                  <el-option v-for="x in referenceTaxonomy.drug_categories" :key="`f1-${x}`" :label="x" :value="x" />
                </el-select>
                <el-select v-model="referenceFilters.material_type" clearable placeholder="筛选资料类型" style="width: 140px">
                  <el-option v-for="x in referenceTaxonomy.material_types" :key="`f2-${x}`" :label="x" :value="x" />
                </el-select>
                <el-button @click="loadReferenceMaterials">筛选</el-button>
              </div>
              <el-table :data="referenceMaterials" border size="mini" style="margin-top: 10px">
                <el-table-column prop="title" label="标题" min-width="150" />
                <el-table-column prop="drug_category" label="药品类别" width="90" />
                <el-table-column prop="material_type" label="类型" width="110" />
                <el-table-column label="解析状态" min-width="150"><template slot-scope="s"><el-tag size="mini" :type="parseStatusType(s.row.parse_status)">{{ parseContentLabel(s.row.parse_status) }}</el-tag><div>{{ parseDiagnosticsLabel(s.row.parse_diagnostics) }}</div><div v-if="(s.row.latest_attempt || {}).content_status === 'failed'">本次失败；此前结果如存在仍保留。</div><el-button type="text" @click="openFileParseDetails(s.row)">查看详情</el-button></template></el-table-column>
                <el-table-column label="索引" width="90"><template slot-scope="s">{{ indexStatusLabel(s.row.index_status) }}</template></el-table-column>
                <el-table-column label="启用" width="80">
                  <template slot-scope="scope">
                    <el-switch :value="!!scope.row.enabled" @change="toggleReferenceEnabled(scope.row, $event)" />
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="150">
                  <template slot-scope="scope">
                    <el-button
                      type="text"
                      :loading="isFilingParseTaskRunning(referenceParseTaskKey(scope.row))"
                      @click="parseReference(scope.row)"
                    >解析</el-button>
                    <el-button type="text" @click="viewReferenceParsed(scope.row)">查看</el-button>
                    <el-button type="text" :disabled="isFilingParseTaskRunning(referenceParseTaskKey(scope.row))" @click="$refs.parseReviewPanel.openSource('reference', scope.row.doc_id)">核对</el-button>
                    <el-button type="text" style="color: #d03050" @click="removeReference(scope.row)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-dialog :visible.sync="referenceParsedDialogVisible" :title="referenceParsedTitle || '参考资料解析结果'" width="70%">
                <div class="markdown-preview parsed-markdown-dialog" v-html="renderMarkdown(referenceParsedMarkdown)"></div>
              </el-dialog>
            </el-card>
          </el-col>
          <el-col :span="12">
            <el-card>
              <div slot="header">审评规则</div>
              <el-form label-width="90px" size="small">
                <el-form-item label="规则编码"><el-input v-model="ruleForm.rule_code" /></el-form-item>
                <el-form-item label="规则名称"><el-input v-model="ruleForm.rule_name" /></el-form-item>
                <el-form-item label="规则分类"><el-input v-model="ruleForm.rule_category" /></el-form-item>
                <el-form-item label="药品类别"><el-input v-model="ruleForm.drug_category" /></el-form-item>
                <el-form-item label="规则类型">
                  <el-select v-model="ruleForm.rule_type" style="width: 100%">
                    <el-option label="完整性" value="formal" />
                    <el-option label="技术" value="technical" />
                    <el-option label="稳定性" value="stability" />
                    <el-option label="质量标准" value="quality" />
                    <el-option label="报告" value="report" />
                  </el-select>
                </el-form-item>
                <el-form-item label="规则内容"><el-input v-model="ruleForm.rule_content" type="textarea" :rows="3" /></el-form-item>
                <el-form-item label="执行条件"><el-input v-model="ruleConditionText" type="textarea" :rows="2" /><div>保留原条件；批次数规则可填 JSON 参数，例如 {"minimum_batches":3}。不支持的自由改写将拒绝保存。</div></el-form-item>
                <el-form-item label="命中结果"><el-input v-model="ruleForm.hit_result" /></el-form-item>
                <el-form-item label="风险等级"><el-input v-model="ruleForm.risk_level" /></el-form-item>
                <el-form-item label="依据来源"><el-input v-model="ruleForm.basis_source" /></el-form-item>
              </el-form>
              <div>
                <el-button type="primary" :loading="savingRule" @click="saveRule">{{ ruleForm.rule_id ? "保存规则" : "新增规则" }}</el-button>
                <el-upload action="#" :auto-upload="false" :show-file-list="false" :on-change="onRuleImportChange" style="display: inline-block; margin-left: 8px">
                  <el-button>导入规则JSON</el-button>
                </el-upload>
              </div>
              <div style="margin-top: 8px; display: flex; gap: 8px">
                <el-input v-model="ruleFilters.rule_category" placeholder="筛选规则分类" />
                <el-input v-model="ruleFilters.drug_category" placeholder="筛选药品类别" />
                <el-button @click="loadRules">筛选</el-button>
              </div>
              <el-table :data="rules" border size="mini" style="margin-top: 10px">
                <el-table-column prop="rule_code" label="编码" width="110" />
                <el-table-column prop="rule_name" label="名称" min-width="110" />
                <el-table-column prop="rule_type" label="类型" width="80" />
                <el-table-column label="分类" width="120">
                  <template slot-scope="scope">{{ ((scope.row.rule_json || {}).rule_category) || "-" }}</template>
                </el-table-column>
                <el-table-column label="启用" width="80">
                  <template slot-scope="scope">
                    <el-switch :value="!!scope.row.enabled" @change="toggleRuleEnabled(scope.row, $event)" />
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="130">
                  <template slot-scope="scope">
                    <el-button type="text" @click="editRule(scope.row)">编辑</el-button>
                    <el-button type="text" style="color: #d03050" @click="removeRule(scope.row)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <el-tab-pane label="AI 审评结果" name="result">
        <el-card>
          <div slot="header">审评输出</div>
          <el-empty v-if="!reviewResult" description="尚未运行审评" />
          <div v-else>
            <el-card shadow="never" style="margin-bottom: 12px">
              <div slot="header"><b>总体审评建议</b></div>
              <el-alert v-if="reviewResult.reference_usage" :title="reviewResult.reference_usage.message"
                type="info" :closable="false" show-icon />
              <el-alert v-if="reviewResult.input_state && reviewResult.input_state.status !== 'current'"
                :title="reviewResult.input_state.message" type="warning" :closable="false" show-icon />
              <el-alert v-if="reviewIncomplete" title="本次审评未完成，以下仅为部分结果" type="error" :closable="false" show-icon>
                <div>{{ reviewResult.failure_message || '不能据此确认完整结论或导出完整报告。' }}</div>
                <div>已完成并保存：{{ (reviewResult.completed_stages || []).join('、') || '无已保存阶段' }}</div>
                <div>失败阶段：{{ (reviewResult.error || {}).stage || '未记录' }}；原因码：{{ (reviewResult.error || {}).code || '未记录' }}</div>
                <div>未完成范围：{{ (reviewResult.incomplete_stages || []).join('、') || '未记录，请核对任务进度' }}</div>
                <div>历史成功结果请从审评历史中单独打开。</div>
              </el-alert>
              <div class="big-conclusion">{{ (reviewResult.overall_conclusion || {}).result || "-" }}</div>
              <el-alert v-if="(reviewResult.llm_calls || []).some(call => call.scene === 'overall_summary' && call.status === 'fallback')"
                type="warning" :closable="false" show-icon
                title="解释排序未完成，使用原始事实顺序；不改变已完成的规则检查结论。" />
              <div class="meta-line">{{ (reviewResult.overall_conclusion || {}).summary || "-" }}</div>
              <div class="meta-line">需人工确认：{{ (reviewResult.overall_conclusion || {}).need_manual_review == null ? '历史结果未记录' : ((reviewResult.overall_conclusion || {}).need_manual_review ? '是' : '否') }}</div>
              <div>技术支持：{{ (reviewResult.technical_support || {}).status || '历史结果未记录' }}</div>
              <div>处理建议：{{ reviewResult.recommended_action || '历史结果未记录' }}</div>
              <div>审评完成时间（北京时间）：{{ reviewResult.review_completed_at || '历史结果未记录' }}</div>
              <details v-for="(label, key) in {file_missing:'文件缺失', content_missing:'字段或内容缺项', parse_error:'解析异常', conflict:'信息冲突'}" :key="key">
                <summary>{{ label }}：{{ (reviewResult.issue_counts || {})[key] == null ? '历史结果未记录' : reviewResult.issue_counts[key] }}</summary>
                <p>按检查发现统计，不同字段、批次、条件及规则分别保留。</p>
                <div v-for="(issue, idx) in ((reviewResult.issue_details || {})[key] || [])" :key="idx">{{ issue.module }}：{{ issue.reason }}；{{ issue.required_action }}</div>
              </details>
              <details><summary>完整问题与处理要求</summary><div v-for="(issue, idx) in (reviewResult.issues || [])" :key="idx">{{ issue.status }} · {{ issue.module }}：{{ issue.reason }}；{{ issue.required_action }}</div></details>
              <el-card style="margin-top:12px">
                <div slot="header">人工复核意见（仅属于当前审评轮次）</div>
                <p>已保存：{{ (reviewResult.manual_confirmation || {}).comment || '尚未记录' }}</p>
                <p aria-live="polite">意见状态：{{ manualSaveLabel }}</p>
                <p>复核人：{{ (reviewResult.manual_confirmation || {}).reviewer || '未记录' }}；时间：{{ (reviewResult.manual_confirmation || {}).confirmed_at || '未记录' }}</p>
                <el-input v-model="manualReviewer" :disabled="reviewIncomplete" placeholder="复核人" />
                <el-input v-model="manualComment" :disabled="reviewIncomplete" type="textarea" placeholder="人工意见与AI结论分别保存" />
                <el-button :disabled="reviewIncomplete" :loading="savingManual" @click="saveManualOpinion">保存本轮人工意见</el-button>
              </el-card>
            </el-card>

            <el-row :gutter="12">
              <el-col :span="12">
                <el-card shadow="never" style="margin-bottom: 12px">
                  <div slot="header"><b>资料形式审查</b></div>
                  <div>结论：{{ (reviewResult.formal_review || {}).result || "历史结果未记录" }}</div>
                  <div class="meta-line">文件缺失：{{ resultCount('formal_review', 'missing_materials') }}；解析异常：{{ resultCount('formal_review', 'parse_issues') }}；内容缺失：{{ resultCount('formal_review', 'content_missing') }}</div>
                  <div v-for="(x, i) in ((reviewResult.formal_review || {}).content_missing || []).concat((reviewResult.formal_review || {}).parse_issues || [])" :key="`formal-detail-${i}`">{{ x.message }}</div>
                  <div v-for="(x, i) in ((reviewResult.formal_review || {}).missing_materials || [])" :key="`fm-${i}`" class="meta-line">
                    - {{ typeof x === 'object' ? (x.message || x.label || JSON.stringify(x)) : x }}
                  </div>
                </el-card>
              </el-col>
              <el-col :span="12">
                <el-card shadow="never" style="margin-bottom: 12px">
                  <div slot="header"><b>一致性核验</b></div>
                  <div class="meta-line">不一致项：{{ resultCount('consistency_check', 'inconsistent_items') }}</div>
                  <div v-for="(x, i) in ((reviewResult.consistency_check || {}).inconsistent_items || [])" :key="`ci-${i}`" class="meta-line">- {{ x }}</div>
                  <div class="meta-line">需人工确认项：{{ resultCount('consistency_check', 'need_manual_review') }}</div>
                </el-card>
              </el-col>
            </el-row>

            <el-card shadow="never" style="margin-bottom: 12px">
              <div slot="header"><b>申报事实与逐项比较</b></div>
              <div>解析异常 {{ resultCount('formal_review', 'parse_issues') }}；内容缺失 {{ resultCount('formal_review', 'content_missing') }}；不适用 {{ resultCount('formal_review', 'not_applicable') }}</div>
              <div v-for="(x, i) in ((reviewResult.formal_review || {}).parse_issues || []).concat((reviewResult.formal_review || {}).content_missing || [])" :key="`content-${i}`">{{ x.file_name }}：{{ x.message }}</div>
              <el-table :data="(reviewResult.consistency_check || {}).comparisons || []" border size="small">
                <el-table-column prop="field_label" label="比较字段" width="140" />
                <el-table-column label="材料一 · 原值 / 标准化值"><template slot-scope="s"><div v-if="s.row.left">{{ s.row.left.source.file_name }}：{{ s.row.left.raw_value }} / {{ s.row.left.normalized_value }}<details><summary>来源位置与可用状态</summary><pre>{{ pretty(s.row.left) }}</pre></details></div><span v-else>未提取</span></template></el-table-column>
                <el-table-column label="材料二 · 原值 / 标准化值"><template slot-scope="s"><div v-if="s.row.right">{{ s.row.right.source.file_name }}：{{ s.row.right.raw_value }} / {{ s.row.right.normalized_value }}<details><summary>来源位置与可用状态</summary><pre>{{ pretty(s.row.right) }}</pre></details></div><span v-else>未提取</span></template></el-table-column>
                <el-table-column prop="status" label="结论" width="100" /><el-table-column prop="reason" label="理由" />
              </el-table>
              <details><summary>全部事实（含原文、空白与来源）</summary><pre>{{ pretty((reviewResult.field_check || {}).facts || []) }}</pre></details>
            </el-card>
            <el-row :gutter="12">
              <el-col :span="12">
                <el-card shadow="never" style="margin-bottom: 12px">
                  <div slot="header"><b>变更管理类别建议</b></div>
                  <div>建议类别：{{ (reviewResult.change_category_suggestion || {}).suggested_category || "-" }}</div>
                  <div class="meta-line">理由：{{ (reviewResult.change_category_suggestion || {}).reason || "-" }}</div>
                </el-card>
              </el-col>
              <el-col :span="12">
                <el-card shadow="never" style="margin-bottom: 12px">
                  <div slot="header"><b>质量标准比对</b></div>
                  <div>结论：{{ (reviewResult.quality_standard_check || {}).result || "-" }}</div>
                  <div class="meta-line">风险项：{{ ((reviewResult.quality_standard_check || {}).risk_items || []).length }}</div>
                </el-card>
              </el-col>
            </el-row>

            <el-card shadow="never" style="margin-bottom: 12px">
              <div slot="header"><b>稳定性趋势分析</b></div>
              <el-alert
                :title="stabilityLimitAlertTitle"
                :type="stabilityLimitAlertType"
                :closable="false"
                show-icon
                style="margin-bottom: 12px"
              >
                <div>{{ stabilityLimitCheck.reminder || "暂无可用判定结果" }}</div>
                <stability-limit-details :check="stabilityLimitCheck" />
              </el-alert>
              <div>结论：{{ (reviewResult.stability_trend_analysis || {}).result || "-" }}</div>
              <div class="meta-line">说明：{{ (reviewResult.stability_trend_analysis || {}).summary || "-" }}</div>
              <div class="meta-line">
                <b>超限提醒：</b>{{ (((reviewResult.stability_trend_analysis || {}).limit_check || {}).status) || "待确认" }}；
                {{ (((reviewResult.stability_trend_analysis || {}).limit_check || {}).reminder) || "暂无可用判定结果" }}
              </div>
              <div class="meta-line">覆盖要求：{{ ((reviewResult.stability_trend_analysis || {}).coverage || {}).reason }}</div>
              <div v-for="(gap, i) in (((reviewResult.stability_trend_analysis || {}).coverage || {}).missing || [])" :key="`coverage-${i}`">{{ gap }}</div>
              <details><summary>逐批次、条件、规格、包装覆盖</summary><pre>{{ pretty((reviewResult.stability_trend_analysis || {}).coverage || {}) }}</pre></details>
              <div>显著变化：{{ ((reviewResult.stability_trend_analysis || {}).significant_change_assessment || {}).status }}；{{ ((reviewResult.stability_trend_analysis || {}).significant_change_assessment || {}).reason }}</div>
              <details><summary>检测记录、限度判定与原表位置</summary><pre>{{ pretty((reviewResult.stability_trend_analysis || {}).records || []) }}</pre></details>
              <details><summary>显著变化依据与分组判断</summary><pre>{{ pretty((reviewResult.stability_trend_analysis || {}).significant_change_assessment || {}) }}</pre></details>
              <div class="meta-line">风险点：{{ ((reviewResult.stability_trend_analysis || {}).risk_points || []).length }}</div>
              <div class="meta-line">
                分析范围：{{ (((reviewResult.stability_trend_analysis || {}).data_scope || {}).analysis_population) || "-" }}；
                自制批次：{{ (((reviewResult.stability_trend_analysis || {}).data_scope || {}).self_batches || []).join("、") || "-" }}；
                参比批次（不计入自制趋势）：{{ (((reviewResult.stability_trend_analysis || {}).data_scope || {}).reference_batches || []).join("、") || "-" }}
              </div>
              <div
                v-for="(item, i) in ((reviewResult.stability_trend_analysis || {}).key_indicators || [])"
                :key="`st-${i}`"
                class="meta-line"
              >
                <div>-指标：{{ item.indicator }}；全部自制批次综合趋势：{{ item.trend }}；风险等级：{{ item.risk_level }}；限度判定：{{ item.limit_reminder || item.limit_status || "待确认" }}</div>
                <div class="meta-line">最小值：{{ item.min_display || "-" }}；最大值：{{ item.max_display || "-" }}</div>
                <div class="meta-line">样本点：{{ item.point_count || 0 }}（数值点 {{ item.numeric_point_count || 0 }}）；自制批次：{{ item.self_batch_count || 0 }}</div>
              </div>
              <div class="chart-grid">
                <div
                  v-for="(chart, i) in ((reviewResult.stability_trend_analysis || {}).charts || [])"
                  :key="`chart-${i}`"
                  class="chart-block chart-grid-item"
                >
                  <div class="meta-line"><b>{{ chart.indicator }}</b></div>
                  <div class="meta-line">{{ chart.data_scope || "" }}</div>
                  <div class="svg-chart" v-html="chart.svg_content || ''"></div>
                </div>
              </div>
            </el-card>

            <el-card shadow="never" style="margin-bottom: 12px">
              <div slot="header"><b>风险点与需人工确认事项</b></div>
              <div class="meta-line">风险点数量：{{ (reviewResult.risk_points || []).length }}</div>
              <div v-for="(x, i) in (reviewResult.risk_points || [])" :key="`rp-${i}`" class="meta-line">- {{ x }}</div>
              <div class="meta-line" style="margin-top:8px">人工确认项：{{ (reviewResult.manual_review_items || []).length }}</div>
              <div v-for="(x, i) in (reviewResult.manual_review_items || [])" :key="`mr-${i}`" class="meta-line">- {{ x }}</div>
            </el-card>

            <el-row :gutter="12">
              <el-col :span="12">
                <el-card shadow="never" style="margin-bottom: 12px">
                  <div slot="header"><b>补正通知书草稿</b></div>
                  <div class="markdown-preview" v-html="renderMarkdown(reviewResult.correction_notice_markdown || pretty(reviewResult.correction_notice_draft))"></div>
                </el-card>
              </el-col>
              <el-col :span="12">
                <el-card shadow="never" style="margin-bottom: 12px">
                  <div slot="header"><b>审评报告草稿</b></div>
                  <div class="markdown-preview" v-html="renderMarkdown(reviewResult.review_report_markdown || pretty(reviewResult.review_report_draft))"></div>
                </el-card>
              </el-col>
            </el-row>

            <el-card shadow="never">
              <div slot="header"><b>完整规则执行结果</b></div>
              <div class="meta-line">本次规则：{{ Array.isArray(reviewResult.rule_results) ? reviewResult.rule_results.length : '历史结果未记录' }}；汇总：{{ pretty(reviewResult.rule_summary || {}) }}</div>
              <details v-for="(ref, idx) in (reviewResult.evidence_refs || [])" :key="`snapshot-${idx}`">
                <summary>{{ ref.evidence_id }} · {{ ref.title || '来源未记录' }} · {{ ref.position || '位置未记录' }}</summary>
                <p>{{ ref.access_message || '本轮保存的证据；来源访问状态未记录' }}；{{ ref.availability || '历史状态未记录' }}</p>
                <pre style="white-space:pre-wrap">{{ ref.snippet || '缺少原文证据，请核对原件' }}</pre>
                <pre style="white-space:pre-wrap">{{ pretty(ref.source || {}) }}</pre>
              </details>
              <div class="meta-line">证据引用数：{{ (reviewResult.evidence_refs || []).length }}</div>
              <div
                v-for="(rule, i) in (reviewResult.rule_results || reviewResult.matched_rules || [])"
                :key="`rule-${i}`"
                class="chart-block"
              >
                <div class="chart-title">{{ rule.rule_code }} {{ rule.rule_name }}</div>
                <div class="meta-line">规则分类：{{ rule.rule_category || "-" }}</div>
                <div class="meta-line">规则内容：{{ rule.rule_content || "-" }}</div>
                <div class="meta-line">依据来源：{{ rule.basis_source || "-" }}</div>
                <div class="meta-line">执行状态：{{ rule.execution_status === "completed" ? "已完成" : (rule.execution_status === "failed" ? "执行失败" : "历史结果未记录") }}；业务结论：{{ rule.status || '无业务结论' }}</div>
                <div>实际使用条件：{{ pretty(rule.applied_condition) }}</div>
                <div v-for="(sub, j) in (rule.subchecks || [])" :key="`sub-${i}-${j}`"><b>{{ sub.name }}：{{ sub.status }}</b><p>{{ sub.reason }}</p><details><summary>实际证据</summary><pre>{{ pretty(sub.evidence) }}</pre></details></div>
                <div v-if="(rule.facts || []).length" class="meta-line">申报资料事实：</div>
                <div v-for="(fact, idx) in (rule.facts || [])" :key="`rf-${i}-${idx}`" class="meta-line">- {{ fact }}</div>
                <div class="meta-line">审评判断：{{ rule.review_conclusion || "-" }}</div>
              </div>
              <div style="margin-top: 8px"><b>结论证据链</b></div>
              <div
                v-for="(chain, i) in ((reviewResult.overall_conclusion || {}).evidence_chain || [])"
                :key="`chain-${i}`"
                class="meta-line"
              >
                - 结论：{{ chain.claim }} => {{ chain.claim_value }}
                <div class="meta-line">  规则：{{ ((chain.evidence_rules || []).map((x) => `${x.rule_code} ${x.rule_name}`)).join('；') || '无' }}</div>
                <div class="meta-line">  文档：{{ ((chain.evidence_docs || []).map((x) => x.doc_name || x.title)).join('；') || '无' }}</div>
              </div>
            </el-card>
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="运行记录" name="history">
        <el-card>
          <div slot="header">审评运行记录</div>
          <el-table :data="runHistory" border>
            <el-table-column prop="run_id" label="Run ID" min-width="210" />
            <el-table-column prop="status" label="状态" width="100" />
            <el-table-column label="查看" width="100"><template slot-scope="scope"><el-button size="mini" @click="selectRun(scope.row.run_id)">查看本轮</el-button></template></el-table-column>
            <el-table-column prop="started_at" label="开始时间" width="170" />
            <el-table-column prop="finished_at" label="完成时间" width="170" />
            <el-table-column prop="error_message" label="错误信息" min-width="200" />
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="审评报告" name="report">
        <el-card>
          <div slot="header">报告预览 · {{ (reviewResult || {}).run_id || '请选择审评轮次' }}</div>
          <el-button :disabled="!reviewResult || reviewIncomplete || exportingReport" :loading="generatingReport" @click="regenerateReport">生成或重新生成当前轮次报告</el-button>
          <el-button :disabled="!reviewResult || exportingReport || generatingReport" @click="loadReport()">重新读取报告</el-button>
          <el-empty v-if="!projectReport" description="暂无报告" />
          <div v-else>
            <el-alert v-if="projectReport.input_state && projectReport.input_state.status !== 'current'"
              :title="projectReport.input_state.message" type="warning" :closable="false" show-icon />
            <div style="margin-bottom: 8px; display:flex; justify-content:space-between; align-items:center;">
              <el-button size="mini" type="primary" :loading="exportingReport" :disabled="reviewIncomplete || generatingReport || savingManual" @click="exportReportWord">导出Word</el-button>
            </div>
            <div class="markdown-preview" v-html="renderMarkdown(projectReport.report_content)"></div>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>
    <ParseReviewPanel ref="parseReviewPanel" :project-id="String(projectId || '')" @dirty-change="parseReviewDirty = $event" @saved="loadBaseData" />
    <el-drawer title="解析诊断详情" :visible.sync="parseDetailsVisible" size="70%" append-to-body>
      <div class="parse-detail-body">
        <p>{{ parseDetailsTitle }}</p>
        <el-empty v-if="!parseDetailGroups.length" description="暂无诊断问题记录" />
        <div v-for="(group, gi) in pagedParseDetails" :key="gi" class="parse-detail-group">
          <b>{{ group.file }} · {{ group.message }}</b>
          <p>{{ group.entries.length }} 条；涉及页码：{{ compactPages(group.entries.map(x => x.page)) || '未记录' }}</p>
          <details><summary>展开全部页码和区域</summary>
            <div class="parse-region-list"><div v-for="(entry, ei) in group.entries" :key="ei">
              第 {{ entry.page || '未知' }} 页 · {{ entry.availability }} · {{ entry.stage || '未记录阶段' }}
              <span v-if="entry.bbox_pdf"> · 区域：{{ entry.bbox_pdf.join(', ') }}</span>
              <span v-if="entry.http_status"> · HTTP {{ entry.http_status }}</span>
              <span v-if="entry.exception_type"> · {{ entry.exception_type }}</span>
              <pre v-if="entry.text">{{ entry.text }}</pre>
            </div></div>
          </details>
        </div>
        <el-pagination v-if="parseDetailGroups.length" :current-page.sync="parseDetailsPage" :page-size="20" :total="parseDetailGroups.length" layout="total, prev, pager, next" />
      </div>
    </el-drawer>
  </div>
</template>

<script>
import {
  checkFilingSubmissionCompleteness,
  compareFilingSubmissions,
  createFilingRule,
  deleteFilingReferenceMaterial,
  deleteFilingRule,
  deleteFilingSubmission,
  downloadFilingReportWord,
  getApplicationForm,
  getFilingChangeProjectDetail,
  getFilingProjectReport,
  getFilingReferenceParsedMarkdown,
  getFilingReferenceTaxonomy,
  getFilingParseTaskProgress,
  getFilingProjectParseTasks,
  getFilingSubmissionCatalog,
  getFilingSubmissionParsedMarkdown,
  downloadFilingSubmissionOriginal,
  confirmFilingSubmissionNumbers,
  getFilingReviewTaskProgress,
  getFilingRunResult,
  generateFilingRunReport,
  manualConfirmFilingRun,
  importFilingRules,
  listFilingReferenceMaterials,
  listFilingReviewHistory,
  listFilingRules,
  listFilingSubmissions,
  saveApplicationForm,
  updateFilingReferenceMaterialMetadata,
  setFilingSubmissionCategoryNotApplicable,
  startApplicationFormImport,
  startApplicationFormParse,
  startBatchFilingSubmissionsParse,
  startFilingAIReview,
  startFilingReferenceMaterialParse,
  startFilingSubmissionParse,
  updateFilingSubmissionMetadata,
  updateFilingRule,
  uploadFilingReferenceMaterials,
  uploadFilingSubmissions,
} from "@/api/filingChangeReview";
import MarkdownIt from "markdown-it";
import StabilityLimitDetails from "./StabilityLimitDetails.vue";
import ParseReviewPanel from "./ParseReviewPanel.vue";
import { getFilingParseReadiness } from '@/api/filingChangeReview';

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });

export default {
  name: "FilingChangeReviewSession",
  components: { StabilityLimitDetails, ParseReviewPanel },
  data() {
    return {
      activeTab: "form",
      parseReviewDirty: false,
      parseDetailsVisible: false,
      parseDetailsTitle: "",
      parseDetailGroups: [],
      parseDetailsPage: 1,
      manualComment: "",
      manualReviewer: "",
      manualLoaded: { projectId: '', runId: '', comment: '', reviewer: '', revision: 0 },
      manualSaveError: '',
      savingManual: false,
      generatingReport: false,
      exportingReport: false,
      project: {},
      formSchema: {},
      formResolutions: {},
      formDirty: false,
      formEdits: {},
      formLoadedValues: {},
      formEditSerial: 0,
      formLegacyRoleNotes: [],
      formVisible: true,
      savingForm: false,
      importingForm: false,
      uploading: false,
      running: false,
      submissions: [],
      selectedSubmissionRows: [],
      submissionFileList: [],
      submissionCatalog: [],
      submissionCategoryMeta: {},
      selectedSubmissionCategory: "",
      completenessResult: null,
      checkingCompleteness: false,
      naReasonInput: "",
      parsingBatch: false,
      comparingSubmissions: false,
      compareDialogVisible: false,
      compareResult: null,
      parsedDialogVisible: false,
      numericReview: {}, numericDraft: [], numericSnapshot: '[]', numericDocId: '', numericPage: 1,
      numericSourceName: '', downloadingNumericSource: false,
      savingNumeric: false, numericSaveError: '',
      parsedMarkdownContent: "",
      parsedRevisionWarning: "",
      parsedMarkdownTitle: "",
      parsedMarkdownRequestToken: 0,
      reviewResult: null,
      reviewTask: { task_id: "", status: "", message: "", logs: [] },
      reviewPollTimer: null,
      reviewPollToken: 0,
      runHistory: [],
      referenceFileList: [],
      referenceTaxonomy: { drug_categories: [], material_types: [], change_items: [] },
      referenceFilters: { drug_category: "", material_type: "", applicable_change_item: "" },
      referenceForm: {
        drug_category: "通用资料",
        material_type: "技术指导原则类",
        applicable_change_item: "延长药品有效期",
        applicable_registration_classification: "",
        publisher: "",
        version: "",
        enabled: true,
        remark: "",
      },
      uploadingReference: false,
      referenceMaterials: [],
      referenceParsedDialogVisible: false,
      referenceParsedTitle: "",
      referenceParsedMarkdown: "",
      referenceParsedRequestToken: 0,
      rules: [],
      ruleFilters: { rule_type: "", rule_category: "", drug_category: "" },
      ruleForm: {
        rule_id: "", rule_code: "", rule_name: "", rule_type: "technical", rule_content: "",
        rule_category: "资料完整性规则", drug_category: "化学药品", applicable_change_item: "延长药品有效期",
        applicable_material_category: "", applicable_fields: "", hit_result: "", risk_level: "中", basis_source: "", remark: "",
      },
      savingRule: false,
      ruleConditionText: "",
      projectReport: null,
      dataRequestTokens: {},
      dataRequestProjects: {},
      dataRequestSerial: 0,
      pageDisposed: false,
      filingParseTaskStates: {},
      formParseAttempt: {},
      formParseStatus: 'not_parsed',
      formParseResolution: {},
      formSource: {},
      filingParseTaskEpochs: {},
      filingParseTaskPollers: {},
    };
  },
  computed: {
    nonFormParseTaskStates() {
      return Object.fromEntries(Object.entries(this.filingParseTaskStates).filter(([key]) => key !== 'application-form'));
    },
    currentFormParseState() {
      const current = this.filingParseTaskStates['application-form'];
      if (current && String(current.bound_project_id || current.project_id || '') === String(this.projectId || '')) return current;
      return this.formParseAttempt.content_status ? this.attemptState(this.formParseAttempt) : null;
    },
    numericDirty() { return JSON.stringify(this.numericDraft || []) !== this.numericSnapshot; },
    manualDirty() {
      const loaded = this.manualLoaded || {};
      return !!loaded.runId && (this.manualComment !== loaded.comment || this.manualReviewer !== loaded.reviewer);
    },
    manualSaveLabel() {
      if (this.savingManual) return '保存中';
      if (this.manualSaveError) return '保存失败 / 版本冲突：' + this.manualSaveError;
      return this.manualDirty ? '未保存' : '已保存';
    },
    reviewIncomplete() {
      const result = this.reviewResult || {};
      return result.review_complete === false || (!!result.execution_status && result.execution_status !== 'completed');
    },
    projectId() {
      return this.$route.params.projectId;
    },
    renderFields() {
      const out = {};
      const skip = new Set(["task_type"]);
      Object.keys(this.formSchema || {}).sort((a, b) => Number((a.match(/^item_(\d+)_/) || [0, 99])[1]) - Number((b.match(/^item_(\d+)_/) || [0, 99])[1])).forEach((k) => {
        if (!skip.has(k) && this.formSchema[k] && this.formSchema[k].field_type) out[k] = this.formSchema[k];
      });
      return out;
    },
    submissionTreeData() {
      return (this.submissionCatalog || []).map((item) => ({
        id: item.code,
        label: item.label,
        required_level: item.required_level,
        children: [],
      }));
    },
    filteredSubmissions() {
      const code = String(this.selectedSubmissionCategory || "");
      if (!code) return this.submissions || [];
      return (this.submissions || []).filter((x) => x.material_category === code);
    },
    selectedSubmissionLabel() {
      const node = (this.submissionTreeData || []).find(x => x.id === this.selectedSubmissionCategory);
      return node ? node.label : '未指定目录，将自动分类';
    },
    pagedParseDetails() {
      return this.parseDetailGroups.slice((this.parseDetailsPage - 1) * 20, this.parseDetailsPage * 20);
    },
    hasRunningSubmissionParse() {
      if (this.parsingBatch) return true;
      return Object.keys(this.filingParseTaskStates || {}).some((key) => (
        (key === "submission-batch" || key.startsWith("submission:"))
        && this.isFilingParseTaskRunning(key)
      ));
    },
    stabilityLimitCheck() {
      return (((this.reviewResult || {}).stability_trend_analysis || {}).limit_check) || {};
    },
    stabilityLimitAlertType() {
      const statusCode = String(this.stabilityLimitCheck.status_code || "").trim();
      if (statusCode === "out_of_spec" || this.stabilityLimitCheck.status === "已超限") return "error";
      if (statusCode === "within_spec" || this.stabilityLimitCheck.status === "未超限") return "success";
      return "warning";
    },
    stabilityLimitAlertTitle() {
      const check = this.stabilityLimitCheck;
      if (this.stabilityLimitAlertType === "error") return `超限提醒：发现 ${Number(check.out_of_spec_count || 0)} 条明确超限结果`;
      if (this.stabilityLimitAlertType === "success") return `限度检查：${Number(check.checked_count || 0)} 条结果均未超限`;
      return `限度检查待确认：${Number(check.undecidable_count || 0)} 条无法自动判断`;
    },
  },
  watch: {
    projectId() {
      this.cancelAllFilingParseTaskPolling();
      if (this.reviewPollTimer) clearTimeout(this.reviewPollTimer);
      const serial = this.dataRequestSerial;
      Object.assign(this.$data, this.$options.data.call(this));
      this.dataRequestSerial = serial;
      this._formSavePromise = null;
      this.loadBaseData();
    },
  },
  beforeRouteLeave(to, from, next) {
    this.confirmDiscardForm().then(ok => next(ok ? undefined : false));
  },
  beforeRouteUpdate(to, from, next) {
    if (to.params.projectId === from.params.projectId && to.query.run_id === from.query.run_id) return next();
    if (to.params.projectId === from.params.projectId && to.query.run_id && to.query.run_id !== (this.reviewResult || {}).run_id) {
      this.confirmDiscardForm().then(async ok => {
        if (!ok) return next(false);
        try {
          const applied = await this.selectRun(to.query.run_id, {discardApproved:true, syncRoute:false});
          next(applied === false ? false : undefined);
        } catch (_) { next(false); }
      });
      return;
    }
    this.confirmDiscardForm().then(ok => next(ok ? undefined : false));
  },
  beforeDestroy() {
    this.pageDisposed = true;
    window.removeEventListener('beforeunload', this.warnUnsavedForm);
    this.parsedMarkdownRequestToken += 1;
    this.referenceParsedRequestToken += 1;
    this.reviewPollToken += 1;
    this.dataRequestTokens = {};
    this.cancelAllFilingParseTaskPolling();
    if (this.reviewPollTimer) clearTimeout(this.reviewPollTimer);
  },
  mounted() {
    window.addEventListener('beforeunload', this.warnUnsavedForm);
    this.loadBaseData();
  },
  methods: {
    warnUnsavedForm(event) {
      if (!this.formDirty && !this.manualDirty && !this.savingManual && !this.numericDirty && !this.savingNumeric && !this.parseReviewDirty) return;
      event.preventDefault();
      event.returnValue = '';
    },
    async confirmDiscardForm() {
      if (!this.formDirty && !this.manualDirty && !this.savingManual && !this.numericDirty && !this.savingNumeric && !this.parseReviewDirty) return true;
      try {
        await this.$confirm('申请表、人工意见、数值确认或解析核对有未保存修改（或正在保存），离开后未保存内容将丢失。是否离开？', '未保存修改', { type: 'warning' });
        return true;
      } catch (_) { return false; }
    },
    beginDataRequest(key) {
      const token = ++this.dataRequestSerial;
      this.$set(this.dataRequestTokens, key, token);
      this.$set(this.dataRequestProjects, key, String(this.projectId || ''));
      return token;
    },
    isDataRequestCurrent(key, token) {
      return !this.pageDisposed && !this._isDestroyed
        && this.dataRequestProjects[key] === String(this.projectId || '')
        && Number(this.dataRequestTokens[key] || 0) === Number(token || 0);
    },
    async readCurrentData(key, load, apply) {
      const token = this.beginDataRequest(key);
      const isCurrent = () => this.isDataRequestCurrent(key, token);
      try {
        const result = await load(isCurrent);
        if (isCurrent()) return await apply(result, isCurrent);
      } catch (error) {
        if (isCurrent()) this.$message.error('读取失败，已保留当前内容，请重试');
      }
    },
    filingParseCancellation(message = "解析操作已取消") {
      const error = new Error(message);
      error.isFilingParseTaskCancelled = true;
      return error;
    },
    isFilingParseCancellation(error) {
      return !!(error && error.isFilingParseTaskCancelled);
    },
    filingParseErrorMessage(error, fallback = "解析任务失败") {
      const raw = String(
        (error && error.userMessage)
        || (error && error.response && error.response.data && error.response.data.message)
        || (error && error.message)
        || "",
      ).trim();
      if (/task not found/i.test(raw)) return "解析任务不存在或已过期，请重新发起";
      if (/project not found/i.test(raw)) return "项目不存在或已被删除";
      if (/file is required/i.test(raw)) return "请先上传需要解析的文件";
      // 长服务诊断保存在任务明细中，浮动提示不展开逐页内容。
      return raw.length > 120 ? `${fallback}，请查看解析诊断详情` : (raw || fallback);
    },
    submissionParseTaskKey(row) {
      return `submission:${String((row && row.doc_id) || "").trim()}`;
    },
    referenceParseTaskKey(row) {
      return `reference:${String((row && row.doc_id) || "").trim()}`;
    },
    isFilingParseTaskRunning(key) {
      const state = (this.filingParseTaskStates || {})[String(key || "")] || {};
      return ["starting", "pending", "running"].includes(String(state.status || "").toLowerCase());
    },
    cancelFilingParseTaskPolling(key, message = "解析任务已被新操作取代") {
      const taskKey = String(key || "");
      const poller = (this.filingParseTaskPollers || {})[taskKey];
      if (!poller) return;
      if (poller.timer) clearTimeout(poller.timer);
      this.$delete(this.filingParseTaskPollers, taskKey);
      if (typeof poller.reject === "function") {
        const reject = poller.reject;
        poller.reject = null;
        reject(this.filingParseCancellation(message));
      }
    },
    cancelAllFilingParseTaskPolling() {
      const keys = new Set([
        ...Object.keys(this.filingParseTaskPollers || {}),
        ...Object.keys(this.filingParseTaskEpochs || {}),
      ]);
      keys.forEach((key) => {
        this.cancelFilingParseTaskPolling(key, "页面已切换，停止更新原解析任务");
        this.$set(this.filingParseTaskEpochs, key, Number(this.filingParseTaskEpochs[key] || 0) + 1);
      });
    },
    beginFilingParseOperation(key, projectId) {
      const taskKey = String(key || "");
      this.cancelFilingParseTaskPolling(taskKey);
      const epoch = Number(this.filingParseTaskEpochs[taskKey] || 0) + 1;
      this.$set(this.filingParseTaskEpochs, taskKey, epoch);
      this.$set(this.filingParseTaskStates, taskKey, {
        task_id: "",
        bound_project_id: String(projectId || ""),
        status: "starting",
        message: "正在创建解析任务",
      });
      return epoch;
    },
    isFilingParseOperationCurrent(key, epoch, projectId) {
      return (
        Number(this.filingParseTaskEpochs[String(key || "")] || 0) === Number(epoch || 0)
        && String(this.projectId || "") === String(projectId || "")
      );
    },
    markFilingParseOperationFailed(key, epoch, projectId, error, fallback) {
      if (!this.isFilingParseOperationCurrent(key, epoch, projectId)) return;
      const previous = this.filingParseTaskStates[key] || {};
      this.$set(this.filingParseTaskStates, key, {
        ...previous,
        status: "failed",
        message: this.filingParseErrorMessage(error, fallback),
        error_message: this.filingParseErrorMessage(error, fallback),
      });
    },
    extractFilingParseTaskId(response) {
      return String((((response || {}).data || {}).task_id) || "").trim();
    },
    waitForFilingParseTask(taskId, { key, projectId, epoch } = {}) {
      const taskKey = String(key || taskId || "");
      const expectedProjectId = String(projectId || "");
      const expectedEpoch = Number(epoch || this.filingParseTaskEpochs[taskKey] || 0);
      const normalizedTaskId = String(taskId || "").trim();
      if (!normalizedTaskId) return Promise.reject(new Error("解析任务未返回 task_id"));
      if (!this.isFilingParseOperationCurrent(taskKey, expectedEpoch, expectedProjectId)) {
        return Promise.reject(this.filingParseCancellation());
      }

      const previous = this.filingParseTaskStates[taskKey] || {};
      this.$set(this.filingParseTaskStates, taskKey, {
        ...previous,
        task_id: normalizedTaskId,
        bound_project_id: expectedProjectId,
        status: "pending",
        message: "解析任务已创建，等待执行",
      });

      return new Promise((resolve, reject) => {
        const control = {
          taskId: normalizedTaskId,
          projectId: expectedProjectId,
          epoch: expectedEpoch,
          timer: null,
          reject,
          cursor: 0,
          consecutiveErrors: 0,
        };
        this.$set(this.filingParseTaskPollers, taskKey, control);

        const isCurrent = () => (
          this.filingParseTaskPollers[taskKey] === control
          && this.isFilingParseOperationCurrent(taskKey, expectedEpoch, expectedProjectId)
        );
        const release = () => {
          if (control.timer) clearTimeout(control.timer);
          control.timer = null;
          control.reject = null;
          if (this.filingParseTaskPollers[taskKey] === control) {
            this.$delete(this.filingParseTaskPollers, taskKey);
          }
        };
        const fail = (error) => {
          release();
          reject(error);
        };
        const scheduleNext = () => {
          if (!isCurrent()) return;
          control.timer = setTimeout(() => {
            control.timer = null;
            pollOnce();
          }, 2000);
        };
        const pollOnce = async () => {
          if (!isCurrent()) {
            fail(this.filingParseCancellation());
            return;
          }
          try {
            const response = await getFilingParseTaskProgress(
              normalizedTaskId,
              { cursor: control.cursor },
              { silentError: true },
            );
            if (!isCurrent()) {
              fail(this.filingParseCancellation());
              return;
            }
            const snapshot = (response && response.data) || {};
            if (snapshot.task_id && String(snapshot.task_id) !== normalizedTaskId) {
              throw new Error("解析任务返回了不匹配的 task_id");
            }
            if (snapshot.project_id && expectedProjectId && String(snapshot.project_id) !== expectedProjectId) {
              throw new Error("解析任务所属项目与当前页面不一致");
            }
            control.consecutiveErrors = 0;
            control.cursor = Number(snapshot.next_cursor || snapshot.cursor || control.cursor || 0);
            const status = String(snapshot.status || "pending").toLowerCase();
            this.$set(this.filingParseTaskStates, taskKey, {
              ...(this.filingParseTaskStates[taskKey] || {}),
              ...snapshot,
              task_id: normalizedTaskId,
              bound_project_id: expectedProjectId,
              status,
            });
            if (status === "completed") {
              release();
              resolve(snapshot);
              return;
            }
            if (["failed", "cancelled", "interrupted"].includes(status)) {
              const result = snapshot.result || {};
              const error = new Error(
                snapshot.error_message || snapshot.message || result.message || "解析任务执行失败",
              );
              error.userMessage = error.message;
              fail(error);
              return;
            }
            scheduleNext();
          } catch (error) {
            if (!isCurrent()) {
              fail(this.filingParseCancellation());
              return;
            }
            control.consecutiveErrors += 1;
            if (control.consecutiveErrors < 5) {
              this.$set(this.filingParseTaskStates, taskKey, {
                ...(this.filingParseTaskStates[taskKey] || {}),
                message: `任务进度查询暂时失败，正在重试（${control.consecutiveErrors}/5）`,
              });
              scheduleNext();
              return;
            }
            const finalError = new Error("解析任务进度查询失败，后台任务可能仍在执行，请稍后刷新列表");
            finalError.userMessage = this.filingParseErrorMessage(error, finalError.message);
            fail(finalError);
          }
        };
        pollOnce();
      });
    },
    pretty(obj) {
      try {
        if (obj === '') return '（空白）';
        return JSON.stringify(obj == null ? {} : obj, null, 2);
      } catch (_) {
        return String(obj || "");
      }
    },
    renderMarkdown(source) {
      const text = String(source || "").trim();
      if (!text) return '<div class="meta-line">暂无内容</div>';
      return md.render(text);
    },
    displayFieldTitle(key, field) {
      if (key === "special_statement") {
        return "其他特别申明事项";
      }
      const match = String(key || "").match(/^item_(\d+)_/);
      const seq = match ? match[1] : "";
      const name = (field && field.field_name) || "";
      return seq ? `${seq}. ${name || "未命名字段"}` : (name || "未命名字段");
    },
    formatSubFieldLabel(key) {
      const map = {
        shared_application_form_no: "共用申请表编号或说明",
        class_no: "注册分类号",
        other_description: "其他事项说明",
        trade_name: "商品名称",
        primary_packaging_material: "直接接触药品包装材料和容器",
        packaging_specification: "包装规格",
        original_validity_period: "原有效期",
        proposed_validity_period: "拟延长后有效期",
        validity_unit: "单位",
        storage_condition: "贮藏条件",
        active_ingredients: "活性成分",
        excipients: "辅料",
        has_change: "是否有变更",
        sample_inspection_no: "检品编号",
        category: "分类",
        description: "描述",
        applicant_name: "申请人名称",
        license_no: "许可证号",
        credit_code: "统一社会信用代码",
        organization_code: "组织机构代码",
        credit_or_org_code: "信用代码/组织机构代码（原表组合项）",
        contact: "联系人",
        address: "地址",
        legal_representative: "法定代表人",
        registered_address: "注册地址",
        production_address: "生产地址",
        mailing_address: "通讯地址",
        research_lead: "研究负责人",
        registration_contact: "注册申请负责人",
        phone: "联系电话",
        mobile: "手机",
        manufacturer_name: "生产企业名称",
        organization_name: "委托机构名称",
        material_name: "原/辅/包材名称",
        register_no: "登记号",
        accept_no: "受理号",
        manufacturer: "生产企业",
      };
      return map[String(key || "")] || String(key || "");
    },
    flattenTreeOptions(nodes) {
      const out = [];
      const queue = Array.isArray(nodes) ? [...nodes] : [];
      while (queue.length) {
        const n = queue.shift();
        if (!n || typeof n !== "object") continue;
        if (n.code && n.label) out.push({ code: n.code, label: n.label });
        if (Array.isArray(n.children)) queue.push(...n.children);
      }
      return out;
    },
    tableColumns(field) {
      const cols = (((field || {}).sub_fields || {}).columns || []).slice();
      return cols.length ? cols : ["col1", "col2", "col3"];
    },
    addTableRow(field) {
      if (!Array.isArray(field.table_rows)) this.$set(field, "table_rows", []);
      const row = {};
      this.tableColumns(field).forEach((c) => {
        row[c] = "";
      });
      field.table_rows.push(row);
      this.markFormEdited(field, "table_rows");
    },
    async loadBaseData() {
      const projectId = this.projectId;
      return this.readCurrentData('base', async isCurrent => {
        await this.restoreParseTasks();
        if (!isCurrent()) return null;
        return Promise.all([getFilingChangeProjectDetail(projectId, { silentError: true }), getApplicationForm(projectId, { silentError: true })]);
      }, async ([detail, formRes], isCurrent) => {
      this.project = (detail && detail.data) || {};
      this.applyFormSchema((((formRes || {}).data || {}).form_json) || {});
      this.formLegacyRoleNotes = (((formRes || {}).data || {}).legacy_role_notes) || [];
      this.formParseAttempt = (((formRes || {}).data || {}).latest_attempt) || {};
      this.formParseStatus = (((formRes || {}).data || {}).parse_status) || 'not_parsed';
      this.formParseResolution = (((formRes || {}).data || {}).parse_resolution) || {};
      this.formSource = (((formRes || {}).data || {}).effective_source) || {};
      await Promise.all([this.loadSubmissionCatalog(), this.loadSubmissions(), this.loadReferenceTaxonomy(), this.loadLatestResult(), this.loadRunHistory(), this.loadReferenceMaterials(), this.loadRules(), this.loadReport()]);
      });
    },
    parseContentLabel(status) {
      return ({ success: '存在可用结果', partial: '部分成功，存在待核对内容', failed: '解析失败', pending: '尚未解析', not_parsed: '尚未解析' })[status] || '尚未解析';
    },
    showParseOutcome(key) {
      const state = this.filingParseTaskStates[key] || {};
      const type = this.parseAttemptType(state);
      this.$message[type === 'error' ? 'error' : type === 'warning' ? 'warning' : type === 'success' ? 'success' : 'info'](this.parseAttemptLabel(state));
    },
    attemptState(data) {
      return { status: data.content_status === 'failed' ? 'failed' : 'completed', result: { data } };
    },
    parseStatusType(status) {
      return ({ success: 'success', partial: 'warning', failed: 'danger' })[status] || 'info';
    },
    parseAttemptType(state) {
      if (state.status === 'failed') return 'error';
      if (['pending', 'running'].includes(state.status)) return 'info';
      const result = state.result || {}, data = result.data || {};
      const status = result.content_status || data.content_status;
      return ({ success: 'success', partial: 'warning', failed: 'error' })[status] || 'info';
    },
    indexStatusLabel(status) {
      return ({ success: '索引完成', indexed: '索引完成', completed: '索引完成', partial: '索引部分完成', failed: '索引失败', pending: '待建索引', running: '索引中', not_indexed: '尚未索引' })[status] || '未记录';
    },
    compactPages(values) {
      const pages = [...new Set((values || []).filter(x => Number.isInteger(x) && x > 0))].sort((a, b) => a - b);
      const ranges = [];
      for (let i = 0; i < pages.length; i++) {
        const start = pages[i];
        while (i + 1 < pages.length && pages[i + 1] === pages[i] + 1) i++;
        ranges.push(start === pages[i] ? String(start) : `${start}–${pages[i]}`);
      }
      return ranges.join('、');
    },
    parseAttemptLabel(state) {
      const result = state.result || {};
      const data = result.data || {};
      const status = result.content_status || data.content_status;
      if (['pending', 'running'].includes(state.status)) return '解析正在进行，可稍后查看进度';
      const batch = Array.isArray(data.success) || Array.isArray(data.failed);
      if (batch) {
        const good = data.success || [], failed = data.failed || [];
        const partial = good.filter(x => x.content_status === 'partial').length;
        return `${state.status === 'failed' ? '批量任务失败或中断' : '批量解析完成'}：成功${good.length - partial}份、部分成功${partial}份、失败${failed.length}份${(data.pending || []).length ? `、待处理${data.pending.length}份` : ''}；详情中查看各文件结果`;
      }
      if (state.status === 'failed') return ['success', 'partial'].includes(status)
        ? '已有解析内容保存；本次任务中断或后续处理失败，请查看详情'
        : '本次解析失败；此前结果如存在仍予保留，请查看详情';
      return `${this.parseContentLabel(status)}${this.parseDiagnosticsLabel(data.parse_diagnostics) ? '；' + this.parseDiagnosticsLabel(data.parse_diagnostics) : ''}`;
    },
    parseDiagnosticsLabel(diagnostics) {
      const d = diagnostics || {}, errors = d.errors || [];
      const pages = [...new Set([...(d.failed_pages || []), ...errors.map(x => x.page)].filter(x => Number.isInteger(x) && x > 0))];
      if (!pages.length && !errors.length) return '';
      return `${pages.length}页需核对，${errors.length}条诊断`;
    },
    diagnosticMessage(error) {
      const stage = error.stage;
      const code = ['ocr_quality', 'ocr_coverage'].includes(stage) ? stage : error.code;
      const known = {
        ocr_quality: '文字低置信度，需核对原页', ocr_coverage: '部分可见区域尚未完整识别',
        numeric_uncertain: '数值核验存在分歧或尚未完成，需核对原页',
        ocr_timeout: 'OCR 识别超时', ocr_unreachable: 'OCR 服务无法连接', ocr_http: 'OCR 服务请求失败',
        ocr_busy: 'OCR 服务繁忙或排队超时，请稍后重试',
        ocr_invalid_response: 'OCR 响应格式或坐标不完整', ocr_empty: '未得到可用文字，请核对原件清晰度及图像内容',
        ocr_response: 'OCR 异常原因待核查', table_structure_unresolved: '文字已识别，表格结构待确认',
        field_region_crossing: '文字跨越字段区域，归属待确认', field_region_unassigned: '文字字段归属待确认',
      };
      return known[code] || error.message || '解析问题原因待核查';
    },
    openFileParseDetails(row) {
      const attempt = row.latest_attempt || {};
      const effective = { ...row, content_status: row.parse_status };
      const data = attempt.content_status === 'failed'
        ? { failed: [{ ...attempt, file_name: `${row.file_name || row.title}（本次尝试）` }], success: ['success', 'partial'].includes(row.parse_status) ? [{ ...effective, file_name: `${row.file_name || row.title}（此前有效结果）` }] : [] }
        : effective;
      this.openParseDetails(this.attemptState(data), row.file_name || row.title || row.doc_id);
    },
    openParseDetails(state, title) {
      const result = state.result || {}, data = result.data || {};
      const items = Array.isArray(data.success) || Array.isArray(data.failed)
        ? [...(data.success || []), ...(data.failed || []), ...(data.pending || []).map(x => typeof x === 'object' ? { ...x, message: '尚未处理' } : { doc_id: x, message: '尚未处理' })] : [data];
      const groups = new Map();
      items.forEach((item, index) => {
        const file = item.file_name || item.source_file_name || ((this.submissions || []).find(x => x.doc_id === item.doc_id) || {}).file_name || item.doc_id || title;
        const d = item.parse_diagnostics || {};
        const errors = [...(d.errors || [])];
        if (state.status === 'failed' && state.error_message && errors.length) errors.push({ stage: 'task', message: state.error_message });
        (d.failed_pages || []).filter(page => !errors.some(e => e.page === page)).forEach(page => errors.push({ page, message: '页面存在待核查问题，历史记录未提供明细' }));
        if (!errors.length) errors.push({ message: item.error || item.message || state.error_message || this.parseContentLabel(item.content_status || result.content_status || item.parse_status) });
        errors.forEach(error => {
          const message = this.diagnosticMessage(error);
          const key = JSON.stringify([index, error.stage || '', message]);
          if (!groups.has(key)) groups.set(key, { file, message, entries: [] });
          const available = (d.available_pages || []).includes(error.page);
          groups.get(key).entries.push({ ...error, availability: available ? '已有内容，需核对' : (d.missing_pages || []).includes(error.page) ? '页面未取得内容' : error.page && (d.failed_pages || []).includes(error.page) ? '内容可用性需核查' : '详见本次结果' });
        });
      });
      this.parseDetailGroups = [...groups.values()];
      this.parseDetailsTitle = `${title} · ${this.parseAttemptLabel(state)}`;
      this.parseDetailsPage = 1;
      this.parseDetailsVisible = true;
    },
    clearSubmissionCategory() {
      this.selectedSubmissionCategory = '';
      if (this.$refs.submissionTree) this.$refs.submissionTree.setCurrentKey(null);
    },
    async restoreParseTasks() {
      const token = this.beginDataRequest('restore-tasks');
      const projectId = String(this.projectId || '');
      const response = await getFilingProjectParseTasks(projectId, { silentError: true });
      if (!this.isDataRequestCurrent('restore-tasks', token)) return;
      const seen = new Set();
      for (const task of (response.data || [])) {
        const key = task.task_type.includes('application_form') ? 'application-form' :
          (task.task_type === 'parse_submissions_batch' ? 'submission-batch' : `${task.task_type === 'parse_reference_material' ? 'reference' : 'submission'}:${task.source_doc_id}`);
        if (seen.has(key)) continue;
        seen.add(key);
        if (this.filingParseTaskPollers[key]) continue;
        this.$set(this.filingParseTaskStates, key, task);
        if (['pending', 'running'].includes(task.status)) {
          const epoch = this.beginFilingParseOperation(key, projectId);
          this.waitForFilingParseTask(task.task_id, { key, projectId, epoch })
            .catch(() => {})
            .finally(() => { if (this.isDataRequestCurrent('restore-tasks', token)) this.loadBaseData(); });
        }
      }
    },
    async loadReferenceTaxonomy() {
      return this.readCurrentData('taxonomy', () => getFilingReferenceTaxonomy({ silentError: true }), res => {
      this.referenceTaxonomy = (res && res.data) || { drug_categories: [], material_types: [], change_items: [] };
      if (!this.referenceForm.drug_category && (this.referenceTaxonomy.drug_categories || []).length) {
        this.referenceForm.drug_category = this.referenceTaxonomy.drug_categories[0];
      }
      });
    },
    async loadSubmissionCatalog() {
      return this.readCurrentData('catalog', () => getFilingSubmissionCatalog(this.projectId, { silentError: true }), (res, isCurrent) => {
      const data = (res && res.data) || {};
      this.submissionCatalog = data.catalog || [];
      this.submissionCategoryMeta = data.category_meta || {};
      if (this.selectedSubmissionCategory && !this.submissionTreeData.some(x => x.id === this.selectedSubmissionCategory)) {
        this.clearSubmissionCategory();
        this.$message.warning('此前选择的目录已不存在，请重新选择；当前为自动分类');
      }
      this.$nextTick(() => {
        if (isCurrent() && this.$refs.submissionTree) this.$refs.submissionTree.setCurrentKey(this.selectedSubmissionCategory || null);
      });
      });
    },
    onSubmissionTreeNodeClick(node) {
      this.selectedSubmissionCategory = "";
      if (node && node.id) {
        this.selectedSubmissionCategory = String(node.id);
      }
    },
    markFormEdited(field, path) {
      const key = Object.keys(this.formSchema).find(key => this.formSchema[key] === field);
      if (!key) return;
      this.$set(this.formEdits, key, { ...(this.formEdits[key] || {}), [path]: ++this.formEditSerial });
      this.formDirty = true;
      this.$set(field, "manual_modified", true);
    },
    applyFormSchema(schema) {
      // 后台刷新只更新未编辑路径；保存期间继续输入也不能被返回结果覆盖。
      const clone = value => JSON.parse(JSON.stringify(value));
      const loaded = {};
      for (const [key, field] of Object.entries(schema)) {
        if (!field || !field.field_type) continue;
        loaded[key] = clone({ value: field.value || '', selected_values: field.selected_values || [], table_rows: field.table_rows || [],
          ...Object.fromEntries(Object.entries(field.sub_fields || {}).filter(([key]) => key !== 'columns').map(([key, value]) => ['sub_fields.' + key, value])) });
      }
      for (const [key, edits] of Object.entries(this.formEdits)) {
        const local = this.formSchema[key];
        if (!local || !schema[key]) continue;
        for (const path of Object.keys(edits)) {
          const sub = path.startsWith('sub_fields.') ? path.slice(11) : null;
          if (sub) schema[key].sub_fields[sub] = clone(local.sub_fields[sub]);
          else schema[key][path] = clone(local[path]);
          if (this.formLoadedValues[key] && path in this.formLoadedValues[key]) loaded[key][path] = clone(this.formLoadedValues[key][path]);
        }
        schema[key].manual_modified = true;
      }
      this.formSchema = schema;
      this.formLoadedValues = loaded;
      this.formDirty = Object.values(this.formEdits).some(paths => Object.keys(paths).length > 0) || Object.keys(this.formResolutions).length > 0;
    },
    formFieldStatus(field) {
      if (Object.keys(field.candidates || {}).length || (field.cross_checks || []).some(check => check.status.includes('存在冲突'))) return "存在冲突";
      if (field.manual_modified) return "人工已修改";
      if (field.recognition_status === 'previous_result' || Object.values(field.value_sources || {}).some(source => source.recognition_status === 'previous_result')) return "此前识别结果（本次未取得）";
      if ((field.semantic_issues || []).length || (field.cross_checks || []).some(check => (check.status || '').includes('待核对'))) return "待核对";
      if (field.reported_value) return "已提取（前后关系需核对）";
      if (field.recognition_status === 'explicit_blank') return "原件明确空白";
      const content = [field.value, ...(field.selected_values || []), ...(field.table_rows || []),
        ...Object.entries(field.sub_fields || {}).filter(([k]) => !["columns", "validity_unit"].includes(k)).map(([, v]) => v)];
      return content.some(v => v !== "" && v != null) ? "已提取" : "未识别";
    },
    recognitionLabel(status) {
      return ({ extracted: '已提取', explicit_blank: '原件明确空白', conflict: '识别冲突，待核对',
        unrecognized: '未识别', legacy_unspecified: '历史字段含义待核对', previous_result: '此前识别结果（本次未取得）', manual: '人工填写', default: '默认单位', mixed_sources: '逐项来源不同' })[status] || '待核对';
    },
    async resolveFormCandidate(key, path, action) {
      this.$set(this.formResolutions, key, { ...(this.formResolutions[key] || {}), [path]: { action, candidate: this.formSchema[key].candidates[path] } });
      this.formDirty = true;
      await this.saveForm();
    },
    async saveForm() {
      if (!this._formSavePromise) {
        const projectId = this.projectId;
        const promise = this.persistFormEdits()
          .catch(error => {
            if (this.projectId === projectId && !this.pageDisposed) this.$message.error(this.filingParseErrorMessage(error, '保存失败，本页输入仍保留'));
            return false;
          })
          .finally(() => { if (this._formSavePromise === promise) this._formSavePromise = null; });
        this._formSavePromise = promise;
      }
      return this._formSavePromise;
    },
    async persistFormEdits() {
      const token = this.beginDataRequest('save-form');
      const projectId = this.projectId;
      this.beginDataRequest('base');
      this.savingForm = true;
      const edits = JSON.parse(JSON.stringify(this.formEdits));
      const payload = JSON.parse(JSON.stringify({ form_json: this.formSchema, resolutions: this.formResolutions,
        expected_values: this.formLoadedValues,
        edited_paths: Object.fromEntries(Object.entries(edits).map(([key, paths]) => [key, Object.keys(paths)])) }));
      try {
        const response = await saveApplicationForm(projectId, payload, { silentError: true });
        if (!this.isDataRequestCurrent('save-form', token)) return false;
        this.beginDataRequest('base');
        for (const [key, paths] of Object.entries(edits)) {
          for (const [path, serial] of Object.entries(paths)) {
            if ((this.formEdits[key] || {})[path] === serial) this.$delete(this.formEdits[key], path);
            else if ((this.formEdits[key] || {})[path] && response?.data?.form_json?.[key]) {
              const saved = response.data.form_json[key];
              const value = path.startsWith('sub_fields.') ? saved.sub_fields[path.slice(11)] : saved[path];
              this.$set(this.formLoadedValues[key], path, JSON.parse(JSON.stringify(value)));
            }
          }
          if (!Object.keys(this.formEdits[key] || {}).length) this.$delete(this.formEdits, key);
        }
        if (response && response.data && response.data.form_json) this.applyFormSchema(response.data.form_json);
        if (response && response.data) {
          this.formParseStatus = response.data.parse_status || 'not_parsed';
          this.formParseResolution = response.data.parse_resolution || {};
          this.formParseAttempt = response.data.latest_attempt || {};
          this.formSource = response.data.effective_source || {};
          this.formLegacyRoleNotes = response.data.legacy_role_notes || [];
        }
        for (const [key, paths] of Object.entries(payload.resolutions)) {
          for (const [path, action] of Object.entries(paths)) {
            if (JSON.stringify((this.formResolutions[key] || {})[path]) === JSON.stringify(action)) this.$delete(this.formResolutions[key], path);
          }
          if (!Object.keys(this.formResolutions[key] || {}).length) this.$delete(this.formResolutions, key);
        }
        this.formDirty = Object.keys(this.formEdits).length > 0 || Object.keys(this.formResolutions).length > 0;
        this.$message.success("申请表已保存");
        return true;
      } finally {
        if (this.isDataRequestCurrent('save-form', token)) this.savingForm = false;
      }
    },
    async reparseForm() {
      if (this.formDirty && !await this.saveForm()) return;
      const projectId = String(this.projectId || "");
      const key = "application-form";
      const epoch = this.beginFilingParseOperation(key, projectId);
      this.importingForm = true;
      try {
        const response = await startApplicationFormParse(projectId, { silentError: true });
        await this.waitForFilingParseTask(this.extractFilingParseTaskId(response), { key, projectId, epoch });
        if (!this.isFilingParseOperationCurrent(key, epoch, projectId)) return;
        this.showParseOutcome(key);
        await this.loadBaseData();
      } finally {
        if (this.isFilingParseOperationCurrent(key, epoch, projectId)) this.importingForm = false;
      }
    },
    async onFormFileChange(file) {
      const raw = file && file.raw;
      if (!raw) return;
      const name = String(raw.name || "");
      const ext = (name.split(".").pop() || "").toLowerCase();
      if (!["doc", "docx", "pdf"].includes(ext)) {
        this.$message.warning("申请表仅支持上传 doc、docx、pdf 文件");
        return;
      }
      const fd = new FormData();
      fd.append("file", raw);
      const projectId = String(this.projectId || "");
      const taskKey = "application-form";
      const epoch = this.beginFilingParseOperation(taskKey, projectId);
      this.importingForm = true;
      let parseCompleted = false;
      try {
        const startResponse = await startApplicationFormImport(projectId, fd, { silentError: true });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        // 先取得任务准入结果；保存等待项目锁时不会错过同件正在运行的任务。
        this.$set(this.filingParseTaskStates, taskKey, startResponse.data || {});
        if (this.formDirty) await this.saveForm();
        await this.waitForFilingParseTask(this.extractFilingParseTaskId(startResponse), {
          key: taskKey,
          projectId,
          epoch,
        });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        parseCompleted = true;
        this.showParseOutcome(taskKey);
        await this.loadBaseData();
      } catch (e) {
        if (this.isFilingParseCancellation(e)) return;
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId) || this.pageDisposed) return;
        if (!parseCompleted) {
          this.markFilingParseOperationFailed(taskKey, epoch, projectId, e, "申请表导入或解析失败");
        }
        this.$message.error(this.filingParseErrorMessage(e, parseCompleted ? "申请表刷新失败" : "申请表导入或解析失败"));
        if (String(this.projectId || '') === projectId) await this.loadBaseData();
      } finally {
        if (this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) {
          this.importingForm = false;
        }
      }
    },
    onSubmissionFileChange(_file, fileList) {
      this.submissionFileList = fileList || [];
    },
    onSubmissionSelectionChange(rows) {
      this.selectedSubmissionRows = rows || [];
    },
    async uploadSubmissions() {
      const files = (this.submissionFileList || []).map((item) => item.raw).filter(Boolean);
      if (!files.length) return this.$message.warning("请选择申报资料文件");
      const category = this.selectedSubmissionCategory || "";
      this.uploading = true;
      try {
        const fd = new FormData();
        files.forEach((f) => fd.append("files", f));
        fd.append("material_category", category);
        await uploadFilingSubmissions(this.projectId, fd);
        this.$message.success("上传成功");
        this.submissionFileList = [];
        await this.loadSubmissions();
      } finally {
        this.uploading = false;
      }
    },
    async markCategoryNotApplicable() {
      const code = this.selectedSubmissionCategory;
      if (!code) return this.$message.warning("请先在目录树选择一个资料目录");
      await setFilingSubmissionCategoryNotApplicable(this.projectId, { category_code: code, reason: this.naReasonInput || "" });
      this.$message.success("已设置不适用");
      await this.loadSubmissionCatalog();
      await this.runCompletenessCheck();
    },
    async runCompletenessCheck() {
      this.checkingCompleteness = true;
      try {
        const res = await checkFilingSubmissionCompleteness(this.projectId);
        this.completenessResult = (res && res.data) || null;
      } finally {
        this.checkingCompleteness = false;
      }
    },
    async toggleSubmissionReviewEnabled(row, enabled) {
      await updateFilingSubmissionMetadata(this.projectId, row.doc_id, { review_enabled: !!enabled });
      row.review_enabled = !!enabled;
      this.$message.success("已更新参与审评状态");
    },
    async loadSubmissions() {
      const requestToken = this.beginDataRequest("submissions");
      try {
        const res = await listFilingSubmissions(
          this.projectId,
          { page: 1, page_size: 100 },
          { silentError: true },
        );
        if (!this.isDataRequestCurrent("submissions", requestToken)) return;
        this.submissions = (((res || {}).data || {}).list) || [];
      } catch (error) {
        if (!this.isDataRequestCurrent("submissions", requestToken)) return;
        this.$message.error((error && error.userMessage) || "申报资料列表加载失败");
      }
    },
    async parseSubmission(row) {
      const docId = String((row && row.doc_id) || "").trim();
      if (!docId) return this.$message.warning("未找到需要解析的申报资料");
      const projectId = String(this.projectId || "");
      const taskKey = this.submissionParseTaskKey(row);
      const epoch = this.beginFilingParseOperation(taskKey, projectId);
      try {
        const startResponse = await startFilingSubmissionParse(projectId, docId, { silentError: true });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        await this.waitForFilingParseTask(this.extractFilingParseTaskId(startResponse), {
          key: taskKey,
          projectId,
          epoch,
        });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        this.showParseOutcome(taskKey);
        await this.loadSubmissions();
      } catch (error) {
        if (this.isFilingParseCancellation(error)) return;
        this.markFilingParseOperationFailed(taskKey, epoch, projectId, error, "申报资料解析失败");
        this.$message.error(this.filingParseErrorMessage(error, "申报资料解析失败"));
        await this.loadSubmissions();
      }
    },
    async batchParseSubmissions() {
      const selected = (this.selectedSubmissionRows || []).map((x) => x.doc_id).filter(Boolean);
      const projectId = String(this.projectId || "");
      const taskKey = "submission-batch";
      const epoch = this.beginFilingParseOperation(taskKey, projectId);
      this.parsingBatch = true;
      try {
        const payload = selected.length ? { doc_ids: selected } : { all: true };
        const startResponse = await startBatchFilingSubmissionsParse(projectId, payload, { silentError: true });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        await this.waitForFilingParseTask(this.extractFilingParseTaskId(startResponse), {
          key: taskKey,
          projectId,
          epoch,
        });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        this.showParseOutcome(taskKey);
        await this.loadSubmissions();
      } catch (error) {
        if (this.isFilingParseCancellation(error)) return;
        this.markFilingParseOperationFailed(taskKey, epoch, projectId, error, "批量解析申报资料失败");
        this.$message.error(this.filingParseErrorMessage(error, "批量解析申报资料失败"));
      } finally {
        if (this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) {
          this.parsingBatch = false;
        }
      }
    },
    async compareSelectedSubmissions() {
      const selectedRows = this.selectedSubmissionRows || [];
      if (selectedRows.length !== 2) {
        return this.$message.warning("请先勾选两份材料（初始材料与补充材料）");
      }
      const initial = selectedRows[0];
      const supplement = selectedRows[1];
      this.comparingSubmissions = true;
      try {
        const res = await compareFilingSubmissions(this.projectId, {
          initial_doc_id: initial.doc_id,
          supplement_doc_id: supplement.doc_id,
        });
        this.compareResult = (res && res.data) || null;
        this.compareDialogVisible = true;
        this.$message.success("自动比对完成");
      } finally {
        this.comparingSubmissions = false;
      }
    },
    async viewParsedMarkdown(row) {
      if ((this.numericDirty || this.savingNumeric) && !await this.confirmDiscardForm()) return;
      const projectId = String(this.projectId || "").trim();
      const docId = String((row && row.doc_id) || "").trim();
      const requestToken = Number(this.dataRequestSerial || 0) + 1;
      this.dataRequestSerial = requestToken;
      this.parsedMarkdownRequestToken = requestToken;
      const isCurrentRequest = () => (
        !this.pageDisposed && !this._isDestroyed && this.parsedMarkdownRequestToken === requestToken
        && String(this.projectId || "").trim() === projectId
      );
      try {
        const res = await getFilingSubmissionParsedMarkdown(projectId, docId, { silentError: true });
        if (!isCurrentRequest()) return;
        const data = (res && res.data) || {};
        this.parsedMarkdownTitle = `解析结果 - ${row.file_name || docId}`;
        this.parsedMarkdownContent = data.markdown || "";
        this.parsedRevisionWarning = data.revision_warning || "";
        this.numericReview = data.numeric_review || {};
        const saved = Object.fromEntries((this.numericReview.items || []).map(x => [x.key, x]));
        this.numericDraft = (this.numericReview.cells || []).map(cell => ({ ...cell,
          value: saved[cell.key] ? saved[cell.key].value : cell.original_text,
          reason: saved[cell.key] ? saved[cell.key].reason : '', checked: !!saved[cell.key] }));
        this.numericSnapshot = JSON.stringify(this.numericDraft);
        this.numericDocId = docId; this.numericSourceName = row.file_name || `${docId}.bin`;
        this.downloadingNumericSource = false; this.numericPage = 1; this.numericSaveError = ''; this.savingNumeric = false;
        this.parsedDialogVisible = true;
      } catch (error) {
        if (!isCurrentRequest()) return;
        this.$message.error((error && error.userMessage) || "解析结果加载失败");
      }
    },
    async downloadNumericSource() {
      if (this.downloadingNumericSource || !this.numericDocId) return;
      const projectId = this.projectId, docId = this.numericDocId, token = this.parsedMarkdownRequestToken;
      const filename = this.numericSourceName.split(/[\\/]/).pop() || 'original.bin';
      const current = () => !this.pageDisposed && !this._isDestroyed && this.projectId === projectId
        && this.numericDocId === docId && this.parsedMarkdownRequestToken === token;
      this.downloadingNumericSource = true;
      try {
        const blob = await downloadFilingSubmissionOriginal(projectId, docId);
        if (!current()) return;
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = filename; document.body.appendChild(link);
        try { link.click(); } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
      } catch (error) {
        if (current()) this.$message.error(error.message || '原件下载失败，请重试');
      } finally { if (current()) this.downloadingNumericSource = false; }
    },
    async closeNumericPreview(done) {
      if ((this.numericDirty || this.savingNumeric) && !await this.confirmDiscardForm()) return;
      this.parsedMarkdownRequestToken += 1;
      this.numericDraft = []; this.numericSnapshot = '[]'; this.savingNumeric = false;
      done();
    },
    async saveNumericConfirmations() {
      if (this.savingNumeric) return;
      const projectId = this.projectId, docId = this.numericDocId, token = this.parsedMarkdownRequestToken;
      const current = () => !this.pageDisposed && !this._isDestroyed && this.projectId === projectId
        && this.numericDocId === docId && this.parsedMarkdownRequestToken === token;
      const snapshot = JSON.stringify(this.numericDraft);
      const items = JSON.parse(snapshot).filter(x => x.checked).map(x => ({key: x.key, original_text: x.original_text, value: x.value, reason: x.reason}));
      if (items.some(x => !x.value.trim() || !x.reason.trim())) return this.$message.warning('请填写每个已勾选单元格的确认值和核对依据');
      this.savingNumeric = true; this.numericSaveError = '';
      try {
        const res = await confirmFilingSubmissionNumbers(projectId, docId, { source_attempt: this.numericReview.source_attempt, expected_revision: this.numericReview.revision, items });
        if (!current()) return;
        this.numericReview.revision = res.data.revision;
        this.numericSnapshot = snapshot;
        this.$message.success('人工确认已保存，请明确发起新分析；历史结果不变');
        try {
          const latest = await getFilingSubmissionParsedMarkdown(projectId, docId, { silentError: true });
          if (!current()) return;
          const data = latest.data || {}, state = data.numeric_review || {};
          if (state.source_attempt !== this.numericReview.source_attempt || state.revision !== res.data.revision) {
            this.parsedRevisionWarning = '保存后资料或确认已变化，请重新读取；当前编辑输入保留。';
          } else {
            // 仅刷新已保存的正文视图，保存期间继续填写的草稿不得被重置。
            this.parsedMarkdownContent = data.markdown || '';
            this.parsedRevisionWarning = data.revision_warning || '';
          }
        } catch (_) {
          if (current()) this.parsedRevisionWarning = '人工确认已保存，但正文预览刷新失败；请重新打开解析结果。';
        }
      } catch (error) {
        if (current()) this.numericSaveError = (error && error.userMessage) || '保存失败或版本冲突，输入已保留，请重新核对';
      } finally { if (current()) this.savingNumeric = false; }
    },
    async removeSubmission(row) {
      try {
        await this.$confirm(`确认删除资料 ${row.file_name}？`, "提示", { type: "warning" });
        await deleteFilingSubmission(this.projectId, row.doc_id);
        this.$message.success("删除成功");
        await this.loadSubmissions();
      } catch (_) {}
    },
    async runReview() {
      if (this.running || this.parseReviewDirty) return;
      const projectId = this.projectId;
      const token = this.beginDataRequest('reviewStart');
      try {
        this.running = true;
        const readiness = await getFilingParseReadiness(projectId);
        if (!this.isDataRequestCurrent('reviewStart', token)) return;
        if (!readiness.data || !readiness.data.ready) {
          this.running = false;
          await this.$refs.parseReviewPanel.open();
          return;
        }
        this.activeTab = 'result';
        const res = await startFilingAIReview(projectId, { silentError: true });
        if (!this.isDataRequestCurrent('reviewStart', token)) return;
        const taskId = ((res || {}).data || {}).task_id || "";
        const runId = ((res || {}).data || {}).run_id || "";
        this.reviewTask = { task_id: taskId, run_id: runId, status: "pending", message: "任务已创建", logs: [] };
        this.startProgressPolling(taskId, runId);
      } catch (error) {
        if (!this.isDataRequestCurrent('reviewStart', token)) return;
        this.running = false;
        const detail = error && error.response && error.response.data && error.response.data.data;
        if (detail && detail.code === 'parse_review_required') {
          await this.$refs.parseReviewPanel.open();
          return;
        }
        this.$message.error((error && error.message) || "启动审评失败");
      }
    },
    startProgressPolling(taskId, runId) {
      if (this.reviewPollTimer) clearInterval(this.reviewPollTimer);
      const projectId = String(this.projectId || "").trim();
      const pollToken = this.reviewPollToken + 1;
      this.reviewPollToken = pollToken;
      const isCurrentPoll = () => (
        this.reviewPollToken === pollToken
        && String(this.projectId || "").trim() === projectId
      );
      const scheduleNext = () => {
        if (!isCurrentPoll()) return;
        this.reviewPollTimer = setTimeout(() => {
          this.reviewPollTimer = null;
          pollOnce();
        }, 30000);
      };
      const pollOnce = async () => {
        if (!isCurrentPoll()) return;
        try {
          const res = await getFilingReviewTaskProgress(taskId);
          if (!isCurrentPoll()) return;
          const data = (res && res.data) || {};
          this.reviewTask = data;
          if (["completed", "failed"].includes(data.status)) {
            if (this.reviewPollTimer) clearTimeout(this.reviewPollTimer);
            this.reviewPollTimer = null;
            this.running = false;
            await this.loadRunHistory();
            if (!isCurrentPoll()) return;
            if (runId) {
              const resultRes = await getFilingRunResult(runId);
              if (!isCurrentPoll()) return;
              this.applyRunResult((resultRes && resultRes.data) || null);
              if (data.status === "completed") await this.loadReport();
              if (!isCurrentPoll()) return;
              // 完成回调只更新结果，不强制切走用户当前核对或编辑的页面。
              if (data.status === "completed" && !this.reviewIncomplete) this.$message.success("审评完成");
              else this.$message.error(data.message || "本次审评未完成，可查看已保存的部分结果");
            } else {
              this.$message.error(data.message || "审评失败");
            }
            return;
          }
        } catch (_) {
          if (!isCurrentPoll()) return;
        }
        scheduleNext();
      };
      pollOnce();
    },
    async loadLatestResult() {
      const selectionToken = this.dataRequestTokens['selected-run'];
      return this.readCurrentData('latest-result', async isCurrent => {
      const historyRes = await listFilingReviewHistory(this.projectId, { silentError: true });
      if (!isCurrent()) return null;
      const list = (((historyRes || {}).data || {}).list) || [];
      const requested = ((this.$route || {}).query || {}).run_id || (this.reviewResult || {}).run_id;
      const latest = list.find(item => item.run_id === requested) || list[0];
      if (!latest) return null;
      const resultRes = await getFilingRunResult(latest.run_id, { silentError: true });
      return (resultRes && resultRes.data) || null;
      }, result => {
        if (this.dataRequestTokens['selected-run'] === selectionToken) this.applyRunResult(result);
      });
    },
    async loadRunHistory() {
      return this.readCurrentData('history', () => listFilingReviewHistory(this.projectId, { silentError: true }), res => {
      this.runHistory = (((res || {}).data || {}).list) || [];
      });
    },
    onReferenceFileChange(_file, fileList) {
      this.referenceFileList = fileList || [];
    },
    async uploadReferenceMaterials() {
      const files = (this.referenceFileList || []).map((item) => item.raw).filter(Boolean);
      if (!files.length) return this.$message.warning("请选择参考资料");
      this.uploadingReference = true;
      try {
        const fd = new FormData();
        files.forEach((f) => fd.append("files", f));
        fd.append("material_type", this.referenceForm.material_type);
        fd.append("task_type", "extend_validity_period");
        fd.append("drug_category", this.referenceForm.drug_category);
        fd.append("applicable_change_item", this.referenceForm.applicable_change_item);
        fd.append("applicable_registration_classification", this.referenceForm.applicable_registration_classification || "");
        fd.append("publisher", this.referenceForm.publisher || "");
        fd.append("version", this.referenceForm.version || "");
        fd.append("enabled", this.referenceForm.enabled ? "true" : "false");
        fd.append("remark", this.referenceForm.remark || "");
        await uploadFilingReferenceMaterials(fd);
        this.$message.success("参考资料上传成功");
        this.referenceFileList = [];
        await this.loadReferenceMaterials();
      } finally {
        this.uploadingReference = false;
      }
    },
    async loadReferenceMaterials() {
      const requestToken = this.beginDataRequest("reference-materials");
      const params = {
        page: 1,
        page_size: 100,
        task_type: "extend_validity_period",
        drug_category: this.referenceFilters.drug_category || "",
        material_type: this.referenceFilters.material_type || "",
        applicable_change_item: this.referenceFilters.applicable_change_item || "",
      };
      try {
        const res = await listFilingReferenceMaterials(params, { silentError: true });
        if (!this.isDataRequestCurrent("reference-materials", requestToken)) return;
        this.referenceMaterials = (((res || {}).data || {}).list) || [];
      } catch (error) {
        if (!this.isDataRequestCurrent("reference-materials", requestToken)) return;
        this.$message.error((error && error.userMessage) || "参考资料列表加载失败");
      }
    },
    async parseReference(row) {
      const docId = String((row && row.doc_id) || "").trim();
      if (!docId) return this.$message.warning("未找到需要解析的参考资料");
      const projectId = String(this.projectId || "");
      const taskKey = this.referenceParseTaskKey(row);
      const epoch = this.beginFilingParseOperation(taskKey, projectId);
      try {
        const startResponse = await startFilingReferenceMaterialParse(docId, { silentError: true });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        await this.waitForFilingParseTask(this.extractFilingParseTaskId(startResponse), {
          key: taskKey,
          projectId,
          epoch,
        });
        if (!this.isFilingParseOperationCurrent(taskKey, epoch, projectId)) return;
        this.showParseOutcome(taskKey);
        await this.loadReferenceMaterials();
      } catch (error) {
        if (this.isFilingParseCancellation(error)) return;
        this.markFilingParseOperationFailed(taskKey, epoch, projectId, error, "参考资料解析失败");
        this.$message.error(this.filingParseErrorMessage(error, "参考资料解析失败"));
      }
    },
    async removeReference(row) {
      try {
        await this.$confirm(`确认删除参考资料 ${row.title}？`, "提示", { type: "warning" });
        await deleteFilingReferenceMaterial(row.doc_id);
        this.$message.success("删除成功");
        await this.loadReferenceMaterials();
      } catch (_) {}
    },
    async toggleReferenceEnabled(row, enabled) {
      await updateFilingReferenceMaterialMetadata(row.doc_id, { enabled: !!enabled });
      row.enabled = !!enabled;
      this.$message.success("状态已更新");
    },
    async viewReferenceParsed(row) {
      const docId = String((row && row.doc_id) || "").trim();
      const requestToken = this.referenceParsedRequestToken + 1;
      this.referenceParsedRequestToken = requestToken;
      const isCurrentRequest = () => this.referenceParsedRequestToken === requestToken;
      try {
        const res = await getFilingReferenceParsedMarkdown(docId, { silentError: true });
        if (!isCurrentRequest()) return;
        const data = (res && res.data) || {};
        this.referenceParsedTitle = `参考资料解析结果 - ${row.title || docId}`;
        this.referenceParsedMarkdown = data.markdown || "";
        this.referenceParsedDialogVisible = true;
      } catch (error) {
        if (!isCurrentRequest()) return;
        this.$message.error((error && error.userMessage) || "参考资料解析结果加载失败");
      }
    },
    async saveRule() {
      if (!this.ruleForm.rule_code || !this.ruleForm.rule_name || !this.ruleForm.rule_content) return this.$message.warning("请补全规则编码、名称和内容");
      this.savingRule = true;
      try {
        const rawCondition = this.ruleConditionText.trim();
        const condition = rawCondition.startsWith('{') ? JSON.parse(rawCondition) : rawCondition;
        if (this.ruleForm.rule_id) {
          await updateFilingRule(this.ruleForm.rule_id, {
            rule_condition: condition,
            rule_name: this.ruleForm.rule_name,
            rule_type: this.ruleForm.rule_type,
            rule_content: this.ruleForm.rule_content,
            rule_category: this.ruleForm.rule_category,
            drug_category: this.ruleForm.drug_category,
            applicable_change_item: this.ruleForm.applicable_change_item,
            applicable_material_category: this.ruleForm.applicable_material_category,
            hit_result: this.ruleForm.hit_result,
            risk_level: this.ruleForm.risk_level,
            basis_source: this.ruleForm.basis_source,
            remark: this.ruleForm.remark,
          });
        } else {
          await createFilingRule({ ...this.ruleForm, rule_condition: condition, task_type: "extend_validity_period", enabled: true });
        }
        this.$message.success("规则保存成功");
        this.ruleForm = {
          rule_id: "", rule_code: "", rule_name: "", rule_type: "technical", rule_content: "",
          rule_category: "资料完整性规则", drug_category: "化学药品", applicable_change_item: "延长药品有效期",
          applicable_material_category: "", applicable_fields: "", hit_result: "", risk_level: "中", basis_source: "", remark: "",
        };
        await this.loadRules();
      } catch (error) {
        this.$message.error(error.message || "规则条件无法保存");
      } finally {
        this.savingRule = false;
      }
    },
    async onRuleImportChange(file) {
      const raw = file && file.raw;
      if (!raw) return;
      const fd = new FormData();
      fd.append("file", raw);
      await importFilingRules(fd);
      this.$message.success("规则导入完成");
      await this.loadRules();
    },
    async loadRules() {
      const requestToken = this.beginDataRequest("rules");
      const params = {
        task_type: "extend_validity_period",
        rule_category: this.ruleFilters.rule_category || "",
        drug_category: this.ruleFilters.drug_category || "",
        rule_type: this.ruleFilters.rule_type || "",
      };
      try {
        const res = await listFilingRules(params, { silentError: true });
        if (!this.isDataRequestCurrent("rules", requestToken)) return;
        this.rules = (((res || {}).data || {}).list) || [];
      } catch (error) {
        if (!this.isDataRequestCurrent("rules", requestToken)) return;
        this.$message.error((error && error.userMessage) || "审评规则列表加载失败");
      }
    },
    editRule(row) {
      const x = row.rule_json || {};
      this.ruleConditionText = typeof x.rule_condition === "object" ? JSON.stringify(x.rule_condition) : (x.rule_condition || "");
      this.ruleForm = {
        rule_id: row.rule_id,
        rule_code: row.rule_code,
        rule_name: row.rule_name,
        rule_type: row.rule_type,
        rule_content: row.rule_content,
        rule_category: x.rule_category || "",
        drug_category: x.drug_category || "",
        applicable_change_item: x.applicable_change_item || "",
        applicable_material_category: x.applicable_material_category || "",
        applicable_fields: Array.isArray(x.applicable_fields) ? x.applicable_fields.join(",") : "",
        hit_result: x.hit_result || "",
        risk_level: x.risk_level || "",
        basis_source: x.basis_source || "",
        remark: x.remark || "",
      };
    },
    async toggleRuleEnabled(row, enabled) {
      await updateFilingRule(row.rule_id, { enabled: !!enabled });
      await this.loadRules();
    },
    async removeRule(row) {
      try {
        await this.$confirm(`确认删除规则 ${row.rule_code}？`, "提示", { type: "warning" });
        await deleteFilingRule(row.rule_id);
        this.$message.success("规则已删除");
        await this.loadRules();
      } catch (_) {}
    },
    resultCount(module, key) {
      const data = (this.reviewResult || {})[module] || {};
      return Array.isArray(data[key]) ? data[key].length : '历史结果未记录';
    },
    applyRunResult(result, { discardManual = false } = {}) {
      const loaded = this.manualLoaded || {};
      const sameIdentity = loaded.projectId === this.projectId && loaded.runId === ((result || {}).run_id || '');
      const preserve = sameIdentity && (this.manualDirty || this.savingManual);
      if (!sameIdentity && !discardManual && (this.manualDirty || this.savingManual)) return false;
      if (result && result.project_id && result.project_id !== this.projectId) return false;
      if (!sameIdentity) {
        this.beginDataRequest('manual-save');
        this.savingManual = false;
      }
      this.reviewResult = result;
      if (!preserve) {
        const saved = ((result || {}).manual_confirmation || {});
        this.manualLoaded = {projectId:this.projectId, runId:(result || {}).run_id || '',
          comment:saved.comment || '', reviewer:saved.reviewer || '', revision:saved.revision || 0};
        this.manualComment = this.manualLoaded.comment;
        this.manualReviewer = this.manualLoaded.reviewer;
        this.manualSaveError = '';
      }
      if (this.projectReport && this.projectReport.run_id !== (result || {}).run_id) this.projectReport = null;
      return true;
    },
    async selectRun(runId, {discardApproved = false, syncRoute = true} = {}) {
      const changing = (this.reviewResult || {}).run_id !== runId;
      if (changing && !discardApproved && (this.manualDirty || this.savingManual) && !await this.confirmDiscardForm()) return false;
      const inputBefore = {comment:this.manualComment, reviewer:this.manualReviewer};
      const requestToken = this.beginDataRequest('selected-run');
      const res = await getFilingRunResult(runId);
      if (!this.isDataRequestCurrent('selected-run', requestToken)) return false;
      if (changing && (this.manualComment !== inputBefore.comment || this.manualReviewer !== inputBefore.reviewer)) {
        this.$message.warning('等待切换期间人工意见又有修改，已保留当前轮次，请重新选择');
        return false;
      }
      if (!this.applyRunResult(res.data, {discardManual:changing})) return false;
      if (syncRoute && this.$router && this.$route) {
        await this.$router.replace({ query: { ...this.$route.query, run_id: runId } }).catch(() => {});
      }
      this.activeTab = 'result';
      await this.loadReport();
      return true;
    },
    async saveManualOpinion() {
      const result = this.reviewResult;
      if (!result || this.savingManual || this.reviewIncomplete) return;
      const projectId = this.projectId;
      const runId = result.run_id;
      const saveToken = this.beginDataRequest('manual-save');
      const comment = this.manualComment, reviewer = this.manualReviewer;
      const revision = (this.manualLoaded || {}).runId === runId ? this.manualLoaded.revision : (result.manual_confirmation || {}).revision || 0;
      const isCurrent = () => this.projectId === projectId && (this.reviewResult || {}).run_id === runId
        && this.isDataRequestCurrent('manual-save', saveToken);
      this.savingManual = true;
      this.manualSaveError = '';
      try {
        const res = await manualConfirmFilingRun(runId, {comment, reviewer, confirmed: true, expected_revision: revision});
        if (!isCurrent()) return;
        const saved = res.data.manual_confirmation;
        this.$set(this.reviewResult, 'manual_confirmation', saved);
        this.manualLoaded = {projectId, runId, comment:saved.comment || '', reviewer:saved.reviewer || '', revision:saved.revision || 0};
        if (this.manualComment === comment && this.manualReviewer === reviewer) {
          this.manualComment = this.manualLoaded.comment;
          this.manualReviewer = this.manualLoaded.reviewer;
        }
        this.$message.success('本轮人工意见已保存');
      } catch (e) {
        if (!isCurrent()) return;
        this.manualSaveError = e.userMessage || e.message || '请刷新核对人工意见';
        this.$message.error(this.manualSaveError);
      }
      finally { if (isCurrent()) this.savingManual = false; }
    },
    async regenerateReport() {
      const context = this.reportReadContext();
      if (!context.runId || this.generatingReport || this.exportingReport || this.reviewIncomplete) return;
      this.generatingReport = true;
      try {
        await generateFilingRunReport(context.runId);
        await this.loadReport({ context });
      } catch (e) { this.$message.error(e.userMessage || e.message || '报告生成失败'); }
      finally { this.generatingReport = false; }
    },
    reportReadContext() {
      return { projectId: this.projectId, runId: (this.reviewResult || {}).run_id,
        selectionToken: this.dataRequestTokens['selected-run'] || 0 };
    },
    isReportContextCurrent(context) {
      return this.projectId === context.projectId && (this.reviewResult || {}).run_id === context.runId
        && (this.dataRequestTokens['selected-run'] || 0) === context.selectionToken && !this._isDestroyed;
    },
    async loadReport({ context = this.reportReadContext(), throwOnError = false } = {}) {
      if (this.reviewIncomplete) { this.projectReport = null; return false; }
      if (!context.runId || !this.isReportContextCurrent(context)) return false;
      const token = this.beginDataRequest('report-read');
      const isCurrent = () => this.isReportContextCurrent(context) && this.isDataRequestCurrent('report-read', token);
      try {
        const [reportRes, resultRes] = await Promise.all([
          getFilingProjectReport(context.projectId, context.runId, { silentError: true }),
          getFilingRunResult(context.runId, { silentError: true }),
        ]);
        if (!isCurrent()) return false;
        const report = reportRes && reportRes.data;
        const result = resultRes && resultRes.data;
        if (!report || !result || result.run_id !== context.runId || (context.reportId && !report.report_id)
          || (report.report_id && (report.run_id !== context.runId || (context.reportId && report.report_id !== context.reportId)))
          || (report.report_id && (context.reportId || typeof result.review_report_markdown === 'string') && report.report_content !== result.review_report_markdown)) {
          throw new Error('报告与结果版本不一致，请重新读取该轮报告');
        }
        this.projectReport = report.report_id ? report : null;
        // 仅同步报告字段，不重置未保存输入或覆盖导出期间保存的人工意见。
        ['review_report_markdown', 'report_generated_at', 'report_manual_revision'].forEach((key) => {
          if (Object.prototype.hasOwnProperty.call(result, key)) this.$set(this.reviewResult, key, result[key]);
        });
        return true;
      } catch (error) {
        if (!isCurrent()) return false;
        if (throwOnError) throw error;
        this.$message.error(`报告读取失败，原预览已保留：${error.userMessage || error.message || '请重试'}`);
        return false;
      }
    },
    async exportReportWord() {
      if (this.exportingReport || this.generatingReport || this.savingManual || this.reviewIncomplete) return;
      const context = { ...this.reportReadContext(), reportId: ((this.projectReport || {}).report_id || '').trim() };
      if (!context.runId || !context.reportId || this.projectReport.run_id !== context.runId) return this.$message.warning('暂无本轮可导出的报告');
      this.exportingReport = true;
      let downloaded = false;
      try {
        const { blob, filename } = await downloadFilingReportWord(context.reportId, context.runId);
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        try {
          link.href = url; link.download = filename; document.body.appendChild(link); link.click();
          downloaded = true;
        } finally {
          link.remove();
          // 仅延后释放下载资源，不用定时器推断服务端生成完成。
          setTimeout(() => URL.revokeObjectURL(url), 0);
        }
        await this.loadReport({ context, throwOnError: true });
      } catch (error) {
        const reason = error.userMessage || error.message || '请重试';
        if (downloaded) this.$message.warning(`Word已下载，但预览尚未更新：${reason}。请点击“重新读取报告”`);
        else this.$message.error(`Word导出失败：${reason}`);
      } finally { this.exportingReport = false; }
    },
  },
};
</script>

<style scoped>
.parse-summary { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.parse-summary .el-alert { flex: 1; min-width: 0; }
.parse-summary > .el-button { flex-shrink: 0; }
.parse-detail-body { padding: 0 24px 24px; overflow-y: auto; height: calc(100vh - 90px); box-sizing: border-box; }
.parse-detail-group { margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #e5e7eb; overflow-wrap: anywhere; }
.parse-region-list { max-height: 360px; overflow: auto; line-height: 1.8; }
.parse-region-list pre { white-space: pre-wrap; }
.upload-target { margin-bottom: 10px; color: #174b85; font-weight: 600; }
.selected-marker { margin-left: 6px; color: #1764b0; font-weight: 700; }
.submission-catalog ::v-deep .el-tree-node.is-current > .el-tree-node__content { background: #d9ecff; color: #174b85; font-weight: 700; }
.submission-catalog ::v-deep .el-tree-node__content { height: auto; min-height: 30px; white-space: normal; }
.markdown-preview {
  white-space: normal;
  word-break: break-word;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px;
}
.parsed-markdown-dialog {
  max-height: 72vh;
  overflow: auto;
  padding: 16px;
}
.markdown-preview :deep(h1), .markdown-preview :deep(h2), .markdown-preview :deep(h3) { margin: 10px 0 8px; }
.markdown-preview :deep(h4), .markdown-preview :deep(h5), .markdown-preview :deep(h6) { margin: 8px 0 6px; }
.markdown-preview :deep(p) { margin: 0 0 8px; }
.markdown-preview :deep(ul), .markdown-preview :deep(ol) { margin: 0 0 8px 20px; }
.markdown-preview :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 12px;
  background: #fff;
}
.markdown-preview :deep(th),
.markdown-preview :deep(td) {
  border: 1px solid #dbe3ee;
  padding: 8px 10px;
  vertical-align: top;
  text-align: left;
}
.markdown-preview :deep(th) {
  background: #eef4fb;
  font-weight: 600;
}
.meta-line {
  margin-top: 6px;
  font-size: 12px;
  color: #64748b;
}
.limit-detail-panel {
  margin-top: 8px;
  line-height: 1.7;
}
.limit-detail-panel summary {
  cursor: pointer;
  font-weight: 600;
}
.limit-detail-row {
  margin-top: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.72);
}
.limit-detail-row--error {
  color: #b42318;
}
.limit-detail-row--warning {
  color: #9a6700;
}
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.chart-block {
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
}
.chart-grid-item {
  margin-top: 0;
}
.chart-title {
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}
.svg-chart {
  overflow-x: auto;
}
.svg-chart :deep(svg) {
  display: block;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  height: auto;
  margin: 0 auto;
}
@media (max-width: 1200px) {
  .chart-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 768px) {
  .chart-grid {
    grid-template-columns: 1fr;
  }
}
details pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 480px;
  overflow: auto;
}
</style>
