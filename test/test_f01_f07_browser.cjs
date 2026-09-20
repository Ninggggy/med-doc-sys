// 真实 Vue 原申请表模板/方法 + Element UI + 隔离异步服务；只替换 API 地址和无关标签页加载。
// T3_NODE_PACKAGES=/tmp/parser-defects-browser/node_modules node test/test_f01_f07_browser.cjs
// F01_BROWSER_PREPARE_ONLY=1 只生成页面并检查语法，不启动浏览器回归。
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const { createRequire } = require('module');
const runtime = createRequire(path.join(process.env.T3_NODE_PACKAGES || '/tmp/parser-defects-browser/node_modules', 'package.json'));
const output = process.env.F01_BROWSER_OUTPUT || '/tmp/f01-f07-browser';
const base = `http://127.0.0.1:${process.env.F01_BROWSER_PORT || 18767}`;
fs.mkdirSync(output, { recursive: true });
const sourcePath = path.join(__dirname, '../agent_fronted/src/views/filing-change-review/FilingChangeReviewSession.vue');
const source = fs.readFileSync(sourcePath, 'utf8');
const formTemplate = source.split('<el-tab-pane label="申请表" name="form">')[1]?.split('</el-tab-pane>')[0];
assert.ok(formTemplate, '当前源码中必须存在申请表原模板');
const template = `<div style="padding:24px">${formTemplate}</div>`;
const compiled = runtime('vue-template-compiler').compile(template);
assert.deepEqual(compiled.errors, [], '申请表原模板编译失败');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/import[\s\S]*?from\s+["'][^"']+["'];?/g, '')
  .replace('export default', 'const component =');
const prelude = `const MarkdownIt = function(){this.render=x=>x};
const api = async (url, data, multipart=false) => {
  const response = await fetch(url, data === undefined ? {} : {method:'POST',
    ...(multipart ? {} : {headers:{'Content-Type':'application/json'}}),
    body:multipart ? data : JSON.stringify(data)});
  if (!response.ok) {
    const data = await response.json();
    const error = Error(data.message || '请求失败');
    error.response = {data, status: response.status};
    throw error;
  }
  return response.json();
};
const getApplicationForm = () => api('/data');
const saveApplicationForm = (id, data) => api('/save', data);
const startApplicationFormParse = () => api('/parse', {});
const startApplicationFormImport = (id, data) => api('/upload', data, true);
const getFilingParseTaskProgress = id => api('/progress/'+id);
const getFilingProjectParseTasks = () => api('/task-list');
const getFilingChangeProjectDetail = async()=>({data:{project_name:'F01–F07 隔离回归'}});
`;
const setup = `
component.template = ${JSON.stringify(template)};
component.created = undefined; component.mounted = undefined;
for (const name of ['loadSubmissionCatalog','loadSubmissions','loadReferenceTaxonomy','loadLatestResult',
                   'loadRunHistory','loadReferenceMaterials','loadRules','loadReport']) {
  component.methods[name] = async()=>{};
}
(async()=>{
  const initial = await api('/data');
  component.computed.projectId = () => initial.data.project_id;
  window.page = new Vue(component).$mount('#app');
  window.page.formVisible = true;
  await window.page.loadBaseData();
  await window.page.$nextTick();
  window.fixtureReady = true;
})().catch(e=>{window.fixtureError=String(e); throw e});
`;
const pageScript = prelude + script + setup;
new Function(pageScript);
fs.writeFileSync(path.join(output, 'page.js'), pageScript);
if (process.env.F01_BROWSER_PREPARE_ONLY === '1') {
  console.log('申请表原模板编译、页面脚本语法检查通过；尚未运行浏览器。');
  process.exit(0);
}

const NAME = 'item_6_generic_name', ENGLISH = 'item_7_english_or_latin_name', VALIDITY = 'item_15_validity_period';
const runOutput = fs.mkdtempSync(path.join(output, 'attempt-'));
fs.copyFileSync(path.join(output, 'page.js'), path.join(runOutput, 'page.js'));
fs.copyFileSync(sourcePath, path.join(runOutput, 'FilingChangeReviewSession.vue'));
fs.copyFileSync(__filename, path.join(runOutput, 'test_f01_f07_browser.cjs'));
const report = { started_at: new Date().toISOString(), source: sourcePath,
  source_mtime: fs.statSync(sourcePath).mtime.toISOString(), output: runOutput, cases: [], page_errors: [],
  limitations: ['挂载当前 Vue 申请表原模板/原方法，未启动生产完整应用、登录及其他标签页。',
    '使用真实 Word/PDF、本机 HTTP OCR、异步控制器和隔离 SQLite；不代替 MySQL 或正式部署验收。',
    'OCR故障仅注入隔离测试进程当前项目请求：连接不可达本机端口和本机延迟HTTP端点超时。',
    'F03 进程中断恢复与 F04 CropBox/旋转/CTD/知识库矩阵由专项回归覆盖，本脚本不宣称覆盖。'] };
