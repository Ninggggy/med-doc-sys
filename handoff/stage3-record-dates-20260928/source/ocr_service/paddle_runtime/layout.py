"""仅加载官方版面/顺序模型，不加载 OCR、公式或图表识别产线。"""
import json
import time
import resource
import sys
from pathlib import Path


class DocumentLayout:
    def __init__(self, model_dir):
        from paddlex import create_model
        self.model = create_model(model_name='PP-DocLayoutV3', model_dir=str(model_dir),
                                  device='cpu', cpu_threads=2, enable_mkldnn=False)

    def predict(self, sample):
        from common import map_points
        start = time.monotonic()
        results = list(self.model.predict(sample['image'], batch_size=1, skip_order_labels=[]))
        if len(results) != 1:
            raise ValueError('layout result count mismatch')
        raw = results[0].json
        if isinstance(raw, str):
            raw = json.loads(raw)
        regions = []
        for index, entry in enumerate(raw.get('res', raw)['boxes']):
            x0, y0, x1, y1 = entry['coordinate']
            poly = entry.get('polygon_points') or [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
            points = map_points(poly, sample['input_to_page'])
            regions.append(dict(region_id=f'layout:{index}', label=entry['label'],
                order=entry.get('order'), score=entry['score'], polygon_pdf=points,
                bbox_pdf=[min(p[0] for p in points),min(p[1] for p in points),
                          max(p[0] for p in points),max(p[1] for p in points)]))
        return dict(model='PP-DocLayoutV3', raw=raw, regions=regions,
                    seconds=time.monotonic()-start, status='completed',
                    peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024))


if __name__ == '__main__':
    import argparse
    from common import read, dump
    p = argparse.ArgumentParser()
    p.add_argument('--model-dir', required=True)
    p.add_argument('--pages', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    model = DocumentLayout(args.model_dir)
    for path in sorted(Path(args.pages).glob('*.json')):
        target = Path(args.output) / path.name
        if target.exists():
            continue
        page = read(path)
        result = model.predict(page['page_result']['input'])
        dump(target, result)
        print(path.name, len(result['regions']), round(result['seconds'], 3), flush=True)
