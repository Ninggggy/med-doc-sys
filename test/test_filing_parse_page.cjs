// 直接执行会话页的方法，核对刷新恢复和用户实际看到的状态文案。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../agent_fronted/src/views/filing-change-review/FilingChangeReviewSession.vue'), 'utf8');
if (process.env.T1_VUE_COMPILER) {
  const compiler = require(process.env.T1_VUE_COMPILER);
  const compiled = compiler.compile(compiler.parseComponent(source).template.content);
  assert.equal(compiled.errors.length, 0, compiled.errors.join('\n'));
  console.log('Vue 会话页模板编译通过');
}
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '')
  .replace('export default', 'module.exports =');
let records = [];
const context = {
  module: { exports: {} },
  StabilityLimitDetails: {}, // 此夹具只测父页面方法；明细组件有独立回归。
  ParseReviewPanel: {}, // 已新增的子组件由独立方法与生产浏览器验证。
  MarkdownIt: function () { this.render = value => value; },
  getFilingProjectParseTasks: async () => ({ data: records }),
  setTimeout, clearTimeout,
};
vm.runInNewContext(script, context);
const component = context.module.exports;
function page() {
  const instance = { ...component.data(), projectId: 'p', $set: (obj, key, value) => { obj[key] = value; }, $delete: (obj, key) => { delete obj[key]; } };
  for (const [key, method] of Object.entries(component.methods)) instance[key] = method.bind(instance);
  return instance;
}
(async () => {
  const current = page();
  records = [{ task_id: 'failed-new', project_id: 'p', source_doc_id: 'a', task_type: 'parse_submission', status: 'failed', error_message: 'OCR 服务无法连接', result: { content_status: 'failed' } },
    { task_id: 'success-old', project_id: 'p', source_doc_id: 'a', task_type: 'parse_submission', status: 'completed', result: { content_status: 'success' } }];
  await current.restoreParseTasks();
  assert.equal(current.filingParseTaskStates['submission:a'].task_id, 'failed-new');
  assert.match(current.parseAttemptLabel(current.filingParseTaskStates['submission:a']), /本次解析失败.*此前结果.*查看详情/);
  current.openParseDetails(current.filingParseTaskStates['submission:a'], 'a');
  assert.match(current.parseDetailGroups[0].message, /OCR/);
  records = [{ ...records[0], task_id: 'retry-success', status: 'completed', error_message: '', result: { content_status: 'partial', data: { parse_diagnostics: { failed_pages: [2, 4] } } } }];
  await current.restoreParseTasks();
  assert.match(current.parseAttemptLabel(current.filingParseTaskStates['submission:a']), /部分成功.*2页需核对/);
  records[0].result = { content_status: 'success' };
  await current.restoreParseTasks();
  assert.doesNotMatch(current.parseAttemptLabel(current.filingParseTaskStates['submission:a']), /失败|未完整解析页/);
  records = [{ ...records[0], task_id: 'running-restored', status: 'running' }];
  let resumed;
  current.waitForFilingParseTask = (id, options) => { resumed = { id, options }; return new Promise(() => {}); };
  await current.restoreParseTasks();
  assert.equal(resumed.id, 'running-restored');
  assert.equal(resumed.options.projectId, 'p');
  assert.equal(current.parseContentLabel('pending'), '尚未解析');
  assert.match(source, /不能视为本次成功/);
  const errors = Array.from({ length: 300 }, (_, i) => ({ page: i % 100 + 1, stage: i % 2 ? 'ocr_quality' : 'ocr_coverage', code: 'ocr_response', message: 'OCR 返回异常', bbox_pdf: [i, 1, i + 1, 2] }));
  const longState = { status: 'completed', result: { content_status: 'partial', data: { parse_diagnostics: { errors, failed_pages: Array.from({ length: 100 }, (_, i) => i + 1), available_pages: [1, 2] } } } };
  assert.ok(current.parseAttemptLabel(longState).length < 100);
  assert.equal(current.parseAttemptType(longState), 'warning');
  current.openParseDetails(longState, '100页.pdf');
  assert.equal(current.parseDetailGroups.length, 2);
  assert.equal(current.parseDetailGroups.reduce((n, group) => n + group.entries.length, 0), 300);
  assert.ok(current.parseDetailGroups.every(group => !group.message.includes('返回异常')));
  assert.equal(current.compactPages([5, 1, 2, 2, 3, 8]), '1–3、5、8');
  const batch = { status: 'completed', result: { content_status: 'partial', data: { success: [{content_status: 'success'}, {content_status: 'partial'}], failed: [{content_status: 'failed'}] } } };
  assert.match(current.parseAttemptLabel(batch), /成功1份、部分成功1份、失败1份/);
  current.openParseDetails({status:'completed', result:{data:{success:Array.from({length:45}, (_,i)=>({doc_id:String(i),content_status:'success'}))}}}, '批量');
  assert.equal(component.computed.pagedParseDetails.call(current).length, 20);
  current.parseDetailsPage = 3;
  assert.equal(component.computed.pagedParseDetails.call(current).length, 5);
  assert.equal(current.formFieldStatus({ recognition_status: 'explicit_blank' }), '原件明确空白');
  assert.equal(current.recognitionLabel('conflict'), '识别冲突，待核对');
  assert.equal(current.recognitionLabel('manual'), '人工填写');
  assert.equal(current.recognitionLabel('previous_result'), '此前识别结果（本次未取得）');
  assert.equal(current.formFieldStatus({ value: 'EXAMPLE TABLETS', recognition_status: 'previous_result' }), '此前识别结果（本次未取得）');
  assert.equal(current.formFieldStatus({ recognition_status: 'mixed_sources', value_sources: { 'sub_fields.a': { recognition_status: 'previous_result' } } }), '此前识别结果（本次未取得）');
  assert.equal(current.formFieldStatus({ manual_modified: true, value: '', recognition_status: 'manual' }), '人工已修改');
  assert.equal(current.pretty(''), '（空白）');
  assert.equal(current.pretty(0), '0');
  console.log('通过：最新失败恢复、旧结果区分、缺页提示、重试成功更新、运行任务续查、尚未解析文案。');
})().catch(error => { console.error(error); process.exitCode = 1; });