let browser, page, apiContext;
const write = (name, value) => fs.writeFileSync(path.join(runOutput, name), JSON.stringify(value, null, 2));
const log = line => {
  console.log(line);
  fs.appendFileSync(path.join(runOutput, 'browser.log'), `${new Date().toISOString()} ${line}\n`);
};
async function get(url) {
  const response = await apiContext.get(base + url);
  assert.ok(response.ok(), `${url}: HTTP ${response.status()}`);
  return response.json();
}
async function preservedAfterFailure(current, before) {
  const separate = form => { const { latest_task_attempts, ...business } = form; return business; };
  assert.deepEqual(separate(current.form_json), separate(before.form_json), '失败后业务值、来源、历史及其余元数据严格不变');
  const taskId = report.cases.at(-1).tasks.at(-1).task_id;
  const task = (await get('/progress/' + taskId)).data;
  const dbTask = (await get('/database-task/' + taskId)).data;
  const attempts = current.form_json.latest_task_attempts;
  const meta = attempts.application_form;
  assert.equal(meta.task_id, taskId);
  assert.equal(meta.status, 'failed');
  for (const key of Object.keys(meta).filter(key => key !== 'result')) {
    const empty = ['log_offset','heartbeat_at','created_at','updated_at','finished_at'].includes(key) ? 0 : '';
    assert.deepEqual(meta[key], dbTask[key] == null ? empty : dbTask[key], '持久任务元数据与独立数据库任务一致：' + key);
  }
  assert.equal(dbTask.status, 'failed');
  assert.deepEqual(dbTask.result, task.result);
  assert.ok(meta.finished_at && dbTask.finished_at);
  for (const key of ['ok', 'content_available', 'content_status', 'message'])
    assert.deepEqual(meta.result[key], task.result[key]);
  assert.equal(meta.result.data.code, current.latest_attempt.code);
  assert.deepEqual(meta.result.data.parse_diagnostics, task.result.data.parse_diagnostics);
  assert.deepEqual(meta.result.data.parse_diagnostics, current.latest_attempt.parse_diagnostics);
  const {application_form: ignoredCurrent, ...otherCurrent} = attempts;
  const {application_form: ignoredBefore, ...otherBefore} = before.form_json.latest_task_attempts || {};
  assert.deepEqual(otherCurrent, otherBefore, '本次失败不得改变其他任务目标的诊断');
  assert.equal(current.original_file_id, before.original_file_id);
  assert.equal(current.parse_status, before.parse_status);
  assert.equal(current.raw_text, before.raw_text);
  assert.deepEqual(current.effective_source, before.effective_source);
  await original(before.effective_source.source_file_name, current);
}

