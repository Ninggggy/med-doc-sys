const assert = require('assert'), fs = require('fs'), vm = require('vm'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../agent_fronted/src/views/filing-change-review/StabilityLimitDetails.vue'), 'utf8');
const context = { module: { exports: {} } };
vm.runInNewContext(source.match(/<script>([\s\S]*?)<\/script>/)[1].replace('export default', 'module.exports ='), context);
const options = context.module.exports;
function fixture(count, saved = count) {
  const p = {...options.data(), check: {out_of_spec_count: count, undecidable_count: count,
    out_of_spec_details: Array.from({length: saved}, (_, i) => ({indicator: i % 2 ? 'B' : 'A', result_text: String(i)})),
    undecidable_details: Array.from({length: saved}, (_, i) => ({indicator: 'C', result_text: String(i)}))}};
  for (const [key, fn] of Object.entries(options.methods)) p[key] = fn.bind(p);
  for (const [key, fn] of Object.entries(options.computed)) Object.defineProperty(p, key, {get: fn.bind(p)});
  return p;
}
for (const count of [25, 100, 1000]) {
  const p = fixture(count);
  for (const group of p.groups) {
    const visited = [];
    for (let page = 1; page <= Math.ceil(count / 20); page++) {
      p.pages[group.key] = page;
      const rows = p.pageRows(group); assert(rows.length <= 20);
      visited.push(...rows.map(row => row.result_text));
    }
    assert.equal(visited.length, count); assert.equal(new Set(visited).size, count);
    assert.equal(group.incomplete, false);
  }
  p.indicator = 'A'; options.watch.indicator.call(p);
  assert.equal(p.pages.out_of_spec, 1); assert.equal(p.groups[0].filtered.length, Math.ceil(count / 2));
  p.pages.out_of_spec = 2; options.watch.check.call(p);
  assert.equal(p.pages.out_of_spec, 1); assert.equal(p.indicator, '');
}
const historical = fixture(25, 20);
assert(historical.groups.every(group => group.incomplete && group.all.length === 20));
assert.equal(historical.componentStatus({within_standard:false}), '已超限');
assert.equal(historical.componentStatus({within_standard:null}), '待确认');
console.log('PASS: 25/100/1000 complete pagination, filtering/reset, historical truncation, component status');
