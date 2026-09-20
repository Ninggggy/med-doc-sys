// 独立浏览器交互回归：只挂载真实组件，不调用业务 API / OCR。
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../agent_fronted');
const source = fs.readFileSync(path.join(root, 'src/views/filing-change-review/FilingChangeReviewSession.vue'), 'utf8');
const template = source.match(/<template>([\s\S]*?)<\/template>\s*<script>/)[1];
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '')
  .replace('export default', 'window.component =');
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.setContent('<div id="app"></div>');
    await page.addScriptTag({ path: path.join(root, 'node_modules/vue/dist/vue.js') });
    await page.addScriptTag({ path: path.join(root, 'node_modules/element-ui/lib/index.js') });
    await page.addStyleTag({ path: path.join(root, 'node_modules/element-ui/lib/theme-chalk/index.css') });
    await page.addStyleTag({ content: source.match(/<style[^>]*>([\s\S]*?)<\/style>/)[1].replace(/::v-deep/g, '') });
    const detailsSource = fs.readFileSync(path.join(root, 'src/views/filing-change-review/StabilityLimitDetails.vue'), 'utf8');
    await page.addScriptTag({ content: detailsSource.match(/<script>([\s\S]*?)<\/script>/)[1].replace('export default', 'window.StabilityLimitDetails =') });
    await page.evaluate(template => { window.StabilityLimitDetails.template = template; }, detailsSource.match(/<template>([\s\S]*?)<\/template>\s*<script>/)[1]);
    await page.addScriptTag({ content: 'window.MarkdownIt = function(){this.render=x=>x};\n' + script });
    await page.evaluate(template => {
      const options = window.component;
      delete options.created; delete options.mounted; delete options.watch;
      options.template = template;
      options.computed.projectId = () => 'test';
      window.catalog = [{ code: 'quality', label: '质量标准', required_level: 'required' }];
      window.getFilingSubmissionCatalog = async () => ({ data: { catalog: window.catalog } });
      window.app = new Vue(options).$mount('#app');
      app.activeTab = 'submission';
      app.submissionCatalog = window.catalog;
      app.filingParseTaskStates = { test: { status: 'completed', result: { content_status: 'partial', data: { parse_diagnostics: {
        failed_pages: Array.from({ length: 100 }, (_, i) => i + 1),
        errors: Array.from({ length: 300 }, (_, i) => ({ page: i % 100 + 1, stage: 'ocr_quality', code: 'ocr_response', bbox_pdf: [i, 0, i + 1, 1] }))
      } } } } };
    }, template);
    await page.locator('.submission-catalog .el-tree-node__content').click();
    await page.getByPlaceholder('当前目录不适用原因（可选）').click();
    assert.equal(await page.locator('.submission-catalog .is-current').count(), 1);
    assert.match(await page.locator('.upload-target').innerText(), /质量标准/);
    await page.evaluate(() => app.loadSubmissionCatalog());
    assert.equal(await page.locator('.submission-catalog .is-current').count(), 1);
    await page.evaluate(() => { app.activeTab = 'form'; });
    await page.evaluate(() => { app.activeTab = 'submission'; });
    assert.equal(await page.locator('.submission-catalog .is-current').count(), 1);
    const summary = page.locator('.parse-summary').first();
    assert.ok((await summary.boundingBox()).height < 100);
    await summary.getByRole('button', { name: '查看详情', exact: true }).click();
    await page.locator('.parse-detail-group summary').click();
    assert.equal(await page.locator('.parse-region-list > div').count(), 300);
    assert.doesNotMatch(await page.locator('.parse-detail-body').innerText(), /OCR 返回异常/);
    await page.evaluate(() => app.openParseDetails({ status: 'completed', result: { data: { success: Array.from({ length: 45 }, (_, i) => ({ doc_id: String(i), content_status: 'success' })) } } }, '分页'));
    assert.equal(await page.locator('.parse-detail-group').count(), 20);
    await page.locator('.el-pagination .number').filter({ hasText: /^3$/ }).click();
    assert.equal(await page.locator('.parse-detail-group').count(), 5);
    await page.evaluate(() => { window.catalog = []; return app.loadSubmissionCatalog(); });
    assert.equal(await page.evaluate(() => app.selectedSubmissionCategory), '');
    assert.match(await page.locator('.upload-target').innerText(), /自动分类/);
    assert.deepEqual(errors, []);
    console.log('PASS: 实际 Vue/Element UI 浏览器交互，目录保持/失效清除，300条诊断与20项分页');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