async function state(snapshot) {
  // 独立 API 客户端 → 新服务/新连接；随后再直接读 SQLite，比较实际持久化字段。
  const data = snapshot || (await get('/data')).data;
  const row = (await get('/database')).data;
  assert.equal(row.original_file_id || '', data.original_file_id || '');
  assert.equal(row.parse_status, data.parse_status);
  assert.equal(row.confidence, data.confidence);
  assert.equal(row.raw_text || '', data.raw_text || '', '保留原文与独立数据库连接一致');
  for (const [key, field] of Object.entries(data.form_json)) {
    if (field && field.field_type) assert.deepEqual(row.form_json[key], field, `数据库字段 ${key}`);
  }
  assert.deepEqual(row.form_json, data.form_json, '完整表单及解析元数据与新连接API一致');
  return data;
}
async function refresh() {
  await page.goto(base, { waitUntil: 'load' });
  await page.waitForFunction(() => window.fixtureReady === true, null, { timeout: 30000 });
}
async function fresh() {
  const response = await apiContext.post(base + '/fresh');
  assert.ok(response.ok());
  await refresh();
}
async function section(label) {
  const node = page.locator('.el-collapse-item').filter({
    has: page.locator('.el-collapse-item__header').filter({ hasText: label }),
  });
  assert.equal(await node.count(), 1, `唯一字段：${label}`);
  if (!(await node.getAttribute('class')).includes('is-active')) await node.locator('.el-collapse-item__header').click();
  // 等真实折叠动画结束再点击内部按钮，避免 Playwright 将仍在展开的
  // overflow:hidden 容器滚动到中间，导致截图把当前人工输入裁掉。
  const handle = await node.elementHandle();
  try {
    await page.waitForFunction(element => {
      const wrap = element.querySelector('.el-collapse-item__wrap');
      const content = element.querySelector('.el-collapse-item__content');
      return wrap && content && getComputedStyle(wrap).display !== 'none'
        && !wrap.classList.contains('el-collapse-transition') && wrap.clientHeight >= content.scrollHeight - 1;
    }, handle);
  } finally { await handle.dispose(); }
  return node;
}
async function fieldInput(label) { return (await section(label)).locator('input,textarea').first(); }
async function save(clickTarget) {
  const responsePromise = page.waitForResponse(r => r.url() === base + '/save' && r.request().method() === 'POST');
  await (clickTarget || page.getByRole('button', { name: '保存申请表', exact: true })).click();
  assert.equal((await responsePromise).status(), 200);
  await page.waitForFunction(() => !window.page.savingForm);
  return state();
}
async function operation(route, action, expectedStatus = 'completed') {
  const previousId = await page.evaluate(() => window.page.filingParseTaskStates['application-form']?.task_id || '');
  const responsePromise = page.waitForResponse(r => r.url() === base + route && r.request().method() === 'POST');
  await action();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  // 内嵌中文字库的真实 PDF 较大，Chrome 会淘汰该请求的调试缓存。
  // 读取原页面消费真实响应后收到的 task_id，再用独立 API 验证实际任务库。
  await page.waitForFunction(previousId => {
    const task = window.page.filingParseTaskStates['application-form'];
    return task?.task_id && task.task_id !== previousId;
  }, previousId);
  const taskId = await page.evaluate(() => window.page.filingParseTaskStates['application-form'].task_id);
  assert.ok(taskId, '必须经过真实持久化异步任务');
  await page.waitForFunction(({ taskId }) => {
    const ui = window.page;
    const task = ui.filingParseTaskStates['application-form'];
    return task?.task_id === taskId && ['completed', 'failed'].includes(task.status) && !ui.importingForm && !ui.savingForm;
  }, { taskId }, { timeout: 120000 });
  const task = (await get('/progress/' + taskId)).data;
  log(`${route} ${taskId} ${task.status}`);
  await refresh();
  const data = (await get('/data')).data;
  const databaseTask = (await get('/database-task/' + taskId)).data;
  const ui = await page.evaluate(() => ({ form_json: window.page.formSchema,
    parse_status: window.page.formParseStatus, latest_attempt: window.page.formParseAttempt,
    effective_source: window.page.formSource }));
  const evidence = { task, databaseTask, data, ui, databaseForm: (await get('/database')).data,
    verification: '原始响应已留存，随后断言页面、新服务API与独立SQLite一致' };
  write('task-' + taskId + '.json', evidence);
  report.cases.at(-1).tasks.push({ route, task_id: taskId, status: task.status,
    evidence: 'task-' + taskId + '.json' });
  await state(data);
  assert.equal(task.project_id, data.project_id, '异步任务必须属于当前项目');
  assert.equal(databaseTask.status, task.status);
  assert.deepEqual(databaseTask.result, task.result, '异步任务接口与数据库结果一致');
  assert.ok(databaseTask.finished_at, '已结束任务必须持久化结束时间');
  assert.deepEqual(ui.form_json, data.form_json, '刷新后页面完整表单与新服务读取一致');
  assert.equal(ui.parse_status, data.parse_status);
  assert.deepEqual(ui.latest_attempt, data.latest_attempt || {});
  assert.deepEqual(ui.effective_source, data.effective_source || {});
  evidence.verification = '页面状态、新服务/新连接API与直接SQLite完整表单和任务一致';
  write('task-' + taskId + '.json', evidence);
  assert.equal(task.status, expectedStatus, `${route} ${taskId}: ${task.result?.content_status}; 完整响应见任务证据`);
  return data;
}
async function upload(name, expectedStatus) {
  const response = await apiContext.get(base + '/fixture/' + name);
  assert.ok(response.ok());
  const buffer = await response.body();
  return operation('/upload', () => page.locator('input[type=file]').setInputFiles({
    name, mimeType: name.endsWith('.pdf') ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer,
  }), expectedStatus);
}
async function reparse() {
  return operation('/parse', () => page.getByRole('button', { name: '重新解析申请表', exact: true }).click());
}
async function original(name, data) {
  assert.equal(data.effective_source.source_file_name, name);
  const originalResponse = await apiContext.get(base + '/effective-original');
  assert.ok(originalResponse.ok(), '有效原件必须实际可读');
  const fixtureResponse = await apiContext.get(base + '/fixture/' + name);
  assert.deepEqual(await originalResponse.body(), await fixtureResponse.body(), '有效原件字节必须对应当前文件');
  await page.getByText(new RegExp('有效原件：' + name.replace('.', '\\.'))).waitFor();
}
async function sourceOf(field, data, name, expected, status = 'extracted') {
  assert.equal(field.value, expected);
  assert.equal(field.manual_modified, false);
  const source = field.value_sources.value;
  assert.equal(source.value, expected);
  assert.equal(source.recognition_status, status);
  assert.equal(source.source_file, name);
  assert.equal(source.source_file_id, data.original_file_id);
  assert.ok(source.source_regions.length, '已提取/明确空白均应能定位原件');
}
async function screenshot(name) {
  await page.screenshot({ path: path.join(runOutput, name + '.png'), fullPage: true, animations: 'disabled' });
}
async function attemptEvidence(data, text, role) {
  const regions = data.latest_attempt.parse_diagnostics.form_content_regions;
  const region = regions.find(region => region.text.includes(text));
  assert.ok(region, '本次失败必须保留对应的结构原文');
  assert.equal(region.role, role);
  assert.ok(region.reason, '结构原文必须有归属判断原因');
  assert.equal(region.page, 1);
  assert.equal(region.coordinate_unit, 'pdf_point');
  assert.equal(region.bbox_pdf.length, 4);
  const [x0, y0, x1, y1] = region.bbox_pdf;
  assert.ok(x0 <= 480 && x1 >= 480 && y0 <= 1070 && y1 >= 1070,
    '诊断位置必须覆盖实际950×1100 PDF底部附注');
  const details = page.locator('details.form-attempt-evidence');
  await details.locator('summary').click();
  assert.equal(await details.getAttribute('open'), '');
  const item = details.locator(':scope > div').nth(regions.indexOf(region));
  assert.equal(await item.count(), 1);
  assert.equal(await item.isVisible(), true);
  const visible = await item.innerText();
  assert.ok(visible.includes(text));
  assert.ok(visible.includes(region.reason));
  assert.ok(visible.includes('第 1 页'));
  assert.ok(visible.includes(region.bbox_pdf.join(',')), '页面必须展示诊断中的实际PDF坐标');
  assert.equal(await details.locator('summary').innerText(), '查看本次未采用内容及位置（不属于此前有效结果）');
}
async function runCase(id, body) {
  if (process.env.F01_BROWSER_CASE_FILTER && !new RegExp(process.env.F01_BROWSER_CASE_FILTER).test(id)) return;
  const entry = { id, started_at: new Date().toISOString(), tasks: [] };
  report.cases.push(entry);
  const priorErrors = report.page_errors.length;
  try {
    await fresh();
    await body();
    const tasks = (await get('/task-list')).data;
    assert.ok(tasks.every(task => ['completed', 'failed'].includes(task.status)), '场景结束没有遗留异步任务');
    assert.equal(await page.evaluate(() => Boolean(window.page.importingForm || window.page.savingForm)), false);
    assert.equal(report.page_errors.length, priorErrors, '本场景出现浏览器未处理错误');
    entry.status = 'passed';
  } catch (error) {
    entry.status = 'failed'; entry.error = error.stack;
  }
  try { write(id + '-state.json', await state()); } catch (error) { entry.evidence_error = String(error); }
  try { await screenshot(id); } catch (error) { entry.screenshot_error = String(error); }
  entry.finished_at = new Date().toISOString();
  write('results.json', report);
  log(`${id}: ${entry.status}${entry.error ? '\n' + entry.error : ''}`);
}

