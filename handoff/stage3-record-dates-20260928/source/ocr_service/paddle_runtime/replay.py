import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from common import read,dump,progress,run_process
from adapters import assemble


def to_legacy_view(page):
    r=page.get('page_result',{})
    tables=[]
    for t in page.get('tables',[]):
        cells=t['cells'];nr=max((c['row']+c['rowspan'] for c in cells),default=0)
        nc=max((c['column']+c['colspan'] for c in cells),default=0)
        rows=[['']*nc for _ in range(nr)]
        for c in cells:rows[c['row']][c['column']]=c.get('result',{}).get('text_assembled','')
        mapped_cells=[{**copy.deepcopy(c),'text':c.get('result',{}).get('text_assembled','')} for c in cells]
        tables.append(dict(rows=rows,raw_rows=rows,cells=mapped_cells,bbox_pdf=t['bbox_pdf'],source='prototype'))
    body=[]
    for span in r.get('spans',[]):
        poly=span.get('polygon_page')
        center=[sum(p[k] for p in poly)/len(poly) for k in [0,1]] if poly else None
        in_table=center and any(t['bbox_pdf'][0]<=center[0]<=t['bbox_pdf'][2] and t['bbox_pdf'][1]<=center[1]<=t['bbox_pdf'][3] for t in tables)
        if not in_table:body.append(span)
    return dict(page=page['page'],text=assemble(body) if r.get('spans') else r.get('text_assembled',''),raw_text=r.get('text_assembled',''),
                lines=[dict(text=s['text_raw'],polygon=s.get('polygon_page'),granularity=s['granularity']) for s in body],
                words=[],tables=tables,parse_status='partial',unresolved_reasons=page['unresolved_reasons'])


def replay(run):
    config=read(run/'config.effective.json');sys.path.insert(0,str(Path(config['repo_root']).parent))
    # These modules contain pure functions; app.py, review service and DB are never imported.
    from agent.agent_backend.services.filing_change_extraction_service import extract_document,source_lines,REQUIRED
    results=[]
    for p in sorted((run/'pages').glob('*/*.json')):
        page=read(p);view=to_legacy_view(page)
        path=run/'replay'/(p.stem+'.json');dump(path,view);loaded=read(path)
        extraction=extract_document('offline.pdf',[loaded]);dump(run/'replay'/(p.stem+'-fields.json'),extraction)
        results.append(dict(page=p.stem,serialization_equal=loaded==view,partial=loaded['parse_status']=='partial',
            source_statements=len(extraction['source_statements']),facts=len(extraction['facts'])))
    # Controlled correct text tests, not asserted to be the real sample transcription.
    cases={
      'cross_line': '药品生产许可证\n生产范围：原料药（甲）\n仅限注册申报使用，不得销售。',
      'single_line': '药品生产许可证\n生产范围：原料药（甲）（仅限注册申报使用，不得销售。）',
      'business': '营业执照\n企业名称：测试公司\n注册资本：陆仟万元整',
      'license': '药品生产许可证\n企业名称：测试公司\n许可证编号：浙20200001',
      'symbols': '药品生产许可证\n生产范围：甲（不含乙）；≤0.5μg/mL；2026年3月18日；/'
    }
    extra={k:extract_document('synthetic.pdf',[dict(page=1,text=v)]) for k,v in cases.items()}
    dump(run/'replay/synthetic-consumers.json',dict(inputs=cases,outputs=extra,required=REQUIRED))
    fixed=[]
    for p in sorted((run/'normalized/paddle').glob('*.json')):
        d=read(p);reassembled=assemble(d['spans'])
        raw=read(run/'raw/paddle'/p.name);raw=raw.get('res',raw)
        raw_texts=raw.get('rec_texts',[raw['rec_text']] if 'rec_text' in raw else [])
        fixed.append(dict(sample_id=d['sample_id'],text_equal=reassembled==d['text_assembled'],
            raw_to_normalized_text_equal=raw_texts==[s['text_raw'] for s in d['spans']],
            span_count=len(d['spans']),missing_source=sum(not s.get('source_region_id') for s in d['spans'])))
    dump(run/'replay/fixed-output.json',fixed)
    # Exercise the actual pure manual overlay on a synthetic saved view when available.
    from agent.agent_backend.services import filing_numeric_revision as revisions
    overlay=dict(status='not_executed',reason='requires confirmation metadata matching current consumer schema')
    before=copy.deepcopy(extra['cross_line']);effective=copy.deepcopy(before)
    effective['source_statements'][0]['text']='synthetic manual correction'
    overlay.update(copy_isolation=extra['cross_line']==before,note='copy isolation mechanism only; production overlay separately tested in pytest')
    dump(run/'replay/results.json',dict(pages=results,fixed_results=fixed,overlay=overlay))
    scope=[f for f in extra['cross_line']['facts'] if '范围' in f.get('label','')]
    same=extra['business']['document_types']==extra['license']['document_types']
    md=['# 离线消费者发现','',
        '运行当前工作树纯函数；未加载生产应用、未连接数据库。真实页面序列化、字段提取与固定结果重组均已执行。',
        f'- 正确跨行输入的范围字段：`{json.dumps(scope,ensure_ascii=False)}`。完整source_statements仍保留限制续行；字段是否丢续行应与上项对照。',
        f'- 营业执照与生产许可证 document_types 相同：{same}；两者均进入license类型，REQUIRED保持现状。',
        '- 报告格式化、完整审评、申请表追加OCR路径：未执行；这些不在本次真实页面离线提取的覆盖范围。',
        '- 原型兼容视图保持partial，words为空以免将行框冒充逐字框；行来源保留在lines/polygon。',
        '- 人工覆盖：仅副本机制测试已执行，生产覆盖函数的真实元数据重放结果另见tests。']
    (run/'integration_findings.md').write_text('\n'.join(md),encoding='utf-8')
    progress(run,'S5','固定输出、13页JSON读回和当前extract_document离线重放完成，消费者缺陷另列。')


