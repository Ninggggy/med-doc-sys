// 真实组件浏览器验证；不是生产构建与后端联动的替代。
const fs = require('fs'), path = require('path'), assert = require('assert');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.join(__dirname, '../agent_fronted');
const source = fs.readFileSync(path.join(root, 'src/views/filing-change-review/StabilityLimitDetails.vue'), 'utf8');
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
  try {
    const page = await browser.newPage(); const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<div id="app"></div>');
    await page.addScriptTag({path:path.join(root, 'node_modules/vue/dist/vue.js')});
    await page.addScriptTag({path:path.join(root, 'node_modules/element-ui/lib/index.js')});
    await page.addScriptTag({content:source.match(/<script>([\s\S]*?)<\/script>/)[1].replace('export default','window.component =')});
    await page.evaluate(template => {
      component.template = template;
      window.app = new Vue({components:{LimitDetails:component}, data: {check:{out_of_spec_count:1000,
        out_of_spec_details:Array.from({length:1000}, (_,i)=>({indicator:'指标',result_text:'结果'+i,
          components:[{item:'C',within_standard:null,reason_text:'缺少检测结果'}],
          numeric_verification:i===0?[{page:1,primary:'1.25',secondary:'-1.25',status:'numeric_uncertain',bbox_pdf:[1,2,3,4]}]:[]}))}},
        template:'<limit-details :check="check" />'}).$mount('#app');
    }, source.match(/<template>([\s\S]*?)<\/template>\s*<script>/)[1]);
    await page.getByText('查看超限及待确认明细', {exact:true}).click();
    assert.equal(await page.locator('.limit-entry').count(),20);
    await page.getByText('OCR数值候选与核验位置', {exact:true}).click();
    assert((await page.locator('.limit-entry').first().innerText()).includes('局部复核：-1.25'));
    await page.locator('section').first().locator('.btn-next').click();
    await page.getByText('检测结果：结果20；可接受标准：-',{exact:true}).waitFor();
    await page.evaluate(()=>{app.$children[0].pages.out_of_spec=50;});
    await page.getByText('检测结果：结果999；可接受标准：-',{exact:true}).waitFor();
    assert.equal(await page.locator('.limit-entry').count(),20);
    assert((await page.locator('.component-entry').first().innerText()).includes('待确认'));
    await page.evaluate(()=>{app.check={out_of_spec_count:25,out_of_spec_details:[{indicator:'历史'}]};});
    await page.getByText('历史明细不完整：仅可查看已保存记录，缺失明细不能自动补齐。',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>app.$children[0].pages.out_of_spec),1);
    assert.deepEqual(errors,[]);
    console.log('PASS browser: 1000 records/20 rows, next/last page, component limitation, history warning, reset, no JS errors');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
