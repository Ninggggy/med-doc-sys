"""显式启用的本地解析入口；不注册或替换默认PDF解析器。"""
import copy
from pathlib import Path
import threading
from common import read, dump
from pipeline import Engine, documents
from runtime_queue import ModelQueue
from replay import to_legacy_view

BACKEND = 'ppocr_v6_medium_experimental'


def box(poly):
    return [min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly)]


def consumer_page(page):
    view = to_legacy_view(page)
    r = page['page_result']
    view.update(status='partial', content_available=True, ocr_backend=BACKEND,
        ocr_calls=1, raw_result=copy.deepcopy(r), unresolved_evidence=copy.deepcopy(page['unresolved_reasons']),
        page_bbox=r['input'].get('clip'))
    lines=[]
    for bi, block in enumerate(page.get('blocks') or [{'spans':r['spans']}]):
        for span in block['spans']:
            poly=span.get('polygon_page')
            if not poly: continue
            bounds=box(poly)
            if any(t['bbox_pdf'][0] <= (bounds[0]+bounds[2])/2 <= t['bbox_pdf'][2]
                   and t['bbox_pdf'][1] <= (bounds[1]+bounds[3])/2 <= t['bbox_pdf'][3] for t in view['tables']): continue
            lines.append(dict(text=span['text_raw'],bbox=bounds,polygon=poly,block_id=bi,
                granularity=span['granularity'],source=BACKEND,source_span_id=span['span_id']))
    view['lines']=lines
    view['text']='\n'.join(l['text'] for l in lines)
    if not view['page_bbox']:
        im=r['input']; size=r['input_size']; scale=im.get('scale',3)
        view['page_bbox']=[0,0,size[0]/scale,size[1]/scale]
    view['errors']=[dict(code='text_assembly_unresolved',stage='assembly',
        reason='实验识别内容与阅读顺序尚未人工核对',bbox_pdf=view['page_bbox'])]
    for ti,t in enumerate(view['tables']):
        t.update(page=page['page'],table_index=ti,id=f'p{page["page"]}-t{ti}',needs_review=True)
        # 申请表消费者使用真实格内行框，不能把整格外框当作字框。
        for cell in t['cells']:
            result=cell.get('result',{})
            for s in result.get('body_spans',result.get('spans',[])):
                if s.get('polygon_page'):
                    view['lines'].append(dict(text=s['text_raw'],bbox=box(s['polygon_page']),polygon=s['polygon_page'],
                        source=BACKEND,granularity=s['granularity'],source_span_id=s['span_id'],table_index=ti))
    # 旧消费者把此集合称为words；每项明确保留line粒度，绝不拆出虚构字框。
    view['words']=copy.deepcopy(view['lines'])
    view['positioned_text_granularity']='line; legacy collection name words'
    return view


class QueuedEngine:
    def __init__(self, config, run):
        self.engine=Engine('paddle',config,run)
        self.queue=ModelQueue(self.engine,capacity=2)
    def predict(self,sample,timeout=None):
        return self.queue.submit(sample,timeout=timeout).result()
    def __getattr__(self,name): return getattr(self.engine,name)
    def close(self): self.queue.close()


class ExperimentalParser:
    def __init__(self,run):
        self.run=Path(run).resolve();self.config=read(self.run/'config.effective.json')
        self.engine=None;self.lock=threading.Lock();self.sequence=0
    def parse_file(self,path):
        if Path(path).suffix.lower()!='.pdf': raise ValueError('实验入口仅接受PDF')
        with self.lock:
            if self.engine is None:self.engine=QueuedEngine(self.config,self.run)
            self.sequence+=1;doc_id=self.sequence
            documents(self.run,'paddle',engine=self.engine,sources=[dict(document=doc_id,path=str(Path(path).resolve()))])
            paths=sorted((self.run/'pages/paddle').glob(f'doc{doc_id}-page*.json'),key=lambda p:int(p.stem.split('page')[1]))
            project=consumer_page
            if self.config.get('consumer')=='product':
                from product_consumer import consumer_page as project
            pages=[project(read(p)) for p in paths]
            dump(self.run/'consumers'/f'doc{doc_id}.json',pages)
            return pages
    def close(self):
        if self.engine:self.engine.close()


class ExperimentalFormParser:
    def __init__(self,parser):
        from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
        self.parser=parser;self.consumer=FilingChangeFormParserService()
    def parse_form_file(self,path):
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        from agent.agent_backend.services.filing_parse_outcome import form_outcome
        parsed=build_pdf_form_from_pages(self.parser.parse_file(path),source_file=str(path))
        parsed['ocr_backend']=BACKEND
        result=self.consumer.form_from_pdf_result(parsed,file_path=str(path))
        result.update(form_outcome(result))
        return result
    def __getattr__(self,name): return getattr(self.consumer,name)
