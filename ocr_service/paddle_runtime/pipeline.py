import multiprocessing as mp
import os
import signal
import time
import traceback
from pathlib import Path
import pymupdf as fitz
import psutil
from PIL import Image
from common import dump,read,progress,map_points
from adapters import assemble
from imaging import render,visible_tables,line_crops


def worker(conn,engine,private,run):
    os.setsid()
    from adapters import Paddle,PaddleRec,Tesseract
    t=time.monotonic()
    try:
        adapter=Paddle(private,run) if engine=='paddle' else PaddleRec(private,run) if engine=='paddle-rec' else Tesseract()
        conn.send(dict(ready=True,load_seconds=time.monotonic()-t))
        while True:
            request=conn.recv()
            if request is None:break
            rid,path,granularity,*extra=request
            try: result=adapter.predict(path,granularity,sequence_candidates=extra[0]) if extra and extra[0] is not None else adapter.predict(path,granularity)
            except Exception as e:result=dict(status='failed',error=repr(e),traceback=traceback.format_exc(),spans=[])
            conn.send((rid,result))
    except BaseException as e:
        conn.send(dict(ready=False,error=repr(e),traceback=traceback.format_exc()))
    finally:conn.close()


class Engine:
    def __init__(self,name,config,run):
        if name not in ('paddle','paddle-rec','tesseract'):
            raise ValueError(f'unsupported OCR engine: {name}')
        self.name=name;self.config=config;self.run=Path(run);self.peak=0
        context=mp.get_context('spawn');self.conn,child=context.Pipe()
        self.p=context.Process(target=worker,args=(child,name,config['private_root'],str(run)))
        t=time.monotonic();self.p.start();child.close()
        if not self.conn.poll(config.get('model_load_timeout',120)):self.close();raise TimeoutError('model load timeout')
        ready=self.conn.recv()
        if not ready.get('ready'):self.close();raise RuntimeError(str(ready))
        self.load=ready['load_seconds'];self.startup=time.monotonic()-t

    def close(self):
        if self.p.is_alive():
            try:os.killpg(self.p.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        self.p.join(5)
        if self.p.is_alive():
            os.killpg(self.p.pid,signal.SIGKILL);self.p.join()
        self.conn.close()

    def predict(self,sample,timeout=None,cancel_event=None):
        if not self.p.is_alive():
            # A failed/terminated worker must not block independent later inputs.
            self.__init__(self.name,self.config,self.run)
        sid=sample['sample_id'];dest=self.run/'normalized'/self.name/(sid+'.json')
        if dest.exists():raise FileExistsError(f'refusing to overwrite {dest}')
        t=time.monotonic();timeout=self.config['crop_timeout'] if timeout is None else max(0,timeout)
        with Image.open(sample['image']) as im:
            size=list(im.size)
            if im.width*im.height>self.config['max_pixels']:raise ValueError('pixel budget exceeded')
        self.conn.send((sid,sample['image'],sample['granularity'],sample.get('sequence_candidates')))
        peak=0;status=None
        while time.monotonic()-t<timeout:
            if cancel_event is not None and cancel_event.is_set():
                self.close();result=dict(status='cancelled',spans=[],error='cancelled and actual worker group joined');break
            try:
                tree=[psutil.Process(self.p.pid)]+psutil.Process(self.p.pid).children(recursive=True)
                peak=max(peak,sum(p.memory_info().rss for p in tree if p.is_running()))
            except psutil.Error:pass
            if self.conn.poll(.05):
                try:
                    rid,result=self.conn.recv()
                    if rid!=sid:raise ValueError('late/mismatched result')
                except (EOFError,ValueError) as e:result=dict(status='failed',error=str(e),spans=[])
                break
            if not self.p.is_alive():result=dict(status='failed',error='worker exited',spans=[]);break
        else:
            self.close();result=dict(status='timeout',spans=[],error='actual worker group terminated and joined')
        self.peak=max(self.peak,peak)
        raw=result.pop('raw',None)
        dump(self.run/'raw'/self.name/(sid+'.json'),raw if raw is not None else result)
        if self.name=='tesseract' and raw is not None:
            (self.run/'raw'/self.name/(sid+'.tsv')).write_text(raw.get('stdout',''),encoding='utf-8')
        for i,s in enumerate(result['spans']):
            s['span_id']=f'{sid}:{i}';s['source_region_id']=sid
            s['polygon_page']=map_points(s['polygon_input_px'],sample['input_to_page']) if sample.get('input_to_page') else None
        result.update(sample_id=sid,engine=self.name,input=sample,input_size=size,
            text_assembled=assemble(result['spans']),seconds=time.monotonic()-t,peak_rss_tree_bytes=peak,
            review_state='unreviewed',content_observation='text_returned' if result['spans'] else
                'no_text_returned' if result['status']=='completed' else 'processing_failed')
        dump(dest,result)
        with (self.run/'records.jsonl').open('a',encoding='utf-8') as f:
            import json
            f.write(json.dumps({k:result[k] for k in ['sample_id','engine','seconds','peak_rss_tree_bytes','status','content_observation']})+'\n')
        return result


def crops(run,engines):
    config=read(run/'config.effective.json');samples=read(run/'predict_inputs.json')
    for name in engines:
        engine=Engine(name,config,run)
        try:
            for s in samples:engine.predict(s)
            dump(run/f'load-{name}-S1.json',dict(seconds=engine.load,startup_seconds=engine.startup,peak=engine.peak))
        finally:engine.close()
    progress(run,'S1','三份原始小裁剪同图双引擎真实执行，见 normalized 和 raw。')


def tables(run,engines):
    config=read(run/'config.effective.json');pdfs={x['document']:x for x in read(run/'pdfs.json')}
    geometries=read(run/'diagnostic_geometry.json');samples=[]
    for t in geometries:
        doc=fitz.open(pdfs[t['document']]['path']);page=doc[t['page']-1]
        prefix=f'd{t["document"]}p{t["page"]}t{t["table_index"]}'
        for suffix,box,kind,cell in [('table',t['bbox_pdf'],'table',None)]+[
            (f'r{c["row"]}c{c["column"]}',c['bbox_pdf'],'cell',c) for c in t['cells']]:
            sid=prefix+suffix;im=render(page,run/'images'/f'{sid}.png',config['scale'],box)
            samples.append(dict(sample_id=sid,granularity=kind,stage='S3',document=t['document'],page=t['page'],
                geometry_origin='manual_diagnostic_historical_grid',cell=cell,**im))
    dump(run/'table-predict-inputs.json',samples)
    for name in engines:
        engine=Engine(name,config,run)
        try:
            for i,s in enumerate(samples):
                engine.predict(s)
                if i%25==0:print('S3',name,i,'/',len(samples),flush=True)
        finally:engine.close()
    progress(run,'S3','全部历史几何表/记录框和完整格内已执行；几何诊断不计自动结果。')


def ablations(run,engines):
    config=read(run/'config.effective.json');pdfs={x['document']:x for x in read(run/'pdfs.json')}
    # Only image geometry is exported from historical crop metadata.
    source=Path(config['repo_root'])/'test/ocr_completion_20260920/original-small-regions/results.json'
    boxes=[{k:x[k] for k in ['document','page','bbox_pdf']} for x in read(source)]
    samples=[]
    for b in boxes:
        page=fitz.open(pdfs[b['document']]['path'])[b['page']-1]
        # Both arms render the complete PDF composition at the identical rectangle.
        for scale in [1.5,3]:
            sid=f'source{b["document"]}-{scale}'
            samples.append(dict(sample_id=sid,granularity='line',stage='S2',**render(page,run/'images'/f'{sid}.png',scale,b['bbox_pdf'])))
    # Dense license blocks and the two-column business certificate: whole vs all image row bands.
    for n,pn in [(1,1),(2,2),(3,2)]:
        page=fitz.open(pdfs[n]['path'])[pn-1]
        box=([page.rect.width*.55,page.rect.height*.06,page.rect.width*.96,page.rect.height*.75]
             if n in (2,3) else [0,page.rect.height*.28,page.rect.width,page.rect.height*.85])
        sid=f'body{n}'
        s=dict(sample_id=sid,granularity='block',stage='S2',**render(page,run/'images'/f'{sid}.png',3,box))
        samples.append(s)
        for i,line in enumerate(line_crops(s['image'],run/'images'/sid)):
            m=[r[:] for r in s['input_to_page']];m[1][2]+=line['bbox_input'][1]/3
            samples.append(dict(sample_id=f'{sid}-line{i}',granularity='line',stage='S2',parent_region_id=sid,
                                image=line['image'],input_to_page=m,segmentation='image_projection_diagnostic'))
    dump(run/'ablation-predict-inputs.json',samples)
    for name in engines:
        engine=Engine(name,config,run)
        try:
            for s in samples:engine.predict(s)
        finally:engine.close()
    progress(run,'S2','3处相同PDF可见内容的1.5/3倍采样、3正文整块/全部投影行真实执行。投影不宣称解决双栏。')


def local_blocks(spans):
    """Separate spatial columns using observed line boxes; retain unresolved spanning lines."""
    if not spans:return []
    boxes=[]
    for s in spans:
        p=s.get('polygon_page')
        if p:boxes.append((min(x[0] for x in p),max(x[0] for x in p),s))
    if len(boxes)<4:return [dict(spans=spans,text=assemble(spans),order='engine_local_order_unverified')]
    starts=sorted(set(b[0] for b in boxes));candidate=None
    for a,b in zip(starts,starts[1:]):
        split=(a+b)/2
        left=[s for x0,x1,s in boxes if x1<split];right=[s for x0,x1,s in boxes if x0>=split]
        crossing=[s for x0,x1,s in boxes if x0<split<=x1]
        if len(left)>=2 and len(right)>=2 and len(crossing)<=len(boxes)*.15:
            score=b-a
            if candidate is None or score>candidate[0]:candidate=(score,left,right,crossing)
    if candidate:
        return [dict(spans=s,text=assemble(s),order='within_column_engine_order',role=label)
                for label,s in zip(['left','right','spanning_unresolved'],candidate[1:]) if s]
    return [dict(spans=spans,text=assemble(spans),order='unresolved_multi_column_possible')]


def documents(run,name,engine=None,sources=None):
    config=read(run/'config.effective.json');owns_engine=engine is None
    if engine is None:engine=Engine(name,config,run)
    started=time.monotonic()
    layout=None
    if config.get('layout_model_dir'):
        from layout import DocumentLayout
        layout=DocumentLayout(config['layout_model_dir'])
    try:
        for src in (sources if sources is not None else read(run/'pdfs.json')):
            dt=time.monotonic();doc=fitz.open(src['path'])
            for pn,page in enumerate(doc,1):
                pt=time.monotonic();prefix=f'auto-d{src["document"]}p{pn}'
                output=dict(document=src['document'],page=pn,status='partial',tables=[],unresolved_reasons=['content_unreviewed','global_reading_order_unverified'])
                try:
                    if time.monotonic()-dt>config['document_timeout'] or time.monotonic()-started>config['total_timeout']:raise TimeoutError('document/total budget')
                    rt=time.monotonic();im=render(page,run/'images'/f'{prefix}.png',config.get('page_scale',config['scale']));output['render_seconds']=time.monotonic()-rt
                    lt=time.monotonic();ts,inc=visible_tables(page,config['repo_root'],config.get('geometry_scale',1.5));output.update(layout_seconds=time.monotonic()-lt,incomplete_regions=inc)
                    s=dict(sample_id=prefix,stage='S4',granularity='page',geometry_origin='automatic',**im)
                    from imaging import original_page_raster
                    try:
                        native=original_page_raster(page,run/'images'/f'{prefix}-original-raster.png',im)
                        if native:s['original_raster']=native
                    except Exception as exc:
                        output['original_raster_observation']=dict(status='unavailable',exception_type=type(exc).__name__)
                    if layout is not None:
                        try:output['layout_result']=layout.predict(s)
                        except Exception as exc:
                            output['layout_result']=dict(status='failed',exception_type=type(exc).__name__,regions=[])
                    output['page_result']=engine.predict(s,config['page_timeout']-(time.monotonic()-pt))
                    output['blocks']=local_blocks(output['page_result']['spans'])
                    if config.get('content_recovery'):
                        from content_recovery import recover_dense
                        effective,candidates=recover_dense(output['page_result'],output['blocks'],engine,run,
                            output.get('layout_result',{}).get('regions',[]))
                        output['original_page_result']=output['page_result']
                        output['page_result']=effective;output['dense_candidates']=candidates
                        output['blocks']=local_blocks(effective['spans'])
                    from segmentation import detection_line_boxes
                    output['detection_lines']=detection_line_boxes(output['page_result']['spans'])
                    output['page_result']['text_order']='engine_sequence_diagnostic_only; use blocks, global order unresolved'
                    if layout is not None and output.get('layout_result',{}).get('status')=='completed':
                        from region_recovery import recover_regions
                        remaining=config['page_timeout']-(time.monotonic()-pt)
                        if remaining>0:
                            output['regional_recognition']=recover_regions(output,engine,run,min(config['crop_timeout'],remaining))
                    if config.get('text_roles'):
                        from text_roles import stamp_regions
                        output['stamp_regions']=stamp_regions(output.get('original_page_result',output['page_result']))
                    for ti,t in enumerate(ts):
                        table={**t,'cells':[]}
                        for ci,c in enumerate(t['cells']):
                            cell={**c,'content_observation':'not_processed'}
                            remaining=config['page_timeout']-(time.monotonic()-pt)
                            if remaining>0:
                                sid=f'{prefix}-t{ti}-cell{ci}'
                                im=render(page,run/'images'/f'{sid}.png',config['scale'],c['bbox_pdf'])
                                cell['result']=engine.predict(dict(sample_id=sid,stage='S4',granularity='cell',
                                    geometry_origin='automatic_visible_grid',**im),min(config['crop_timeout'],remaining))
                                cell['content_observation']=cell['result']['content_observation']
                                if config.get('content_recovery'):
                                    from content_recovery import recover_cell
                                    recover_cell(cell,engine,run,remaining)
                                if config.get('text_roles'):
                                    from text_roles import apply_cell_roles
                                    apply_cell_roles(cell,output['stamp_regions'])
                                if config.get('sparse_ink_diagnostic') and not config.get('content_recovery') and not cell['result']['text_assembled']:
                                    import cv2
                                    from segmentation import sparse_ink_box
                                    rgb=cv2.imread(im['image']);box=sparse_ink_box(rgb)
                                    if box:
                                        x0,y0,x1,y1=box;path=run/'images'/f'{sid}-ink.png'
                                        cv2.imwrite(str(path),rgb[y0:y1,x0:x1])
                                        matrix=[r[:] for r in im['input_to_page']]
                                        matrix[0][2]+=x0/config['scale'];matrix[1][2]+=y0/config['scale']
                                        left=config['page_timeout']-(time.monotonic()-pt)
                                        if left>0:
                                            cell['sparse_candidate']=engine.predict(dict(sample_id=sid+'-ink',stage='S4',
                                                parent_region_id=sid,granularity='line-rec',image=str(path.resolve()),
                                                input_to_page=matrix,geometry_origin='automatic_sparse_ink'),min(config['crop_timeout'],left))
                                        cell['unresolved_reasons']=['visible_ink_without_detected_text','sparse_recognition_not_validated']
                                        output['unresolved_reasons'].append('sparse_ink_requires_review')
                                        # Keep original result: same-model tight recognition confused / with 1 in probes.
                                        cell['sparse_candidate_adopted']=False
                            else:output['unresolved_reasons'].append('page_budget_exhausted')
                            table['cells'].append(cell)
                        output['tables'].append(table)
                except Exception as e:
                    output['error']=repr(e);output['unresolved_reasons'].append('processing_failed')
                if config.get('local_adjudication_model_dir') and output.get('page_result') and not output.get('error'):
                    from bounded_review import run_bounded_review
                    output=run_bounded_review(output,config['local_adjudication_model_dir'],run/'bounded-review'/prefix,engine=engine)
                    if config.get('seal_detection_model_dir'):
                        from seal_review import review_page_seals
                        try:
                            output=review_page_seals(output,config['seal_detection_model_dir'],config['local_adjudication_model_dir'],engine,run/'seal-review'/prefix)
                        except Exception as exc:
                            # 专用局部复核失败不能抹去已经完成的主识别，也不能将失败当作通过。
                            output.setdefault('optional_review_failures',[]).append(dict(stage='seal_review',reason=repr(exc)))
                output['end_to_end_seconds']=time.monotonic()-pt
                dump(run/'pages'/name/f'doc{src["document"]}-page{pn}.json',output)
                print('S4',prefix,round(output['end_to_end_seconds'],2),'tables',len(output['tables']),flush=True)
    finally:
        if owns_engine:engine.close()
    dump(run/'S4-resource.json',dict(end_to_end_seconds=time.monotonic()-started,model_load_seconds=engine.load,
        startup_seconds=engine.startup,peak_rss_tree_bytes=engine.peak,measurement='50ms sampled engine process tree RSS; GPU null',gpu_peak_bytes=None))
    progress(run,'S4','同一配置完成三份PDF自动页面尝试，逐页保留partial、布局和失败证据。')