(async () => {
  const { chromium, request } = runtime('playwright');
  apiContext = await request.newContext();
  report.fixture = (await get('/ready')).data;
  browser = await chromium.launch({ headless: true,
    executablePath: process.env.T3_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    args: ['--no-proxy-server'] });
  report.browser = browser.version();
  page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => { report.page_errors.push(String(error)); log('PAGE ERROR: ' + error); });
  page.on('console', message => { if (['error', 'warning'].includes(message.type())) log(`console ${message.type()}: ${message.text()}`); });

  for (const footer of ['number', 'pages', 'instruction']) for (const extension of ['pdf', 'docx']) {
    await runCase(`F01-reaudit-footer-${footer}-${extension}`, async () => {
      const fixture = `reaudit-footer-${footer}.${extension}`;
      await upload('reaudit-old.docx');
      const before = await save();
      await sourceOf(before.form_json[NAME], before, 'reaudit-old.docx', '此前有效药品');
      await original('reaudit-old.docx', before);
      write(`F01-reaudit-footer-${footer}-${extension}-before.json`, before);
      const rejected = await upload(fixture, 'failed');
      assert.equal(rejected.latest_attempt.content_status, 'failed', '模板页脚不能成为本次填写内容');
      assert.notEqual(rejected.latest_attempt.attempt_id, before.latest_attempt.attempt_id);
      assert.equal(rejected.latest_attempt.source_file_name, fixture);
      const verifyPreserved = async data => {
        assert.equal(data.original_file_id, before.original_file_id);
        assert.equal(data.parse_status, before.parse_status);
        await preservedAfterFailure(data, before);
        assert.deepEqual(data.effective_source, before.effective_source);
        await original('reaudit-old.docx', data);
        assert.equal(await (await fieldInput('药品通用名称')).inputValue(), '此前有效药品');
      };
      await verifyPreserved(rejected);
      await refresh();
      const refreshed = await state();
      await verifyPreserved(refreshed);
      assert.deepEqual(refreshed.latest_attempt, rejected.latest_attempt, '刷新保留本次失败诊断与尝试标识');
      await page.getByText(/本次解析失败；此前结果如存在仍予保留/).first().waitFor();
      if (extension === 'pdf') {
        assert.equal(rejected.latest_attempt.code, 'empty_form');
        const footerText = { number: '1', pages: '第1页 共1页', instruction: '填写说明：请在相应方框内打勾。' }[footer];
        await attemptEvidence(refreshed, footerText, 'template');
      }
      await screenshot(`F01-reaudit-footer-${footer}-${extension}-rejected`);
      const retried = await reparse();
      assert.equal(retried.original_file_id, before.original_file_id);
      assert.equal(retried.form_json[NAME].value, '此前有效药品');
      assert.equal(retried.latest_attempt.mode, 'reparse');
      assert.equal(retried.latest_attempt.content_status, 'success');
      await original('reaudit-old.docx', retried);
    });
  }

  for (const mode of ['replacement', 'first-import']) {
    await runCase(`F01-reaudit-uncertain-${mode}`, async () => {
      const fixture = 'reaudit-footer-uncertain.pdf';
      let before;
      if (mode === 'replacement') {
        await upload('reaudit-old.docx');
        before = await save();
        write('F01-reaudit-uncertain-replacement-before.json', before);
      }
      let data = await upload(fixture, before ? 'failed' : 'completed');
      const attempt = data.latest_attempt;
      const verify = async current => {
        const diagnostics = current.latest_attempt.parse_diagnostics;
        assert.equal(diagnostics.replacement_eligible, false);
        const region = diagnostics.form_content_regions.find(region => region.text.includes('尚待核对的附注'));
        assert.equal(region?.role, 'uncertain');
        assert.ok(region.reason);
        if (before) {
          assert.equal(current.latest_attempt.code, 'form_content_unconfirmed');
          assert.equal(current.latest_attempt.content_status, 'failed');
          assert.ok(diagnostics.raw_text.includes('尚待核对的附注'));
          assert.equal(current.original_file_id, before.original_file_id);
          assert.equal(current.parse_status, before.parse_status);
          await preservedAfterFailure(current, before);
          assert.deepEqual(current.effective_source, before.effective_source);
          await original('reaudit-old.docx', current);
          assert.equal(await (await fieldInput('药品通用名称')).inputValue(), '此前有效药品');
        } else {
          assert.equal(current.parse_status, 'partial');
          assert.equal(current.latest_attempt.content_status, 'partial');
          assert.ok(current.raw_text.includes('尚待核对的附注'));
          assert.equal(current.form_json[NAME].value, '');
          await original(fixture, current);
        }
      };
      await verify(data);
      if (!before) await save();
      await refresh();
      data = await state();
      await verify(data);
      assert.deepEqual(data.latest_attempt, attempt);
      if (before) await attemptEvidence(data, '尚待核对的附注', 'uncertain');
      await screenshot(`F01-reaudit-uncertain-${mode}-evidence`);
      data = await reparse();
      if (before) {
        assert.equal(data.original_file_id, before.original_file_id);
        await sourceOf(data.form_json[NAME], data, 'reaudit-old.docx', '此前有效药品');
        assert.equal(data.parse_status, 'success');
        await original('reaudit-old.docx', data);
      } else await verify(data);
    });
  }

  for (const variant of ['A', 'B']) for (const side of ['left', 'right']) {
    await runCase(`F05-reaudit-layout-${variant}-${side}`, async () => {
      const fixture = `reaudit-layout-${variant}-${side}.pdf`;
      let data = await upload(fixture);
      const fileId = data.original_file_id;
      for (const phase of ['upload', 'save-refresh', 'reparse']) {
        if (phase === 'save-refresh') { await save(); await refresh(); data = await state(); }
        if (phase === 'reparse') data = await reparse();
        write(`F05-reaudit-layout-${variant}-${side}-${phase}.json`, data);
        const drug = data.form_json[NAME], spec = data.form_json.item_12_specification;
        assert.equal(drug.value.replace(/\s/g, ''), '甲乙缓释片', phase + ': 完整药名不能丢失或写入规格');
        assert.equal(spec.value.replace(/\s/g, ''), '3mg每盒10片', phase + ': 规格不能包含药名');
        assert.equal(data.original_file_id, fileId);
        assert.equal(data.parse_status, 'success');
        assert.equal(data.latest_attempt.parse_diagnostics.ocr_calls, 0, '原生几何归属不能由OCR兜底掩盖');
        for (const [field, isRight] of [[drug, side === 'right'], [spec, side === 'left']]) {
          await sourceOf(field, data, fixture, field.value);
          const offset = side === 'right' ? 460 : 0;
          const points = field === drug ? (variant === 'A' ? [[185 + offset, 105]] :
            [[118 + offset, 55], [205 + offset, 105]]) : [[530 - offset, 75], [495 - offset, 115]];
          for (const regions of [field.source_regions, field.value_sources.value.source_regions]) {
            assert.ok(regions?.length, phase + ': 字段与逐值来源均需原件位置');
            for (const region of regions) {
              assert.equal(region.page, 1);
              assert.equal(region.coordinate_unit, 'pdf_point');
              assert.equal(region.bbox_pdf?.length, 4);
              assert.ok(isRight ? region.bbox_pdf[0] >= 489 : region.bbox_pdf[2] < 490,
                `${phase}: 来源不得跨入相邻列 ${JSON.stringify(region)}`);
            }
            for (const [x, y] of points) assert.ok(regions.some(region => {
              const [x0, y0, x1, y1] = region.bbox_pdf;
              return x0 <= x && x <= x1 && y0 <= y && y <= y1;
            }), `${phase}: 来源必须覆盖实际填写行中的位置 (${x},${y})`);
          }
          const expectedSource = field === drug ? '甲乙缓释片' : '3mg每盒10片';
          assert.ok(field.value_sources.value.source_text.replace(/\s/g, '').includes(expectedSource),
            phase + ': 逐值来源原文必须包含完整填写内容');
        }
        assert.ok(!/3mg|每盒10片/.test(drug.source_text), '药名来源不得借用规格');
        assert.ok(!/甲乙|缓释片/.test(spec.source_text), '规格来源不得借用药名');
        assert.equal((await (await fieldInput('药品通用名称')).inputValue()).replace(/\s/g, ''), '甲乙缓释片');
        assert.equal((await (await fieldInput('12. 规格')).inputValue()).replace(/\s/g, ''), '3mg每盒10片');
        await original(fixture, data);
        await screenshot(`F05-reaudit-layout-${variant}-${side}-${phase}`);
      }
      for (const label of ['药品通用名称', '12. 规格']) {
        const field = await section(label);
        await field.getByText('查看申请表原文来源', { exact: true }).click();
      }
    });
  }

  for (const extension of ['docx', 'pdf']) await runCase('F01-unchecked-' + extension, async () => {
    await upload('old.docx');
    const before = await save();
    write('F01-unchecked-' + extension + '-before.json', before);
    const data = await upload('unchecked.' + extension, 'failed');
    assert.equal(data.latest_attempt.content_status, 'failed');
    assert.equal(data.original_file_id, before.original_file_id);
    assert.equal(data.parse_status, before.parse_status);
    await preservedAfterFailure(data, before);
    await original('old.docx', data);
    assert.equal(await (await fieldInput('药品通用名称')).inputValue(), '原Word药品甲');
    await page.getByText(/本次解析失败；此前结果如存在仍予保留/).first().waitFor();
    await screenshot('F01-unchecked-' + extension + '-failure-preserved');
    const after = await reparse();
    assert.equal(after.original_file_id, before.original_file_id);
    assert.equal(after.form_json[NAME].value, '原Word药品甲');
    await original('old.docx', after);
  });

  for (const mode of ['connection', 'timeout']) await runCase('F02-real-ocr-' + mode, async () => {
    const setMode = async mode => {
      const response = await apiContext.post(base + '/test-ocr-mode', { data: { mode } });
      assert.ok(response.ok());
    };
    await setMode('real');
    try {
      const before = await upload('real-ocr.pdf');
      write('F02-real-ocr-' + mode + '-before.json', before);
      assert.equal(before.parse_status, 'success');
      await sourceOf(before.form_json[ENGLISH], before, 'real-ocr.pdf', 'EXAMPLE TABLETS');
      await original('real-ocr.pdf', before);
      const previous = before.form_json[ENGLISH].value_sources.value;
      await setMode(mode);
      const failed = await reparse();
      write('F02-real-ocr-' + mode + '-failure.json', failed);
      assert.equal(failed.parse_status, 'partial');
      assert.equal(failed.latest_attempt.content_status, 'partial');
      assert.equal(failed.original_file_id, before.original_file_id);
      const field = failed.form_json[ENGLISH], source = field.value_sources.value;
      assert.equal(field.value, 'EXAMPLE TABLETS', '同件OCR失败不能无历史清除此前有效值');
      assert.equal(field.manual_modified, false);
      assert.equal(field.recognition_status, 'previous_result');
      assert.equal(source.recognition_status, 'previous_result');
      assert.equal(source.historical, true);
      assert.deepEqual(source.previous_source, previous, '保留旧值完整来源证据');
      assert.equal(source.source_file_id, before.original_file_id);
      assert.ok(field.value_history.some(h => h.value === previous.value && h.source_file_id === before.original_file_id
        && JSON.stringify(h.source_regions) === JSON.stringify(previous.source_regions)), '来源历史含旧值及原文位置');
      const diagnostics = source.reparse_diagnostics;
      assert.ok(diagnostics, '当前值附本次相交失败范围');
      const errors = Array.isArray(diagnostics) ? diagnostics : diagnostics.errors;
      assert.ok(errors?.some(e => e.page === 2 && e.bbox_pdf?.length === 4
        && e.code === (mode === 'timeout' ? 'ocr_timeout' : 'ocr_unreachable')), '必须记录第2页真实故障类型与区域');
      assert.deepEqual(errors.find(e => e.page === 2).bbox_pdf, [200, 30, 500, 90]);
      assert.equal(failed.form_json[NAME].value, '明确药品');
      assert.equal(failed.form_json[NAME].recognition_status, 'extracted', '正常第一页不得误标为历史');
      assert.deepEqual(await page.evaluate(key => window.page.formSchema[key], ENGLISH), field, '页面与新连接持久化结果一致');
      const english = await section('英文名称');
      assert.equal(await (await fieldInput('英文名称')).inputValue(), 'EXAMPLE TABLETS');
      await english.getByText('此前识别结果（本次未取得）', { exact: true }).first().waitFor();
      for (const detail of await english.locator('details').all()) {
        if (await detail.getAttribute('open') === null) await detail.locator(':scope > summary').click();
      }
      assert.match(await english.innerText(), /本次失败范围/);
      assert.match(await english.innerText(), /第\s*2\s*页/);
      assert.match(await english.innerText(), mode === 'timeout' ? /超时/ : /无法连接/);
      const scopeText = await english.getByText(/^本次失败范围：/).first().innerText();
      assert.match(scopeText.replace(/\s/g, ''), /200,30,500,90/);
      assert.match(scopeText, /pdf_point/);
      await english.getByText('此前识别的原文依据', { exact: true }).waitFor();
      await english.screenshot({ path: path.join(runOutput, 'F02-real-ocr-' + mode + '-previous-result.png'), animations: 'disabled' });
      await original('real-ocr.pdf', failed);
      await save(); await refresh();
      const saved = await state();
      assert.deepEqual(saved.form_json[ENGLISH], field, '保存刷新不能伪造为本次提取或丢历史');
      await setMode('real');
      const recovered = await reparse();
      assert.equal(recovered.parse_status, 'success');
      assert.equal(recovered.original_file_id, before.original_file_id);
      await sourceOf(recovered.form_json[ENGLISH], recovered, 'real-ocr.pdf', 'EXAMPLE TABLETS');
      assert.ok(recovered.form_json[ENGLISH].value_history.length >= field.value_history.length, '恢复后保留历史');
      assert.equal(recovered.form_json[ENGLISH].value_sources.value.historical, undefined);
      assert.equal(await (await fieldInput('英文名称')).inputValue(), 'EXAMPLE TABLETS');
      await original('real-ocr.pdf', recovered);
    } finally { await setMode('real'); }
  });

  await runCase('F02-word-pdf-word', async () => {
    let data = await upload('old.docx');
    await sourceOf(data.form_json[NAME], data, 'old.docx', '原Word药品甲');
    const firstId = data.original_file_id;
    data = await upload('new.pdf');
    assert.notEqual(data.original_file_id, firstId);
    await sourceOf(data.form_json[NAME], data, 'new.pdf', '新PDF药品乙');
    await sourceOf(data.form_json[ENGLISH], data, 'new.pdf', '', 'explicit_blank');
    assert.equal(await (await fieldInput('药品通用名称')).inputValue(), '新PDF药品乙');
    await (await section('英文名称')).getByText('原件明确空白', { exact: true }).waitFor();
    await original('new.pdf', data);
    await screenshot('F02-pdf-explicit-blank');
    data = await reparse();
    assert.equal(data.form_json[NAME].value, '新PDF药品乙');
    const secondId = data.original_file_id;
    data = await upload('new.docx');
    assert.notEqual(data.original_file_id, secondId);
    await sourceOf(data.form_json[NAME], data, 'new.docx', '新Word药品丙');
    await sourceOf(data.form_json[ENGLISH], data, 'new.docx', 'New Word Name');
    await original('new.docx', data);
    data = await save(); await refresh(); data = await reparse();
    assert.equal(data.form_json[NAME].value, '新Word药品丙');
    assert.equal(data.form_json[ENGLISH].value, 'New Word Name');
  });

  for (const extension of ['docx', 'pdf']) await runCase('F01-blank-' + extension, async () => {
    await upload('old.docx');
    // 上传方法会先保存，提前保存一次以隔离保存元数据和失败替换的比较。
    const before = await save();
    const data = await upload('blank.' + extension, 'failed');
    assert.equal(data.latest_attempt.content_status, 'failed');
    assert.equal(data.original_file_id, before.original_file_id);
    assert.equal(data.parse_status, before.parse_status);
    await preservedAfterFailure(data, before);
    await original('old.docx', data);
    await page.getByText(/本次解析失败；此前结果如存在仍予保留/).first().waitFor();
    await screenshot('F01-blank-' + extension + '-failed-attempt');
    const after = await reparse();
    assert.equal(after.original_file_id, before.original_file_id);
    assert.equal(after.form_json[NAME].value, '原Word药品甲');
    await original('old.docx', after);
  });

  for (const manual of ['人工药名', '']) await runCase('F02-manual-' + (manual ? 'edit' : 'clear'), async () => {
    await upload('old.docx');
    await (await fieldInput('药品通用名称')).fill(manual);
    await (await fieldInput('英文名称')).fill('人工英文');
    let data = await save(); await refresh();
    assert.equal(data.form_json[NAME].value, manual);
    assert.equal(data.form_json[NAME].manual_modified, true);
    assert.equal(data.form_json[NAME].value_sources.value.recognition_status, 'manual');
    data = await reparse();
    assert.equal(data.form_json[NAME].value, manual);
    data = await upload('new.pdf');
    for (const [key, value] of [[NAME, manual], [ENGLISH, '人工英文']]) {
      const field = data.form_json[key];
      assert.equal(field.value, value);
      assert.equal(field.manual_modified, true);
      assert.equal(field.value_sources.value.recognition_status, 'manual');
      assert.equal(field.value_sources.value.source_file_id, '');
      assert.equal(field.value_sources.value.edited_from.source_file, 'old.docx');
      assert.equal(field.candidates.value.source_file_id, data.original_file_id);
      assert.equal(field.candidates.value.source_file, 'new.pdf');
      assert.ok(field.candidates.value.source_regions.length);
    }
    assert.equal(data.form_json[NAME].candidates.value.value, '新PDF药品乙');
    assert.equal(data.form_json[ENGLISH].candidates.value.value, '');
    assert.equal(data.form_json[ENGLISH].candidates.value.recognition_status, 'explicit_blank');
    assert.equal(await (await fieldInput('药品通用名称')).inputValue(), manual);
    let english = await section('英文名称');
    const blankCandidate = english.locator('pre').filter({ hasText: /新候选：.*原件明确空白/ });
    await blankCandidate.waitFor();
    assert.ok(!(await blankCandidate.innerText()).includes('{}'), '明确空候选不能显示为对象 {}');
    await english.locator('summary').filter({ hasText: '查看候选原文来源' }).click();
    await english.getByText('new.pdf', { exact: true }).waitFor();
    await english.screenshot({ path: path.join(runOutput, 'F02-manual-' + (manual ? 'edit' : 'clear') + '-empty-candidate.png'), animations: 'disabled' });
    const manualName = await section('药品通用名称');
    await manualName.locator('summary').filter({ hasText: '查看申请表原文来源' }).click();
    await manualName.getByText('人工修订前的依据', { exact: true }).waitFor();
    await screenshot('F02-manual-' + (manual ? 'edit' : 'clear') + '-candidates');
    await save(english.getByRole('button', { name: '保留人工值', exact: true }));
    await refresh(); data = await state();
    assert.equal(data.form_json[ENGLISH].value, '人工英文');
    assert.equal(data.form_json[ENGLISH].manual_modified, true);
    assert.deepEqual(data.form_json[ENGLISH].candidates, {});
    assert.equal(data.form_json[ENGLISH].resolution_history.at(-1).action, 'keep');
    data = await reparse();
    assert.equal(data.form_json[ENGLISH].candidates.value.recognition_status, 'explicit_blank');
    english = await section('英文名称');
    await save(english.getByRole('button', { name: '采用候选值', exact: true }));
    await refresh(); data = await state();
    await sourceOf(data.form_json[ENGLISH], data, 'new.pdf', '', 'explicit_blank');
    assert.equal(data.form_json[ENGLISH].resolution_history.at(-1).action, 'adopt');
    const nameSection = await section('药品通用名称');
    await save(nameSection.getByRole('button', { name: '保留人工值', exact: true }));
    await refresh(); data = await reparse();
    assert.equal(data.form_json[NAME].value, manual);
    assert.equal(data.form_json[ENGLISH].value, '');
    await save((await section('药品通用名称')).getByRole('button', { name: '采用候选值', exact: true }));
    await refresh(); data = await reparse();
    await sourceOf(data.form_json[NAME], data, 'new.pdf', '新PDF药品乙');
    assert.equal(data.form_json[ENGLISH].value, '');
  });

  await runCase('F02-unrecognized', async () => {
    await upload('old.docx');
    const data = await upload('missing.docx');
    assert.equal(data.form_json[ENGLISH].value, '');
    assert.equal(data.form_json[ENGLISH].recognition_status, 'unrecognized');
    assert.equal(data.form_json[ENGLISH].value_sources.value.source_file_id, data.original_file_id);
    const english = await section('英文名称');
    await english.getByText('未识别', { exact: true }).waitFor();
    assert.equal(await (await fieldInput('英文名称')).inputValue(), '');
  });

  for (const extension of ['docx', 'pdf']) await runCase('F02-conflicting-validity-' + extension, async () => {
    await upload('old.docx');
    let data = await upload('conflict.' + extension);
    for (const phase of ['upload', 'save', 'reparse']) {
      if (phase === 'save') { await save(); await refresh(); data = await state(); }
      if (phase === 'reparse') data = await reparse();
      const field = data.form_json[VALIDITY];
      assert.ok(field.semantic_issues.length);
      for (const key of ['original_validity_period', 'proposed_validity_period']) {
        assert.equal(field.sub_fields[key], '', phase + ': 不得用旧自动期限填回冲突空值');
        assert.equal(field.value_sources['sub_fields.' + key].recognition_status, 'conflict');
        assert.equal(field.value_sources['sub_fields.' + key].source_file_id, data.original_file_id);
      }
      const node = await section('药品有效期');
      await node.getByText('待核对', { exact: true }).waitFor();
      assert.equal(await node.locator('.el-form-item').filter({ hasText: '原有效期' }).locator('input').inputValue(), '');
      assert.equal(await node.locator('.el-form-item').filter({ hasText: '拟延长后有效期' }).locator('input').inputValue(), '');
    }
    const node = await section('药品有效期');
    await node.locator('summary').filter({ hasText: '冲突' }).first().click();
    await node.getByText(/药品有效期由18个月延长至24个月/).first().waitFor();
  });

  for (const manual of ['20个月', '']) await runCase('F02-manual-validity-' + (manual ? 'edit' : 'clear'), async () => {
    await upload('old.docx');
    const node = await section('药品有效期');
    await node.locator('.el-form-item').filter({ hasText: '原有效期' }).locator('input').fill(manual);
    await save(); await refresh();
    let data = await upload('conflict.docx');
    for (const again of [false, true]) {
      if (again) data = await reparse();
      const field = data.form_json[VALIDITY];
      assert.equal(field.sub_fields.original_validity_period, manual);
      assert.equal(field.sub_fields.proposed_validity_period, '');
      assert.equal(field.value_sources['sub_fields.original_validity_period'].recognition_status, 'manual');
      assert.equal(field.value_sources['sub_fields.proposed_validity_period'].recognition_status, 'conflict');
      assert.ok(field.semantic_issues.length);
      assert.equal(field.manual_modified, true);
      const current = await section('药品有效期');
      assert.equal(await current.locator('.el-form-item').filter({ hasText: '原有效期' }).locator('input').inputValue(), manual);
      if (manual) {
        assert.equal(field.candidates['sub_fields.original_validity_period'].value, '');
        assert.equal(field.candidates['sub_fields.original_validity_period'].recognition_status, 'conflict');
        await current.getByRole('button', { name: '采用候选值', exact: true }).waitFor();
      }
    }
  });

  await runCase('F05-parallel-pdf', async () => {
    let data = await upload('parallel.pdf');
    for (const reparseNow of [false, true]) {
      if (reparseNow) data = await reparse();
      assert.equal(data.form_json[NAME].value, '盐酸测试缓释片');
      assert.equal(data.form_json.item_12_specification.value, '1%');
      assert.equal(await (await fieldInput('药品通用名称')).inputValue(), '盐酸测试缓释片');
      assert.equal(await (await fieldInput('12. 规格')).inputValue(), '1%');
    }
  });

  for (const extension of ['docx', 'pdf']) await runCase('F06-F07-semantics-' + extension, async () => {
    await upload('semantics.' + extension);
    await save(); await refresh();
    const data = await reparse();
    assert.deepEqual(data.form_json.item_11_dosage_form.selected_values, ['片剂']);
    const matter = data.form_json.item_5_application_matter_category;
    assert.deepEqual(matter.selected_values, ['1.7']);
    assert.equal(matter.original_matter.find(x => x.code === '6.8').selected, true);
    assert.equal(matter.original_matter.find(x => x.code === '6.9').selected, false);
    assert.equal(data.form_json.item_24_patent_info.value, '本申请声明：构成他人专利侵权。');
    const first = data.form_json.item_28_change_related_items;
    assert.equal(first.value, '非首次申请');
    assert.ok(first.source_text.includes('非首次申请'), '非首次申请原文必须可追溯');
    await (await section('剂型')).getByText('片剂', { exact: true }).first().waitFor();
    const matterNode = await section('申请事项分类');
    assert.equal(await matterNode.locator('.el-checkbox.is-checked').count(), 1);
    await matterNode.locator('.el-checkbox.is-checked').filter({ hasText: '变更有效期和贮藏条件' }).waitFor();
    assert.equal(await (await fieldInput('专利情况')).inputValue(), '本申请声明：构成他人专利侵权。');
  });
  report.finished_at = new Date().toISOString();
  report.passed = report.cases.filter(x => x.status === 'passed').length;
  report.failed = report.cases.filter(x => x.status === 'failed').length;
  report.scenario_count = report.cases.length;
  report.task_count = report.cases.reduce((count, entry) => count + entry.tasks.length, 0);
  report.task_status_counts = report.cases.flatMap(entry => entry.tasks).reduce((counts, task) => {
    counts[task.status] = (counts[task.status] || 0) + 1; return counts;
  }, {});
  report.sources_changed_during_run = Object.entries(report.fixture.source_mtimes || {}).filter(([file, mtime]) =>
    Math.abs(fs.statSync(file).mtimeMs - Date.parse(mtime)) > 2).map(([file]) => file);
  if (fs.statSync(sourcePath).mtime.toISOString() !== report.source_mtime) report.sources_changed_during_run.push(sourcePath);
  write('results.json', report);
  log(`完成：${report.passed} 通过，${report.failed} 失败；证据 ${runOutput}`);
  process.exitCode = report.failed ? 1 : 0;
})().catch(error => {
  report.fatal_error = error.stack;
  write('results.json', report);
  log(error.stack);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (apiContext) await apiContext.dispose();
});
