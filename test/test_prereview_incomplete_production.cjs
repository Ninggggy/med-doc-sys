// Actual production assets, controlled HTTP responses; not a real-model acceptance test.
const fs = require('fs'), path = require('path'), http = require('http'), assert = require('assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const dist = path.resolve(process.env.AUDIT_PRODUCTION_DIST), output = process.env.AUDIT_BROWSER_OUTPUT;
fs.mkdirSync(output, { recursive: true });
const summary = { review_complete: false, execution_status: 'failed', completed_stages: ['consistency'], incomplete_stages: ['qa_run', 'complete_report'], error: { stage: 'qa_run' } };
const runs = [{ run_id: 'failed-current', summary_payload: summary }, { run_id: 'success-history', summary_payload: { review_complete: true } }];
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    let data = {};
    if (req.url.includes('/runs/history')) data = runs;
    else if (req.url.includes('/sections/overview')) data = { run_summary: req.url.includes('failed-current') ? summary : { review_complete: true }, reviewed_section_ids: [], pending_section_ids: [] };
    else if (req.url.includes('/traces') || req.url.includes('/patch')) data = [];
    else if (req.url.includes('/detail')) data = { project_name: '隔离失败轮次验收' };
    else if (req.url.includes('/latest')) data = null;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ code: 200, message: 'success', data })); return;
  }
  const file = path.resolve(dist, '.' + decodeURIComponent(req.url.split('?')[0] === '/' ? '/index.html' : req.url.split('?')[0]));
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) { res.writeHead(404);res.end();return; }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  fs.createReadStream(file).pipe(res);
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const result = { mode: 'production_build_controlled_HTTP_no_model', errors: [], checks: [] };
  page.on('pageerror', e => result.errors.push(e.message));
  try {
    const base = `http://127.0.0.1:${server.address().port}/#/pre-review/session/synthetic`;
    await page.goto(base);
    await page.getByText('本次审评未完成，仅展示已保存的部分结果，不能导出完整审评报告。', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '导出审评结论', exact: true }).isDisabled(), true);
    await page.getByText('已完成阶段：consistency', { exact: true }).waitFor();
    result.checks.push('failed stages visible; export disabled');
    await page.reload();
    await page.getByText('失败阶段：qa_run', { exact: true }).waitFor();
    result.checks.push('reload retains failed run');
    await page.goto(base + '?run_id=success-history');
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.session-page')?.__vue__?.selectedRunId === 'success-history');
    assert.equal(await page.getByRole('button', { name: '导出审评结论', exact: true }).isDisabled(), false);
    result.checks.push('explicit historical success remains accessible');
    assert.deepEqual(result.errors, []); result.passed = true;
    await page.screenshot({ path: output + '/historical-success.png' });
  } catch (error) { result.failure = error.stack; throw error; }
  finally { fs.writeFileSync(output + '/prereview-result.json', JSON.stringify(result, null, 2)); await browser.close();server.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; server.close(); });
