import unittest
from copy import deepcopy
from agent.agent_backend.services.filing_parse_resolution import apply_resolutions, manual_table


class ResolutionTests(unittest.TestCase):
    def test_full_mixed_region_requires_explicit_table_and_complete_outside_sources(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        chunks=[{'page':1,'page_bbox':[0,0,100,80],'status':'partial','text':'old','raw_text':'old',
            'words':[{'text':'old','bbox':[1,1,8,8],'source':'ocr'},{'text':'outside','bbox':[1,30,40,40],'source':'ocr'}],
            'lines':[],'tables':[{'id':'t','bbox_pdf':[0,0,60,20],'markdown':'|old|'}],
            'errors':[{'code':'ocr_coverage','bbox_pdf':[0,0,60,40]},
                      {'code':'field_region_crossing','bbox_pdf':[0,0,60,40],'candidate_items':[6,7]}]}]
        issues=source_issues({'content_status':'partial'},'application_form',chunks)
        parts=[{'table_index':0,'target_item_no':6,'reviewed_text':'合成甲片'},
               {'source_kind':'outside_text','target_item_no':7,'reviewed_text':'药品英文名称：Synthetic'}]
        item={'issue_key':issues[0]['issue_key'],'action':'correct_table','reason':'整区域逐字逐格核对',
            'table':{'row_count':1,'column_count':2,'cells':[{'row':0,'column':0,'text':'药品通用名称'},
                {'row':0,'column':1,'text':'合成甲片'}]},'outside_text':'药品英文名称：Synthetic','outside_text_verified':True,
            'related_issues':[{'issue_key':issues[1]['issue_key'],'reason':'明确表内及表外来源',
                'reviewed_text':'合成甲片\n药品英文名称：Synthetic','field_text_verified':True,'field_assignments':parts}]}
        original=deepcopy(chunks)
        effective,resolved,saved=apply_resolutions(chunks,issues,[item])
        self.assertEqual(resolved,{i['issue_key'] for i in issues})
        self.assertEqual(apply_resolutions(chunks,issues,saved)[:2],(effective,resolved))
        fields={i['item_no']:i['raw_text'] for i in build_pdf_form_from_pages(effective)['items']}
        self.assertEqual(fields[6],'合成甲片');self.assertEqual(fields[7],'Synthetic')
        self.assertEqual(chunks,original)
        for changes in ({'source_kind':'unknown'},{'table_index':0},{'target_item_no':6}):
            bad=deepcopy(item);bad['related_issues'][0]['field_assignments'][1].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):apply_resolutions(chunks,issues,[bad])
        for changes in ({'outside_text_verified':False},{'outside_text':'药品英文名称：Synthetic\n其他未分配文字'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):apply_resolutions(chunks,issues,[{**item,**changes}])
        narrower=deepcopy(issues);narrower[1]['bbox_pdf']=[0,0,55,40]
        with self.assertRaises(ValueError):apply_resolutions(chunks,narrower,[item])
        expanded=deepcopy(item)
        expanded['related_issues'][0]['review_scope']='complete_repair_region'
        expanded_effective,expanded_resolved,expanded_saved=apply_resolutions(chunks,narrower,[expanded])
        self.assertEqual(expanded_effective,effective)
        self.assertEqual(expanded_resolved,resolved)
        self.assertEqual(apply_resolutions(chunks,narrower,expanded_saved)[:2],(effective,resolved))
        self.assertEqual(expanded_saved[0]['related_issues'][0]['review_scope'],'complete_repair_region')
        # 扩大核对范围不能只摘录一个字段的局部值，也不能遗漏同表另一个字段。
        fragment=deepcopy(expanded)
        fragment['related_issues'][0]['reviewed_text']='甲片\n药品英文名称：Synthetic'
        fragment['related_issues'][0]['field_assignments'][0]['reviewed_text']='甲片'
        with self.assertRaises(ValueError):apply_resolutions(chunks,narrower,[fragment])
        missing=deepcopy(expanded);missing['table']['row_count']=2
        missing['table']['cells'] += [{'row':1,'column':0,'text':'规格'},{'row':1,'column':1,'text':'3 mg'}]
        with self.assertRaises(ValueError):apply_resolutions(chunks,narrower,[missing])
        for bad_scope in ('all',True,{},''):
            bad=deepcopy(expanded);bad['related_issues'][0]['review_scope']=bad_scope
            with self.subTest(scope=bad_scope),self.assertRaises(ValueError):apply_resolutions(chunks,narrower,[bad])
        self.assertEqual(chunks,original)

    def test_cross_table_related_fields_require_explicit_sources_and_all_tables(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        chunks = [{'page':1, 'page_bbox':[0,0,100,60], 'status':'partial',
            'words':[{'text':'旧甲', 'bbox':[1,1,8,8], 'source':'ocr'}, {'text':'旧乙','bbox':[41,1,48,8], 'source':'ocr'}],
            'lines':[], 'tables':[{'id':'left', 'bbox_pdf':[0,0,30,20], 'markdown':'|old1|'},
                                {'id':'right','bbox_pdf':[40,0,70,20], 'markdown':'|old2|'}],
            'errors':[{'code':'ocr_coverage','bbox_pdf':[0,0,70,20]},
                      {'code':'field_region_crossing','bbox_pdf':[0,0,70,20], 'candidate_items':[6,12]}]}]
        issues = source_issues({'content_status':'partial'}, 'application_form', chunks)
        def table(label, value):
            return {'row_count':1,'column_count':2,'cells':[{'row':0,'column':0,'text':label}, {'row':0,'column':1,'text':value}]}
        parts = [{'table_index':0,'target_item_no':6,'reviewed_text':'合成甲片'},
                 {'table_index':1,'target_item_no':12,'reviewed_text':'3 mg'}]
        entry = {'issue_key':issues[1]['issue_key'],'reason':'对照两个独立原表逐段核对',
                 'reviewed_text':'合成甲片\n3 mg','field_text_verified':True,'field_assignments':parts}
        item = {'issue_key':issues[0]['issue_key'],'action':'correct_table','reason':'完整核对两表及空白间隔',
                'outside_text':'','outside_text_verified':True,
                'tables':[{'table_index':0,'verified':True,'table':table('药品通用名称','合成甲片')},
                          {'table_index':1,'verified':True,'table':table('规格','3 mg')}], 'related_issues':[entry]}
        original = deepcopy(chunks)
        effective, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual(resolved, {i['issue_key'] for i in issues})
        self.assertEqual(len(effective[0]['tables']),2)
        self.assertEqual([t['bbox_pdf'] for t in effective[0]['tables']],[[0,0,30,20],[40,0,70,20]])
        self.assertEqual(apply_resolutions(chunks, issues, saved)[:2],(effective,resolved))
        parsed=build_pdf_form_from_pages(effective)
        self.assertEqual(next(i for i in parsed['items'] if i['item_no']==6)['raw_text'],'合成甲片')
        self.assertEqual(next(i for i in parsed['items'] if i['item_no']==12)['raw_text'],'3 mg')
        self.assertEqual(chunks, original)
        for changed in ({'table_index':None},{'table_index':0},{'table_index':True},{'target_item_no':6}):
            bad=deepcopy(item);bad['related_issues'][0]['field_assignments'][1].update(changed)
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [bad])
        for changed in ({'outside_text':'还有表外文字'}, {'outside_text_verified':False}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item,**changed}])
        omitted = deepcopy(item)
        omitted['related_issues'][0].update(reviewed_text='合成\n甲片', field_assignments=[
            {'table_index':0,'target_item_no':6,'reviewed_text':'合成'},
            {'table_index':0,'target_item_no':6,'reviewed_text':'甲片'}])
        with self.assertRaisesRegex(ValueError, '遗漏涉及的原表'):
            apply_resolutions(chunks, issues, [omitted])
        narrow=deepcopy(issues);narrow[1]['bbox_pdf']=[10,0,60,20]
        with self.assertRaises(ValueError):
            apply_resolutions(chunks,narrow,[item])

    def test_related_multifield_region_requires_complete_ordered_assignments(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        chunks = [{'page': 1, 'page_bbox': [0, 0, 300, 400], 'status': 'partial', 'tables': [],
            'words': [{'text': '旧识别', 'bbox': [10, 10, 100, 50], 'source': 'ocr'}], 'lines': [],
            'errors': [{'code': 'ocr_coverage', 'bbox_pdf': [0, 0, 300, 400]},
                {'code': 'field_region_crossing', 'bbox_pdf': [10, 10, 100, 50], 'candidate_items': [6, 12]}]}]
        original = deepcopy(chunks)
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        parts = [{'target_item_no': 6, 'reviewed_text': '6. 药品通用名称：合成甲片'},
                 {'target_item_no': 12, 'reviewed_text': '12. 规格：3 mg'}]
        complete = '\n'.join(part['reviewed_text'] for part in parts)
        entry = {'issue_key': issues[1]['issue_key'], 'reason': '本区域两字段逐段核对',
                 'reviewed_text': complete, 'field_assignments': parts, 'field_text_verified': True}
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_text', 'reason': '完整核对原页',
                'text': complete, 'related_issues': [entry]}
        effective, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual(resolved, {i['issue_key'] for i in issues})
        self.assertEqual(saved[0]['related_issues'], [entry])
        self.assertEqual(apply_resolutions(chunks, issues, saved)[:2], (effective, resolved))
        parsed = build_pdf_form_from_pages(effective)
        self.assertEqual(next(i for i in parsed['items'] if i['item_no'] == 6)['raw_text'], '合成甲片')
        self.assertEqual(next(i for i in parsed['items'] if i['item_no'] == 12)['raw_text'], '3 mg')
        self.assertEqual(chunks, original)
        bad_entries = [{**entry, 'field_assignments': parts[:1]},
            {**entry, 'field_assignments': list(reversed(parts))},
            {**entry, 'reviewed_text': '\n'.join(p['reviewed_text'] for p in reversed(parts)), 'field_assignments': list(reversed(parts))},
            {**entry, 'field_assignments': [parts[0], {**parts[1], 'target_item_no': 6}]},
            {**entry, 'field_assignments': [parts[0], {**parts[1], 'reviewed_text': '12. 规格：3mg'}]},
            {**entry, 'reviewed_text': '\n'.join([parts[0]['reviewed_text']] * 2), 'field_assignments': [parts[0], parts[0]]},
            {**entry, 'field_text_verified': False}, {**entry, 'target_item_no': 6}]
        for bad in bad_entries:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, 'related_issues': [bad]}])
        self.assertEqual(chunks, original)

    def test_conflicting_quality_evidence_requires_explicit_text_revision(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        for kind in ('application_form', 'submission', 'reference'):
            chunks = deepcopy(self.chunks)
            chunks[0]['errors'] = [{'code':'ocr_quality', 'bbox_pdf':[0,0,10,10],
                'recovery_conflicts':[{'reason_code':'recovery_context_ambiguous',
                    'has_conflicting_candidates':True, 'candidates':[{'text':'different','bbox':[1,1,4,4]}]}]}]
            original = deepcopy(chunks)
            issues = source_issues({'content_status':'partial'}, kind, chunks)
            with self.subTest(kind=kind):
                self.assertFalse(issues[0]['confirm_allowed'])
                with self.assertRaises(ValueError):
                    apply_resolutions(chunks, issues, [{'issue_key':issues[0]['issue_key'], 'action':'confirm', 'reason':'已核对'}])
                effective, resolved, saved = apply_resolutions(chunks, issues, [
                    {'issue_key':issues[0]['issue_key'], 'action':'correct_text', 'text':'different', 'reason':'逐字核对原区域完整文字'}])
                self.assertIn(issues[0]['issue_key'], resolved)
                self.assertIn('different', effective[0]['text'])
                self.assertEqual(apply_resolutions(chunks, issues, saved)[:2], (effective, resolved))
                self.assertEqual(chunks, original)

    def test_related_multifield_inside_one_table_preserves_label_value_pairs(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        chunks = deepcopy(self.chunks)
        for word in chunks[0]['words']:
            word['source'] = 'ocr'
        chunks[0]['tables'] = [{'id': 't', 'page': 1, 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        chunks[0]['errors'] = [{'code': 'table_structure_unresolved', 'bbox_pdf': [0, 0, 10, 10]},
            {'code': 'field_region_crossing', 'bbox_pdf': [1, 1, 9, 9], 'candidate_items': [6, 12]}]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '逐格核对',
            'table': {'row_count': 2, 'column_count': 2, 'cells': [
                {'row': 0, 'column': 0, 'text': '药品通用名称'}, {'row': 0, 'column': 1, 'text': '合成甲片'},
                {'row': 1, 'column': 0, 'text': '规格'}, {'row': 1, 'column': 1, 'text': '3 mg'}]},
            'related_issues': [{'issue_key': issues[1]['issue_key'], 'reason': '同区域药名及规格分别核对',
                'reviewed_text': '合成甲片\n3 mg', 'field_text_verified': True, 'field_assignments': [
                    {'target_item_no': 6, 'reviewed_text': '合成甲片'}, {'target_item_no': 12, 'reviewed_text': '3 mg'}]}]}
        original = deepcopy(chunks)
        effective, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual(resolved, {issue['issue_key'] for issue in issues})
        self.assertEqual(apply_resolutions(chunks, issues, saved)[:2], (effective, resolved))
        parsed = build_pdf_form_from_pages(effective)
        self.assertEqual(next(i for i in parsed['items'] if i['item_no'] == 6)['raw_text'], '合成甲片')
        self.assertEqual(next(i for i in parsed['items'] if i['item_no'] == 12)['raw_text'], '3 mg')
        self.assertEqual(chunks, original)

    def test_table_field_confirmation_uses_unique_repaired_label_value(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        chunks = deepcopy(self.chunks)
        chunks[0]['page_bbox'] = [0, 0, 30, 30]
        for word in chunks[0]['words']:
            word['source'] = 'ocr'
        chunks[0]['tables'] = [{'id': 't', 'page': 1, 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        chunks[0]['errors'] = [{'code': 'table_structure_unresolved', 'bbox_pdf': [0, 0, 10, 10]},
            {'code': 'field_region_crossing', 'bbox_pdf': [1, 1, 4, 4], 'candidate_items': [6, 12]}]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        entry = {'issue_key': issues[1]['issue_key'], 'reason': '对照完整原表标签和值',
                 'target_item_no': 6, 'reviewed_text': '合成甲片', 'field_text_verified': True}
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '逐格完整核对',
            'table': {'row_count': 1, 'column_count': 2, 'cells': [
                {'row': 0, 'column': 0, 'text': '药品通用名称'}, {'row': 0, 'column': 1, 'text': '合成甲片'}]},
            'related_issues': [entry]}
        original = deepcopy(chunks)
        effective, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertIn(issues[1]['issue_key'], resolved)
        self.assertEqual(apply_resolutions(chunks, issues, saved)[:2], (effective, resolved))
        parsed = build_pdf_form_from_pages(effective)
        self.assertEqual(next(i for i in parsed['items'] if i['item_no'] == 6)['raw_text'], '合成甲片')
        self.assertEqual(chunks, original)
        for changed in ({'target_item_no': 12}, {'reviewed_text': 'old'}, {'field_text_verified': False}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, 'related_issues': [{**entry, **changed}]}])
        bad = deepcopy(item)
        bad['table']['cells'][0]['text'] = '不明标签'
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, issues, [bad])
        duplicate = deepcopy(item)
        duplicate['table']['row_count'] = 2
        duplicate['table']['cells'].extend([
            {'row': 1, 'column': 0, 'text': '药品通用名称'}, {'row': 1, 'column': 1, 'text': '另一名称'}])
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, issues, [duplicate])
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, [issues[0], {**issues[1], 'bbox_pdf': [1, 1, 14, 4]}], [item])
        self.assertNotIn(issues[1]['issue_key'], apply_resolutions(chunks, issues, [{**item, 'related_issues': []}])[1])

    def test_mixed_table_outside_field_assignment_preserves_table(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't', 'page': 1, 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        chunks[0]['errors'] = [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 30, 10]},
            {'code': 'field_region_crossing', 'bbox_pdf': [21, 1, 28, 4], 'candidate_items': [6, 12]}]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        entry = {'issue_key': issues[1]['issue_key'], 'reason': '对照表外药名完整文字',
                 'target_item_no': 6, 'reviewed_text': '合成甲片', 'field_text_verified': True}
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '逐格核对及表外核对',
            'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': '表内数据'}]},
            'outside_text': '6. 药品通用名称：合成甲片', 'outside_text_verified': True, 'related_issues': [entry]}
        original = deepcopy(chunks)
        effective, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertIn(issues[1]['issue_key'], resolved)
        self.assertEqual(effective[0]['tables'][0]['rows'], [['表内数据']])
        self.assertEqual(apply_resolutions(chunks, issues, saved)[:2], (effective, resolved))
        self.assertEqual(chunks, original)
        for changed in ({'target_item_no': 12}, {'reviewed_text': '表内数据'}, {'field_text_verified': False}, {'source_kind': 'table'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, 'related_issues': [{**entry, **changed}]}])
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, [issues[0], {**issues[1], 'bbox_pdf': [1, 1, 4, 4]}], [item])

    def test_full_text_can_resolve_field_issue_only_with_explicit_matching_assignment(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        chunks = [{'page': 4, 'page_bbox': [0, 0, 300, 400], 'status': 'partial', 'tables': [],
                   'words': [{'text': '旧识别', 'bbox': [10, 10, 100, 20], 'source': 'ocr'}],
                   'lines': [], 'errors': [{'code': 'ocr_coverage', 'bbox_pdf': [0, 0, 300, 400]},
                       {'code': 'field_region_crossing', 'bbox_pdf': [10, 10, 100, 20], 'candidate_items': [31, 32]}]}]
        original = deepcopy(chunks)
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        entry = {'issue_key': issues[1]['issue_key'], 'reason': '原页该区域属于生产企业，已完整补录',
                 'target_item_no': 31, 'reviewed_text': '联系人：合成联系人', 'field_text_verified': True}
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_text', 'reason': '完整核对原页',
                'text': '生产企业：\n联系人：合成联系人\n31. 委托研究机构：\n中文名称：合成研究机构',
                'related_issues': [entry]}
        effective, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual(resolved, {i['issue_key'] for i in issues})
        self.assertEqual(saved[0]['related_issues'], [entry])
        self.assertEqual(apply_resolutions(chunks, issues, saved)[:2], (effective, resolved))
        self.assertEqual(chunks, original)
        self.assertEqual(apply_resolutions(chunks, issues, [{**item, 'related_issues': []}])[1], {issues[0]['issue_key']})
        for changed in ({'target_item_no': 32}, {'target_item_no': True}, {'target_item_no': 999},
                        {'reviewed_text': ''}, {'reviewed_text': '并不存在的文字'}, {'field_text_verified': False}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, 'related_issues': [{**entry, **changed}]}])
        for changed in ({'chunk_index': 1}, {'bbox_pdf': [299, 399, 301, 401]}, {'source_kind': 'submission'}):
            altered = [issues[0], {**issues[1], **changed}]
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                apply_resolutions(chunks, altered, [item])
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, issues, [{**item, 'text': item['text'] + '\n联系人：合成联系人'}])

    def setUp(self):
        self.chunks = [{'page': 1, 'status': 'partial', 'text': 'old outside', 'raw_text': 'old outside',
            'words': [{'text': 'old', 'bbox': [1, 1, 4, 4]}, {'text': 'outside', 'bbox': [21, 1, 28, 4]}],
            'tables': [], 'errors': [{'code': 'ocr_coverage'}]}]
        self.issues = [{'issue_key': 'e', 'chunk_index': 0, 'code': 'ocr_coverage', 'bbox_pdf': [0, 0, 10, 10]}]

    def test_correction_preserves_original_and_updates_effective_content(self):
        original = deepcopy(self.chunks)
        updated, resolved, _ = apply_resolutions(self.chunks, self.issues, [
            {'issue_key': 'e', 'action': 'correct_text', 'text': 'correct', 'reason': '原页逐字核对'}])
        self.assertEqual(self.chunks, original)
        self.assertIn('correct', updated[0]['text'])
        self.assertIn('outside', updated[0]['text'])
        self.assertNotIn('old', updated[0]['raw_text'])
        self.assertEqual(updated[0]['status'], 'partial')
        self.assertEqual(updated[0]['errors'], original[0]['errors'])
        self.assertEqual(resolved, {'e'})

    def test_missing_text_cannot_be_dismissed_by_confirmation(self):
        with self.assertRaises(ValueError):
            apply_resolutions(self.chunks, self.issues, [{'issue_key': 'e', 'action': 'confirm', 'reason': 'checked'}])

    def test_complete_text_revision_can_explicitly_resolve_same_region_quality(self):
        quality = {**self.issues[0], 'issue_key': 'quality', 'code': 'ocr_quality'}
        issues = self.issues + [quality]
        item = {'issue_key': 'e', 'action': 'correct_text', 'text': '完整修订正文', 'reason': '原页逐字核对',
                'related_issues': [{'issue_key': 'quality', 'reason': '低置信度原文已全部补录修正'}]}
        output, resolved, saved = apply_resolutions(self.chunks, issues, [item])
        self.assertEqual(resolved, {'e', 'quality'})
        self.assertEqual(saved[0]['related_issues'], item['related_issues'])
        self.assertEqual(apply_resolutions(self.chunks, issues, saved)[1], resolved)
        self.assertIn('outside', output[0]['text'])
        self.assertEqual(apply_resolutions(self.chunks, issues, [{**item, 'related_issues': []}])[1], {'e'})
        for altered in ({'bbox_pdf': [21, 1, 28, 4]}, {'chunk_index': 1}, {'code': 'numeric_uncertain'},
                        {'code': 'field_region_crossing'}, {'code': 'ocr_timeout'}):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                apply_resolutions(self.chunks, self.issues + [{**quality, **altered}], [item])
        with self.assertRaises(ValueError):
            apply_resolutions(self.chunks, issues, [{**item, 'related_issues': [{'issue_key': 'quality', 'reason': ''}]}])

    def test_mixed_table_and_text_numeric_review_requires_explicit_outside_review(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't', 'page': 1, 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        chunks[0]['errors'] = [
            {'code': 'ocr_quality', 'bbox_pdf': [0, 0, 30, 10]},
            {'code': 'numeric_uncertain', 'bbox_pdf': [0, 0, 30, 10], 'numeric_verification': [
                {'bbox_pdf': [1, 1, 4, 4]}, {'bbox_pdf': [21, 1, 28, 4]}]}]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_table',
                'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': '0.2mg'}]},
                'outside_text': '备注：3mg', 'outside_text_verified': True, 'reason': '逐格及表外逐字核对'}
        self.assertNotIn(issues[1]['issue_key'], apply_resolutions(chunks, issues, [item])[1])
        checked = {**item, 'numeric_text_verified': True}
        result, resolved, saved = apply_resolutions(chunks, issues, [checked])
        self.assertIn(issues[1]['issue_key'], resolved)
        self.assertIn('备注：3mg', result[0]['text'])
        self.assertEqual(result[0]['tables'][0]['rows'], [['0.2mg']])
        self.assertTrue(saved[0]['numeric_text_verified'])
        self.assertEqual(apply_resolutions(chunks, issues, saved)[1], resolved)
        for check in ({}, {'bbox_pdf': [35, 1, 40, 4]}):
            extra = deepcopy(chunks)
            extra[0]['errors'][1]['numeric_verification'].append(check)
            self.assertNotIn(issues[1]['issue_key'], apply_resolutions(extra, issues, [checked])[1])
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, issues, [{**checked, 'outside_text_verified': False}])

    def test_standalone_numeric_text_requires_complete_located_evidence_and_explicit_review(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        chunks = deepcopy(self.chunks)
        chunks[0]['errors'] = [{'code': 'numeric_uncertain', 'bbox_pdf': [0, 0, 10, 10],
            'numeric_verification': [{'bbox_pdf': [1, 1, 4, 4], 'status': 'uncertain'}]}]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        self.assertTrue(issues[0].get('numeric_text_review_allowed'))
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_text', 'text': '0.2mg',
                'numeric_text_verified': True, 'reason': '完整区域逐字对照原页'}
        updated, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertIn(issues[0]['issue_key'], resolved)
        self.assertIn('0.2mg', updated[0]['text'])
        self.assertIn('outside', updated[0]['text'])
        for change in ({'numeric_text_verified': False}, {'numeric_text_verified': 'true'}, {'action': 'confirm'},
                       {'action': 'irrelevant_region'}, {'action': 'correct_table'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, **change}])
        for bad in ('missing', 'outside', 'table', 'crossing_word'):
            other = deepcopy(chunks)
            if bad == 'missing':
                other[0]['errors'][0]['numeric_verification'] = [{}]
            elif bad == 'outside':
                other[0]['errors'][0]['numeric_verification'][0]['bbox_pdf'] = [8, 1, 18, 4]
            elif bad == 'table':
                other[0]['tables'] = [{'bbox_pdf': [1, 1, 8, 8]}]
            else:
                other[0]['words'][0]['bbox'] = [1, 1, 12, 4]
            current = source_issues({'content_status': 'partial'}, 'application_form', other)
            self.assertFalse(current[0].get('numeric_text_review_allowed'))
            # 客户端伪造可编辑标记也不能绕过服务端原始证据验证。
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                apply_resolutions(other, [{**current[0], 'numeric_text_review_allowed': True}], [item])

    def test_explicit_numeric_text_review_only_resolves_fully_covered_checks(self):
        chunks = deepcopy(self.chunks)
        chunks[0]['errors'].append({'code': 'numeric_uncertain', 'numeric_verification': [
            {'status': 'uncertain', 'bbox_pdf': [1, 1, 4, 4]}]})
        numeric = {'issue_key': 'n', 'chunk_index': 0, 'error_index': 1,
                   'code': 'numeric_uncertain', 'bbox_pdf': [0, 0, 30, 10]}
        issues = self.issues + [numeric]
        item = {'issue_key': 'e', 'action': 'correct_text', 'text': '≤0.20 mg',
                'reason': '原页逐字核对数值、单位与符号', 'numeric_text_verified': True}
        original = deepcopy(chunks)
        updated, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertIn('n', resolved)
        self.assertTrue(saved[0]['numeric_text_verified'])
        self.assertEqual(chunks, original)
        self.assertIn('≤0.20 mg', updated[0]['text'])
        self.assertIn('outside', updated[0]['text'])
        self.assertEqual(apply_resolutions(chunks, issues, saved)[1], resolved)
        self.assertNotIn('n', apply_resolutions(chunks, issues, [{**item, 'numeric_text_verified': False}])[1])
        for check in ({'bbox_pdf': [21, 1, 28, 4]}, {'bbox_pdf': [3, 1, 15, 4]}, {},
                      {'bbox_pdf': [1, 1, float('nan'), 4]}):
            with self.subTest(check=check):
                modified = deepcopy(chunks)
                modified[0]['errors'][1]['numeric_verification'].append(check)
                self.assertNotIn('n', apply_resolutions(modified, issues, [item])[1])
        self.assertNotIn('n', apply_resolutions(chunks, self.issues + [{**numeric, 'chunk_index': 1}], [item])[1])

    def test_quality_confirmation_requires_text_in_the_actual_issue_region(self):
        issue = {**self.issues[0], 'code':'ocr_quality', 'bbox_pdf':[40,40,60,60]}
        with self.assertRaisesRegex(ValueError, '区域'):
            apply_resolutions(self.chunks, [issue],
                [{'issue_key':'e','action':'confirm','reason':'已查看其他正文'}])

    def test_quality_confirmation_of_positioned_text_keeps_original(self):
        issue = {**self.issues[0], 'code':'ocr_quality'}
        before = deepcopy(self.chunks)
        output, resolved, _ = apply_resolutions(self.chunks, [issue],
            [{'issue_key':'e','action':'confirm','reason':'对照本区域确认old确为原文'}])
        self.assertEqual(resolved, {'e'})
        self.assertEqual(output,before)
        self.assertEqual(self.chunks,before)

    def test_field_assignment_requires_explicit_valid_target_and_application_source(self):
        issue = {**self.issues[0], 'source_kind': 'application_form', 'code': 'field_region_crossing',
                 'field_options': [{'item_no': 6, 'title': '药品通用名称'}]}
        item = {'issue_key': 'e', 'action': 'correct_text', 'text': '核对文字', 'reason': '核对原页', 'target_item_no': 6}
        for target in (None, True, '6', 12, -1):
            with self.subTest(target=target), self.assertRaises(ValueError):
                apply_resolutions(self.chunks, [issue], [{**item, 'target_item_no': target}])
        for action in ('confirm', 'irrelevant_region', 'correct_table'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                apply_resolutions(self.chunks, [issue], [{**item, 'action': action}])
        with self.assertRaises(ValueError):
            apply_resolutions(self.chunks, [{**issue, 'source_kind': 'submission'}], [item])
        updated, resolved, saved = apply_resolutions(self.chunks, [issue], [item])
        self.assertEqual(saved[0]['target_item_no'], 6)
        self.assertEqual(next(w for w in updated[0]['words'] if w['text'] == '核对文字')['manual_target_item_no'], 6)
        self.assertEqual(resolved, {'e'})

    def test_field_summary_deduplication_keeps_distinct_candidate_evidence(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        error = {'code': 'field_region_crossing', 'stage': 'field_region', 'reason': '需核对',
                 'text': '合成文字', 'bbox_pdf': [0, 0, 10, 10], 'bbox_px': [0, 0, 25, 25],
                 'dpi': 180, 'needs_review': True, 'candidate_items': [6, 12]}
        summary = {k: v for k, v in error.items() if k not in ('bbox_px', 'dpi', 'needs_review', 'reason')}
        summary.update(page=1, message='需核对')
        chunks = [{**self.chunks[0], 'errors': [error]}]
        source = {'content_status': 'partial', 'parse_diagnostics': {'errors': [summary]}}
        self.assertEqual(len(source_issues(source, 'application_form', chunks)), 1)
        source['parse_diagnostics']['errors'].append({**summary, 'candidate_items': [6, 7]})
        self.assertEqual(len(source_issues(source, 'application_form', chunks)), 2)

    def test_multiple_field_assignments_preserve_complete_region_and_reject_gaps(self):
        issue = {**self.issues[0], 'source_kind': 'application_form', 'code': 'field_region_crossing',
                 'field_options': [{'item_no': 6}, {'item_no': 12}]}
        item = {'issue_key': 'e', 'action': 'correct_text', 'text': '名称甲\n3mg', 'reason': '逐段核对',
                'all_text_verified': True, 'field_assignments': [
                    {'target_item_no': 6, 'text': '名称甲'}, {'target_item_no': 12, 'text': '3mg'}]}
        output, resolved, saved = apply_resolutions(self.chunks, [issue], [item])
        self.assertEqual([(w['manual_target_item_no'], w['text']) for w in output[0]['words']
                          if 'manual_target_item_no' in w], [(6, '名称甲'), (12, '3mg')])
        self.assertEqual(resolved, {'e'})
        self.assertEqual(saved[0]['field_assignments'], item['field_assignments'])
        for invalid in ({**item, 'all_text_verified': False},
                        {**item, 'field_assignments': item['field_assignments'][:1]},
                        {**item, 'field_assignments': [{'target_item_no': 7, 'text': item['text']}]},
                        {**item, 'field_assignments': []}):
            with self.assertRaises(ValueError):
                apply_resolutions(self.chunks, [issue], [invalid])

    def test_field_split_cannot_erase_internal_numeric_separators(self):
        issue = {**self.issues[0], 'source_kind': 'application_form', 'code': 'field_region_crossing',
                 'field_options': [{'item_no': 12}]}
        item = {'issue_key': 'e', 'action': 'correct_text', 'text': '1 2 mg', 'reason': '核对原页',
                'all_text_verified': True, 'field_assignments': [{'target_item_no': 12, 'text': '12 mg'}]}
        with self.assertRaises(ValueError):
            apply_resolutions(self.chunks, [issue], [item])

    def test_manual_label_value_table_reaches_application_fields(self):
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
        chunks = deepcopy(self.chunks)
        for word in chunks[0]['words']:
            word['source'] = 'native'
        chunks[0]['tables'] = [{'id': 't', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        table = {'row_count': 2, 'column_count': 2, 'cells': [
            {'row': 0, 'column': 0, 'text': '药品通用名称'}, {'row': 0, 'column': 1, 'text': '合成甲片'},
            {'row': 1, 'column': 0, 'text': '规格'}, {'row': 1, 'column': 1, 'text': '3mg'}]}
        effective, _, _ = apply_resolutions(chunks, [{**self.issues[0], 'code': 'table_structure_unresolved'}],
            [{'issue_key': 'e', 'action': 'correct_table', 'table': table, 'reason': '逐格核对合成原表'}])
        form = FilingChangeFormParserService().form_from_pdf_result(build_pdf_form_from_pages(effective))['form_json']
        self.assertEqual(form['item_6_generic_name']['value'], '合成甲片')
        self.assertEqual(form['item_12_specification']['value'], '3mg')

    def test_text_or_table_cannot_be_ignored_as_decoration(self):
        with self.assertRaises(ValueError):
            apply_resolutions(self.chunks, self.issues, [{'issue_key': 'e', 'action': 'irrelevant_region',
                                                        'reason': 'checked', 'non_text_kind': 'decoration'}])

    def test_manual_merged_label_value_table_reaches_application_fields(self):
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
        layouts = [
            {'row_count': 3, 'column_count': 2, 'cells': [
                {'row': 0, 'column': 0, 'rowspan': 2, 'text': '药品通用名称'},
                {'row': 0, 'column': 1, 'rowspan': 2, 'text': '合成甲片'},
                {'row': 2, 'column': 0, 'text': '规格'},
                {'row': 2, 'column': 1, 'text': '3mg'}]},
            {'row_count': 2, 'column_count': 3, 'cells': [
                {'row': 0, 'column': 0, 'text': '药品通用名称'},
                {'row': 0, 'column': 1, 'colspan': 2, 'text': '合成甲片'},
                {'row': 1, 'column': 0, 'colspan': 2, 'text': '规格'},
                {'row': 1, 'column': 2, 'text': '3mg'}]},
            {'row_count': 1, 'column_count': 4, 'cells': [
                {'row': 0, 'column': 0, 'text': '药品通用名称'},
                {'row': 0, 'column': 1, 'text': '合成甲片'},
                {'row': 0, 'column': 2, 'text': '规格'},
                {'row': 0, 'column': 3, 'text': '3mg'}]},
        ]
        for layout in layouts:
            with self.subTest(layout=layout):
                chunks = deepcopy(self.chunks)
                for word in chunks[0]['words']:
                    word['source'] = 'native'
                chunks[0]['tables'] = [{'id': 't', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
                effective, _, _ = apply_resolutions(chunks,
                    [{**self.issues[0], 'code': 'table_structure_unresolved'}],
                    [{'issue_key': 'e', 'action': 'correct_table', 'table': layout, 'reason': '逐格核对'}])
                form = FilingChangeFormParserService().form_from_pdf_result(build_pdf_form_from_pages(effective))['form_json']
                self.assertEqual(form['item_6_generic_name']['value'], '合成甲片')
                self.assertEqual(form['item_12_specification']['value'], '3mg')

    def test_merged_table_requires_complete_nonoverlapping_topology(self):
        valid = {'row_count': 2, 'column_count': 2, 'cells': [
            {'row': 0, 'column': 0, 'colspan': 2, 'text': 'Header'},
            {'row': 1, 'column': 0, 'text': 'A'}, {'row': 1, 'column': 1, 'text': ''}]}
        table = manual_table(valid, [0, 0, 100, 100], 1, 'checked')
        self.assertEqual(table['cells'][0]['colspan'], 2)
        self.assertEqual(table['rows'], [['Header', ''], ['A', '']])
        for cells in (valid['cells'][:-1], valid['cells'] + [{'row': 0, 'column': 1, 'text': 'duplicate'}]):
            with self.assertRaises(ValueError):
                manual_table({**valid, 'cells': cells}, [0, 0, 100, 100], 1, 'checked')

    def test_ambiguous_manual_field_table_preserves_text_and_requires_assignment(self):
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        table = manual_table({'row_count': 2, 'column_count': 2, 'cells': [
            {'row': 0, 'column': 0, 'text': '药品通用名称'},
            {'row': 1, 'column': 0, 'text': '规格'},
            {'row': 0, 'column': 1, 'rowspan': 2, 'text': '合成甲片 3mg'}]}, [0, 0, 10, 10], 1, '核对结构')
        pages = [{'page': 1, 'status': 'partial', 'tables': [table], 'words': [],
                  'lines': [{'source': 'manual_revision', 'bbox': [0, 0, 10, 10], 'text': table['markdown']}],
                  'raw_text': table['markdown'], 'text': table['markdown'], 'errors': []}]
        original = deepcopy(pages)
        result = build_pdf_form_from_pages(pages)
        self.assertEqual(pages, original)
        self.assertIn('合成甲片 3mg', result['raw_text'])
        self.assertTrue(any(issue['code'] == 'field_region_unassigned'
                            and issue['candidate_items'] == [6, 12] for issue in result['unassigned_regions']))

    def test_table_correction_removes_stale_words_from_analysis_copy(self):
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't1', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        issues = [{**self.issues[0], 'code': 'table_structure_unresolved'}]
        corrected, _, _ = apply_resolutions(chunks, issues, [{'issue_key': 'e', 'action': 'correct_table',
            'reason': '核对原表', 'table': {'row_count': 1, 'column_count': 1,
            'cells': [{'row': 0, 'column': 0, 'text': 'new'}]}}])
        self.assertNotIn('old', corrected[0]['raw_text'])
        self.assertIn('new', corrected[0]['text'])
        self.assertIn('outside', corrected[0]['text'])

    def test_cutting_word_boundary_is_rejected(self):
        issue = {**self.issues[0], 'bbox_pdf': [2, 2, 10, 10]}
        with self.assertRaises(ValueError):
            apply_resolutions(self.chunks, [issue], [{'issue_key': 'e', 'action': 'correct_text', 'text': 'new', 'reason': 'checked'}])

    def test_unmatched_line_outside_correction_is_preserved(self):
        self.chunks[0]['lines'] = [{'text': 'outside EXTRA FOOTNOTE', 'bbox': [20, 0, 40, 5]}]
        updated, _, _ = apply_resolutions(self.chunks, self.issues, [
            {'issue_key': 'e', 'action': 'correct_text', 'text': 'correct', 'reason': 'checked'}])
        self.assertIn('outside EXTRA FOOTNOTE', updated[0]['text'])
        self.assertEqual(updated[0]['text'].count('outside'), 1)

    def test_partial_unmatched_line_cannot_be_silently_removed(self):
        self.chunks[0]['lines'] = [{'text': 'old UNKNOWN', 'bbox': [0, 0, 15, 5]}]
        with self.assertRaises(ValueError):
            apply_resolutions(self.chunks, self.issues, [
                {'issue_key': 'e', 'action': 'correct_text', 'text': 'correct', 'reason': 'checked'}])

    def test_located_assembly_issue_requires_actual_correction(self):
        issues = [{**self.issues[0], 'code': 'text_assembly_unresolved'}]
        updated, resolved, _ = apply_resolutions(self.chunks, issues, [
            {'issue_key': 'e', 'action': 'correct_text', 'text': 'verified text', 'reason': 'checked original'}])
        self.assertIn('verified text', updated[0]['text'])
        self.assertEqual(resolved, {'e'})
        for action in ('confirm', 'irrelevant_region'):
            with self.assertRaises(ValueError):
                apply_resolutions(self.chunks, issues, [
                    {'issue_key': 'e', 'action': action, 'reason': 'checked', 'non_text_kind': 'blank'}])

    def test_cross_table_assembly_keeps_separate_outside_text(self):
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't1', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        chunks[0]['lines'] = [{'text': 'old outside UNKNOWN', 'bbox': [0, 0, 30, 5]}]
        issues = [{**self.issues[0], 'code': 'text_assembly_unresolved', 'bbox_pdf': [0, 0, 30, 5]}]
        item = {'issue_key': 'e', 'action': 'correct_table', 'reason': '逐格及表外原文核对',
                'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': 'new'}]},
                'outside_text': 'outside UNKNOWN', 'outside_text_verified': True}
        updated, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual(updated[0]['tables'][0]['bbox_pdf'], [0, 0, 10, 10])
        self.assertIn('outside UNKNOWN', updated[0]['text'])
        self.assertEqual(updated[0]['text'].count('outside'), 1)
        self.assertNotIn('old', updated[0]['text'])
        self.assertEqual(chunks[0]['lines'][0]['text'], 'old outside UNKNOWN')
        self.assertEqual(resolved, {'e'})
        replay, _, _ = apply_resolutions(chunks, issues, saved)
        self.assertEqual(replay, updated)
        from agent.agent_backend.services.filing_parse_resolution import assembly_table_scope
        self.assertEqual(assembly_table_scope(chunks[0], issues[0]),
                         {'repair_bbox_pdf': [0, 0, 30, 10], 'repair_table_index': 0})
        forged = [{**issues[0], 'repair_bbox_pdf': [0, 0, 1000, 1000], 'repair_table_index': 9}]
        self.assertEqual(apply_resolutions(chunks, forged, [item])[0], updated)
        multiple = deepcopy(chunks)
        multiple[0]['tables'].append({'id': 't2', 'bbox_pdf': [20, 0, 30, 10], 'markdown': '|second| '})
        # 旧单表提交不能把两个独立表格合并，完整多表提交另测。
        with self.assertRaises(ValueError):
            apply_resolutions(multiple, issues, [item])
        for incomplete in ({**item, 'outside_text_verified': False}, {k: v for k, v in item.items() if k != 'outside_text'}):
            with self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [incomplete])

    def test_coverage_over_table_has_complete_repair_path(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't1', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        chunks[0]['errors'] = [{'code': 'ocr_coverage', 'bbox_pdf': [0, 0, 30, 10]}]
        original = deepcopy(chunks)
        issues = source_issues({'parse_status': 'partial'}, 'submission', chunks)
        self.assertEqual(issues[0].get('repair_bbox_pdf'), [0, 0, 30, 10])
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '原图逐格及表外核对',
                'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': '-1.25'}]},
                'outside_text': 'outside', 'outside_text_verified': True}
        updated, resolved, _ = apply_resolutions(chunks, issues, [item])
        self.assertEqual(updated[0]['tables'][0]['rows'], [['-1.25']])
        self.assertIn('outside', updated[0]['text'])
        self.assertEqual(resolved, {issues[0]['issue_key']})
        self.assertEqual(chunks, original)
        for invalid in ({**item, 'outside_text_verified': False},
                        {**item, 'action': 'irrelevant_region', 'non_text_kind': 'stamp'},
                        {**item, 'action': 'confirm'}):
            with self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [invalid])

    def test_cell_quality_has_table_repair_path_without_overwriting_other_content(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't1', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        chunks[0]['errors'] = [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 5, 5],
                                'quality_evidence': {'quality_scope': 'table_cell'}}]
        original = deepcopy(chunks)
        issues = source_issues({'parse_status': 'partial'}, 'submission', chunks)
        self.assertEqual(issues[0].get('repair_bbox_pdf'), [0, 0, 10, 10])
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '逐格核对原图',
                'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': 'correct'}]},
                'outside_text': '', 'outside_text_verified': True}
        updated, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual(updated[0]['tables'][0]['rows'], [['correct']])
        self.assertIn('outside', updated[0]['text'])
        self.assertEqual(chunks, original)
        self.assertEqual(resolved, {issues[0]['issue_key']})
        self.assertEqual(apply_resolutions(chunks, issues, saved)[0], updated)
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, issues, [{**item, 'action': 'correct_text', 'text': 'correct'}])
        # 表外质量问题仍走文字修订，不借此扩大表格修改范围。
        chunks[0]['errors'][0]['bbox_pdf'] = [20, 0, 30, 10]
        self.assertNotIn('repair_bbox_pdf', source_issues({'parse_status': 'partial'}, 'submission', chunks)[0])

    def test_cell_quality_outcome_summary_does_not_create_unrepairable_duplicate(self):
        from agent.agent_backend.services.filing_parse_outcome import outcome
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        chunks = deepcopy(self.chunks)
        chunks[0]['errors'] = [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 5, 5],
                                'quality_evidence': {'quality_scope': 'table_cell', 'word_count': 12}}]
        result = outcome(chunks)
        issues = source_issues(result, 'submission', chunks)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['chunk_index'], 0)

    def test_ocr_recovery_evidence_survives_summary_without_duplicate_blockers(self):
        from agent.agent_backend.services.filing_parse_outcome import outcome
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        details = [('ocr_quality', {'recovery_conflicts': [{'reason_code': 'recovery_conflict'}]}),
                   ('ocr_coverage', {'recovery_conflicts': [{'reason_code': 'recovery_conflict'}]}),
                   ('ocr_coverage', {'uncovered_components': [[1, 1, 4, 4]]}),
                   ('ocr_quality', {'low_confidence_words': [{'text': 'SYNTHETIC', 'bbox': [1, 1, 4, 4]}]})]
        for code, detail in details:
            with self.subTest(code=code, detail=detail):
                chunks = deepcopy(self.chunks)
                chunks[0]['errors'] = [{'code': code, 'stage': code, 'bbox_pdf': [0, 0, 5, 5], **detail}]
                result = outcome(chunks)
                self.assertEqual(len(source_issues(result, 'submission', chunks)), 1)
                for key, value in detail.items():
                    self.assertEqual(result['parse_diagnostics']['errors'][0][key], value)
                # 同页同码不同证据仍是独立问题，不能只按坐标去重。
                summary = deepcopy(result['parse_diagnostics']['errors'][0])
                summary[next(iter(detail))] = []
                result['parse_diagnostics']['errors'].append(summary)
                self.assertEqual(len(source_issues(result, 'submission', chunks)), 2)

    def test_complete_table_revision_can_explicitly_address_related_quality_and_coverage(self):
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't1', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        issues = [dict(self.issues[0]), {**self.issues[0], 'issue_key': 'quality', 'code': 'ocr_quality'},
                  {**self.issues[0], 'issue_key': 'outside', 'bbox_pdf': [20, 0, 30, 10]}]
        item = {'issue_key': 'e', 'action': 'correct_table', 'reason': '完整原格补录',
                'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': 'correct'}]},
                'outside_text': '', 'outside_text_verified': True,
                'related_issues': [{'issue_key': 'quality', 'reason': '本格错误文字已逐字改正'}]}
        output, resolved, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual(resolved, {'e', 'quality'})
        self.assertEqual(apply_resolutions(chunks, issues, saved)[0], output)
        self.assertEqual(apply_resolutions(chunks, issues, [{**item, 'related_issues': []}])[1], {'e'})
        for extra in ([{'issue_key': 'outside', 'reason': '不在修订范围'}],
                      [{'issue_key': 'quality', 'reason': ''}], item['related_issues'] * 2):
            with self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, 'related_issues': extra}])
        for code in ('numeric_uncertain', 'ocr_timeout', 'field_region_crossing'):
            unrelated = [issues[0], {**issues[1], 'code': code}, issues[2]]
            with self.assertRaises(ValueError):
                apply_resolutions(chunks, unrelated, [item])
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, [issues[0], {**issues[1], 'chunk_index': 1}], [item])

    def test_table_numeric_resolution_requires_all_candidate_regions(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        for candidates, expected in (([[1, 1, 4, 4]], True),
                                     ([[1, 1, 4, 4], [21, 1, 28, 4]], False),
                                     ([[1, 1, 4, 4], None], False), ([], False)):
            with self.subTest(candidates=candidates):
                chunks = deepcopy(self.chunks)
                chunks[0]['tables'] = [{'id': 't1', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
                chunks[0]['errors'] = [
                    {'code': 'table_structure_unresolved', 'bbox_pdf': [0, 0, 10, 10]},
                    {'code': 'numeric_uncertain', 'bbox_pdf': [0, 0, 30, 10],
                     'numeric_verification': [{'status': 'numeric_uncertain', 'bbox_pdf': b} for b in candidates]}]
                issues = source_issues({'parse_status': 'partial'}, 'submission', chunks)
                item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '核对全部单元格',
                        'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': '-1.25'}]}}
                _, resolved, _ = apply_resolutions(chunks, issues, [item])
                self.assertEqual(issues[1]['issue_key'] in resolved, expected)

    def test_table_repair_resolves_only_contained_structural_duplicates(self):
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 't1', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'}]
        issues = [{**self.issues[0], 'code': 'table_review_required'},
                  {**self.issues[0], 'issue_key': 'duplicate', 'code': 'table_structure_unresolved'},
                  {**self.issues[0], 'issue_key': 'coverage', 'code': 'ocr_coverage'},
                  {**self.issues[0], 'issue_key': 'outside', 'code': 'table_structure_unresolved', 'bbox_pdf': [20, 0, 30, 10]}]
        item = {'issue_key': 'e', 'action': 'correct_table', 'reason': '逐格核对',
                'table': {'row_count': 1, 'column_count': 1, 'cells': [{'row': 0, 'column': 0, 'text': 'new'}]}}
        _, resolved, _ = apply_resolutions(chunks, issues, [item])
        self.assertEqual(resolved, {'e', 'duplicate'})
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, issues, [item, {**item, 'issue_key': 'duplicate'}])

    def test_multiple_tables_keep_separate_geometry_and_require_all(self):
        chunks = deepcopy(self.chunks)
        chunks[0]['tables'] = [{'id': 'left', 'bbox_pdf': [0, 0, 10, 10], 'markdown': '|old|'},
                               {'id': 'right', 'bbox_pdf': [20, 0, 30, 10], 'markdown': '|outside|'}]
        chunks[0]['lines'] = [{'text': 'old outside UNKNOWN', 'bbox': [0, 0, 40, 5]}]
        issues = [{**self.issues[0], 'code': 'text_assembly_unresolved', 'bbox_pdf': [0, 0, 40, 5]}]
        tables = [{'table_index': i, 'verified': True, 'table': {'row_count': 1, 'column_count': 1,
                   'cells': [{'row': 0, 'column': 0, 'text': text}]}} for i, text in enumerate(('LEFT', 'RIGHT'))]
        item = {'issue_key': 'e', 'action': 'correct_table', 'reason': '两表独立核对',
                'tables': tables, 'outside_text': 'NOTE', 'outside_text_verified': True}
        result, _, saved = apply_resolutions(chunks, issues, [item])
        self.assertEqual([t['id'] for t in result[0]['tables']], ['left', 'right'])
        self.assertEqual([t['rows'] for t in result[0]['tables']], [[['LEFT']], [['RIGHT']]])
        self.assertEqual([t['bbox_pdf'] for t in result[0]['tables']], [[0, 0, 10, 10], [20, 0, 30, 10]])
        self.assertIn('NOTE', result[0]['text'])
        self.assertEqual(apply_resolutions(chunks, issues, saved)[0], result)
        for invalid in (tables[:1], [tables[0], tables[0]], [{**tables[0], 'verified': False}, tables[1]], tables + [{'table_index': 9, 'table': tables[0]['table']}]):
            with self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, 'tables': invalid}])


if __name__ == '__main__':
    unittest.main()