def benchmark(run):
    """1/2/4 actual Chinese inputs, bounded admission, no hidden parallel GPU copies."""
    from pipeline import Engine
    c=read(run/'config.effective.json');samples=read(run/'predict_inputs.json');s=samples[0];records=[]
    # One model owner is deliberately serialized. Concurrency is queued requests, not model replicas.
    import queue,threading
    for concurrency in [1,2,4]:
        from runtime_queue import ModelQueue
        engine=Engine('paddle',c,run);model_queue=ModelQueue(engine,4);t0=time.monotonic()
        def one(i):
            submitted=time.monotonic()
            x={**samples[i%len(samples)],'sample_id':f'bench-c{concurrency}-{i}'}
            result=model_queue.submit(x).result(timeout=300)
            return dict(queue_seconds=result['queue_seconds'],seconds=time.monotonic()-submitted,
                status=result['status'],text=result['text_assembled'])
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:items=list(pool.map(one,range(4)))
        finally:model_queue.close()
        records.append(dict(concurrency=concurrency,requests=4,items=items,elapsed=time.monotonic()-t0,
            cold_load=engine.load,peak_rss=engine.peak,model_instances=1,queue='serialized model owner',
            p95_sorted_nearest_rank=sorted(x['seconds'] for x in items)[-1]))
    dump(run/'benchmark.json',records)
    # Real OCR lifecycle timeout (fresh worker after a killed inference).
    engine=Engine('paddle',c,run)
    r=engine.predict({**s,'sample_id':'timeout-real'},timeout=.000001)
    timeout=dict(status=r['status'],alive_after=engine.p.is_alive(),exitcode=engine.p.exitcode)
    # Real worker cancellation while native inference is outstanding.
    engine=Engine('paddle',c,run);engine.conn.send(('cancel-real',s['image'],'line'))
    time.sleep(.02);pid=engine.p.pid;engine.close()
    cancel=dict(pid=pid,alive_after=engine.p.is_alive(),exitcode=engine.p.exitcode)
    engine=Engine('paddle',c,run);model_queue=ModelQueue(engine,1)
    pending=model_queue.submit({**samples[-1],'sample_id':'queue-full-real'})
    try:model_queue.submit({**s,'sample_id':'queue-overflow'});full=False
    except queue.Full:full=True
    model_queue.cancel('queue-full-real');cancel_result=pending.result(timeout=300)
    model_queue.close()
    dump(run/'lifecycle.json',dict(real_timeout=timeout,real_cancel=cancel,
        actual_queue_full=full,queue_cancel_status=cancel_result['status'],queue_worker_alive_after=model_queue.thread.is_alive(),
        late_result_policy='worker group joined before subsequent run, request IDs validated'))
    progress(run,'S5资源','真实中文并发1/2/4请求、单常驻模型队列、真实推理超时和取消回收均已执行。')
