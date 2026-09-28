# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import fitz,json,sys,math
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from imaging import render
root=Path('[LOCAL_PATH_OMITTED]')
pdf=next(Path('[LOCAL_PATH_OMITTED]').glob('fcs_edd7103be1f64131_*.pdf'))
p=fitz.open(pdf);page=p[4]
r=json.loads(Path('[LOCAL_PATH_OMITTED]').read_text())
c=r['tables'][1]['cells'][0]
print(c['result'].keys())
print(json.dumps(c['result']['spans'],ensure_ascii=False)[:6000])
year=c['result']['spans'][4]; day=c['result']['spans'][5]
pts=year['polygon_page']+day['polygon_page']; bb=[min(x[0] for x in pts),min(x[1] for x in pts),max(x[0] for x in pts),max(x[1] for x in pts)]
h=bb[3]-bb[1]; margin=h*.2
box=[max(c['bbox_pdf'][0],bb[0]-margin),max(c['bbox_pdf'][1],bb[1]-margin),min(c['bbox_pdf'][2],bb[2]+margin),min(c['bbox_pdf'][3],bb[3]+margin)]
infos=page.get_image_info();info=max(infos,key=lambda x:x['width']*x['height']);b=info['bbox'];scale=max(info['width']/(b[2]-b[0]),info['height']/(b[3]-b[1]))
g=render(page,root/'doc2-date-native-composite.png',scale,box)
g.update(sample_id='doc2-date-native-composite',stage='S4',granularity='line-rec',geometry_origin='actual_year_day_detection_union',source_span_ids=[year['span_id'],day['span_id']],source_pdf=str(pdf),page_index=4)
(root/'input.json').write_text(json.dumps(g,ensure_ascii=False,indent=2));print(json.dumps(g,ensure_ascii=False))
render(page,root/'doc2-date-scale3.png',3,box)
