"""历史导入入口；公共 PDF 能力不依赖 CTD 标题识别。"""
from pathlib import Path
from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages


def parse_submission_pdf_to_payload(file_path, title=None, embed_images=False):
    pages = extract_pdf_pages(file_path)
    section_title = title or '全文'
    section = {'section_id': 'document.full_text', 'section_code': 'document.full_text',
               'section_name': section_title, 'title_path': [section_title], 'parent_section_id': '',
               'content': '\n\n'.join(p['text'] for p in pages),
               'raw_pages': [p['page'] for p in pages],
               'page_start': pages[0]['page'] if pages else None,
               'page_end': pages[-1]['page'] if pages else None,
               'tables': [t for p in pages for t in p['tables']], 'source_pages': pages}
    section['char_count'] = len(section['content'])
    section['content_preview'] = section['content'][:320]
    return {'title': section_title, 'source_pdf': str(Path(file_path).resolve()),
            'structure_type': 'generic_pdf_page_payload', 'sections': [section], 'review_units': pages,
            'pages': pages, 'parse_diagnostics': pages[0]['parse_diagnostics'] if pages else {'status': 'failed'},
            'statistics': {'section_total': 1, 'review_unit_total': len(pages)}}
