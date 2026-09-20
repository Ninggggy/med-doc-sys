from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse
import asyncio
import os
import re
import time
import math
import unicodedata
import subprocess
import tempfile
from pathlib import Path
import numpy as np
import cv2
import pytesseract

app = FastAPI(title="Tesseract OCR Service")


def positive_setting(name, default):
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f'{name} must be positive')
    return value


OCR_EXECUTION_TIMEOUT = positive_setting('OCR_EXECUTION_TIMEOUT_SECONDS', 120)


class OCRCapacityError(Exception):
    pass


class OCRExecutionTimeout(Exception):
    pass


class OCRExecutor:
    """有界等待，线程取消不提前释放仍运行的Tesseract占用。"""
    def __init__(self, concurrency=1, waiting=4, queue_timeout=30):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.capacity = concurrency + waiting
        self.queue_timeout = queue_timeout
        self.admitted = 0

    async def run(self, function, *args, admission_deadline=None):
        if self.admitted >= self.capacity:
            raise OCRCapacityError()
        self.admitted += 1
        acquired = False
        worker = None
        try:
            try:
                remaining = admission_deadline - time.monotonic() if admission_deadline is not None else self.queue_timeout
                if remaining <= 0:
                    raise OCRExecutionTimeout()
                await asyncio.wait_for(self.semaphore.acquire(), timeout=min(self.queue_timeout, remaining))
                acquired = True
            except asyncio.TimeoutError:
                if admission_deadline is not None and time.monotonic() >= admission_deadline:
                    raise OCRExecutionTimeout() from None
                raise OCRCapacityError() from None
            worker = asyncio.create_task(asyncio.to_thread(function, *args))
            # shield保护真正的执行；客户端取消只停止等待，不取消工作计数。
            return await asyncio.shield(worker)
        finally:
            def release(task=None):
                if task is not None and not task.cancelled():
                    task.exception()  # 已取消请求的异常也被消费，不输出正文/内部异常。
                if acquired:
                    self.semaphore.release()
                self.admitted -= 1
            if worker is not None:
                if worker.done():
                    release(worker)
                else:
                    worker.add_done_callback(release)
            else:
                release()


executor = OCRExecutor(
    positive_setting('OCR_CONCURRENCY', 1),
    positive_setting('OCR_MAX_WAITING', 4),
    positive_setting('OCR_QUEUE_TIMEOUT_SECONDS', 30),
)


def read_image(file_bytes: bytes):
    arr = np.frombuffer(file_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


@app.get("/health")
def health():
    return {"status": "ok"}


def numeric_batch_strings(images, lang, deadline):
    """有界独立图清单，输出必须逐区域对应；失败由调用方保留待核对。"""
    if not 1 <= len(images) <= 4:
        raise ValueError('invalid_numeric_batch_size')
    with tempfile.TemporaryDirectory(prefix='ocr-numeric-') as directory:
        paths = []
        for index, pixels in enumerate(images):
            if time.monotonic() >= deadline:
                raise OCRExecutionTimeout()
            path = Path(directory) / f'{index}.png'
            if not cv2.imwrite(str(path), pixels):
                raise ValueError('numeric_image_write_failed')
            paths.append(str(path))
        listing = Path(directory) / 'images.txt'
        listing.write_text('\n'.join(paths)+'\n', encoding='utf8')
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise OCRExecutionTimeout()
        result = subprocess.run([pytesseract.pytesseract.tesseract_cmd, str(listing), 'stdout',
                                 '-l', lang, '--oem', '1', '--psm', '7'],
                                capture_output=True, check=True, timeout=remaining)
        parts = result.stdout.decode('utf8').split('\f')
        if len(parts) == len(images)+1 and not parts[-1].strip():
            parts.pop()
        if len(parts) != len(images):
            raise ValueError('numeric_batch_output_mismatch')
        return [part.strip() for part in parts]


def verify_numeric_regions(gray, data, lang, deadline):
    """复核只提供证据，不替换主识别值；所有子调用共享请求的剩余预算。"""
    height, width = gray.shape[:2]
    words = []
    for index, raw in enumerate(data.get('text', [])):
        text = str(raw).strip()
        if not text:
            continue
        x, y, w, h = (int(data[key][index]) for key in ('left', 'top', 'width', 'height'))
        words.append({'index': index, 'text': text, 'box': [x, y, x+w, y+h]})
    regions = []
    for word in words:
        if not re.search(r'\d', word['text']):
            continue
        x0, y0, x1, y1 = word['box']
        h = max(1, y1-y0)
        # 原词框可能已漏掉负号；至少保留一个字高的左侧空间。
        box = [max(0, x0-h), max(0, y0-h//2), min(width, x1+h), min(height, y1+h//2+1)]
        nearby = [other for other in words if other is not word
                  and abs((other['box'][1]+other['box'][3]-y0-y1)/2) <= h/2
                  and other['box'][0] <= x1+h and other['box'][2] >= x0-h]
        # 相邻的独立比较符、单位和指数可能不在数值词框内。
        for other in nearby:
            b = other['box']
            box = [max(0, min(box[0], b[0]-h)), max(0, min(box[1], b[1]-h//2)),
                   min(width, max(box[2], b[2]+h)), min(height, max(box[3], b[3]+h//2+1))]
        indices = {word['index'], *(other['index'] for other in nearby)}
        # 扩展符号留白后可能切到再相邻词的尾/首字母。把相交的同行词
        # 完整纳入候选，不能拿裁出的字母碎片与未包含该词的主候选比较。
        # 仅补齐已有框，不继续逐词增加一个字高，避免无界扩大到整行。
        while True:
            touched = [other for other in words if other['index'] not in indices
                       and abs((other['box'][1]+other['box'][3]-y0-y1)/2) <= h/2
                       and other['box'][0] < box[2] and other['box'][2] > box[0]]
            if not touched:
                break
            for other in touched:
                b = other['box']
                indices.add(other['index'])
                box = [max(0, min(box[0], b[0]-1)), max(0, min(box[1], b[1]-1)),
                       min(width, max(box[2], b[2]+1)), min(height, max(box[3], b[3]+1))]
        regions.append({'box': box, 'indices': indices})
    # 相交且同一文字行的区域合并，不按每个数字/字符启动子进程。
    merged = []
    for region in regions:
        changed = True
        while changed:
            changed = False
            for old in list(merged):
                a, b = region['box'], old['box']
                overlap_y = min(a[3], b[3])-max(a[1], b[1])
                if min(a[2], b[2]) > max(a[0], b[0]) and overlap_y > min(a[3]-a[1], b[3]-b[1])*.5:
                    region = {'box': [min(a[0],b[0]), min(a[1],b[1]), max(a[2],b[2]), max(a[3],b[3])],
                              'indices': region['indices'] | old['indices']}
                    merged.remove(old)
                    changed = True
        merged.append(region)
    results = []
    def comparable(value):
        # 只统一排版空白和全角形式；保留符号、指数、单位及大小写差异。
        normalized = unicodedata.normalize('NFKC', value).replace('−', '-')
        numbers = re.findall(r'[+-]?\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]\s*[+-]?\s*\d+)?', normalized)
        return (re.sub(r'\s+', '', normalized), [re.sub(r'\s+', '', n) for n in numbers])
    pending = []
    def flush_pending():
        batch = list(pending)
        pending.clear()
        if not batch:
            return
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            for item, _ in batch:
                item['reason_code'] = 'numeric_verification_budget_exhausted'
            return
        try:
            secondary = ([pytesseract.image_to_string(batch[0][1], lang=lang,
                         config='--oem 1 --psm 7', timeout=remaining).strip()]
                         if len(batch) == 1 else
                         numeric_batch_strings([pixels for _, pixels in batch], lang, deadline))
            if len(secondary) != len(batch):
                raise ValueError('numeric_batch_output_mismatch')
        except Exception:
            # 已完成批次仍保留；本批失败不猜配输出，也不重置期限重试。
            return
        for (item, _), value in zip(batch, secondary):
            item['secondary'] = value
            if value and comparable(item['primary']) == comparable(value):
                item.update(status='verified', reason_code='numeric_candidates_agree')
            else:
                item['reason_code'] = 'numeric_candidates_disagree'

    for region in merged:
        x0, y0, x1, y1 = region['box']
        included = [w for w in words if w['index'] in region['indices'] or
                    (x0 <= (w['box'][0]+w['box'][2])/2 <= x1 and y0 <= (w['box'][1]+w['box'][3])/2 <= y1)]
        included.sort(key=lambda w: (w['box'][1]//max(1,y1-y0), w['box'][0]))
        item = {'word_indices': sorted(w['index'] for w in included), 'bbox_pixel': region['box'],
                'primary': ' '.join(w['text'] for w in included), 'secondary': '',
                'status': 'numeric_uncertain', 'reason_code': 'numeric_verification_failed'}
        results.append(item)
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            item['reason_code'] = 'numeric_verification_budget_exhausted'
            continue
        if x1 <= x0 or y1 <= y0:
            item['reason_code'] = 'numeric_region_invalid'
            continue
        # PDF UserUnit/高DPI可把字形放大到原扫描的数倍；原尺寸的两次
        # Tesseract可能产生相同误读。复核将过大的数值字形归一到32像素，
        # 不做高斯模糊、不改主结果、不增OCR调用或重置执行预算。
        numeric_heights = [w['box'][3]-w['box'][1] for w in included
                           if re.search(r'\d', w['text']) and w['box'][3] > w['box'][1]]
        scale = min(1.0, 32.0/float(np.median(numeric_heights))) if numeric_heights else 1.0
        verification_image = gray[y0:y1, x0:x1]
        if scale < 1.0:
            verification_image = cv2.resize(verification_image,
                (max(1, round((x1-x0)*scale)), max(1, round((y1-y0)*scale))),
                interpolation=cv2.INTER_AREA)
        item['secondary_image_scale'] = scale
        # 图像准备也属于同一个请求预算。
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            item['reason_code'] = 'numeric_verification_budget_exhausted'
            continue
        pending.append((item, verification_image))
        if len(pending) == 4:
            flush_pending()
    flush_pending()
    return results


def preserve_thin_symbols(gray, processed, deadline):
    """仅还原原图中紧邻字形的细横线；不绘制、推断或替换文字。"""
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    components = stats[1:]
    output = processed.copy()
    for x, y, w, h, area in components:
        if time.monotonic() >= deadline:
            raise OCRExecutionTimeout()
        if h < 1 or not 2 <= w / h <= 12 or area < 3:
            continue
        a, b, c, d, _ = components.T
        nearby = ((a >= x+w) & (a-(x+w) <= 2*w) & (d >= 2*h)
                  & (b <= y+h/2) & (y+h/2 <= b+d) & (.15*d <= w) & (w <= 1.5*d))
        if not np.any(nearby):
            continue
        x0, y0 = max(0, x-1), max(0, y-1)
        x1, y1 = min(gray.shape[1], x+w+1), min(gray.shape[0], y+h+1)
        output[y0:y1, x0:x1] = gray[y0:y1, x0:x1]
    return output


def recognize(content, lang, psm, kind, deadline=None, preprocessing='standard'):
    if preprocessing not in ('standard', 'original_gray'):
        raise ValueError('invalid_preprocessing')
    deadline = min(deadline, time.monotonic() + OCR_EXECUTION_TIMEOUT) if deadline is not None else time.monotonic() + OCR_EXECUTION_TIMEOUT
    img = read_image(content)
    if img is None:
        return JSONResponse(status_code=400, content={"error": "invalid image"})

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 原图回退不能再次经过可能损伤细字的模糊与二值化。正常主路径保持不变。
    if preprocessing == 'original_gray':
        thr = gray
    else:
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        thr = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        thr = preserve_thin_symbols(gray, thr, deadline)

    config = f"--oem 1 --psm {psm}"
    try:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise OCRExecutionTimeout()
        if kind == 'text':
            return {'text': pytesseract.image_to_string(thr, lang=lang, config=config, timeout=remaining),
                    'numeric_verification_status': 'not_performed'}
        data = pytesseract.image_to_data(thr, lang=lang, config=config,
                    output_type=pytesseract.Output.DICT, timeout=remaining)
        data['numeric_verification'] = verify_numeric_regions(gray, data, lang, deadline)
        data['execution_budget_enforced'] = True
        data['preprocessing'] = preprocessing
        return {'data': data}
    except RuntimeError as exc:
        if 'Tesseract process timeout' in str(exc):
            # pytesseract超时先终止并回收子进程，然后才抛出该异常。
            raise OCRExecutionTimeout() from None
        raise


async def run_request(file, lang, psm, kind, execution_budget_seconds=None, preprocessing='standard'):
    try:
        if preprocessing not in ('standard', 'original_gray'):
            return JSONResponse(status_code=400, content={'error': 'invalid_preprocessing'})
        deadline = None
        if execution_budget_seconds is not None:
            try:
                budget = float(execution_budget_seconds)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={'error': 'invalid_execution_budget'})
            if not math.isfinite(budget) or budget <= 0:
                return JSONResponse(status_code=400, content={'error': 'invalid_execution_budget'})
            # 显式剩余预算包含排队；不因再次进入工作线程重置执行期限。
            deadline = time.monotonic() + min(budget, OCR_EXECUTION_TIMEOUT)
        content = await file.read()
        return await executor.run(recognize, content, lang, psm, kind, deadline, preprocessing, admission_deadline=deadline)
    except OCRCapacityError:
        return JSONResponse(status_code=503, content={'error': 'ocr_busy'}, headers={'Retry-After': '2'})
    except OCRExecutionTimeout:
        return JSONResponse(status_code=504, content={'error': 'ocr_timeout'})
    except Exception:
        return JSONResponse(status_code=500, content={'error': 'ocr_processing_failed'})


@app.post("/ocr/text")
async def ocr_text(file: UploadFile = File(...), lang: str = Form("chi_sim+eng"), psm: int = Form(6)):
    return await run_request(file, lang, psm, 'text')


@app.post("/ocr/data")
async def ocr_data(
    file: UploadFile = File(...),
    lang: str = Form("chi_sim+eng"),
    psm: int = Form(6),
    execution_budget_seconds: str = Form(None),
    preprocessing: str = Form('standard')
):
    return await run_request(file, lang, psm, 'data', execution_budget_seconds, preprocessing)
