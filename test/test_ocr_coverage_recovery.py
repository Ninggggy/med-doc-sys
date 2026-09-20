"""区域补识别的有界性、正常路径零额外调用和冲突保护。"""
import unittest
from unittest.mock import patch, Mock
import fitz
from agent.agent_backend.utils.parser import pdf_page_extractor as parser


class CoverageRecoveryTests(unittest.TestCase):
    def test_spatial_recovery_grouping_matches_all_pairs_oracle(self):
        import random
        randomizer = random.Random(920)
        region = fitz.Rect(-100, -100, 1200, 1200)
        for trial in range(8):
            boxes = []
            for _ in range(100):
                x, y = randomizer.uniform(0,1000), randomizer.uniform(0,1000)
                boxes.append(fitz.Rect(x,y,x+randomizer.uniform(1,100),y+randomizer.uniform(1,40)))
            expanded = [b+(-max(b.height,1),-max(b.height,1)/3,max(b.height,1),max(b.height,1)/3) for b in boxes]
            components = [{i} for i in range(len(boxes))]
            for i, left in enumerate(boxes):
                for j in range(i+1,len(boxes)):
                    if not (expanded[i]&boxes[j]).is_empty or not (expanded[j]&left).is_empty:
                        a = next(group for group in components if i in group)
                        b = next(group for group in components if j in group)
                        if a is not b:
                            a.update(b)
                            components.remove(b)
            expected = []
            for group in components:
                indices = iter(group)
                box = fitz.Rect(boxes[next(indices)])
                for index in indices:
                    box |= boxes[index]
                expected.append(tuple(box))
            actual = [tuple(box) for box, _ in parser.recovery_boxes(boxes, region)]
            self.assertEqual(sorted(actual), sorted(expected))

    def test_partial_recovery_with_conflict_stays_blocked_after_page_assembly(self):
        import tempfile
        from pathlib import Path
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.pdf'
            doc = fitz.open()
            page = doc.new_page(width=200, height=200)
            page.insert_text((20, 30), 'Synthetic original text for conflict regression')
            pix = page.get_pixmap()
            doc.save(path)
            doc.close()
            primary = {'text':'Original', 'bbox':[20,20,60,30], 'confidence':99, 'source':'ocr'}
            recovered = {'text':'Added', 'bbox':[70,20,100,30], 'confidence':99, 'source':'ocr'}
            attempt = {'status':'recovered', 'bbox_pdf':[10,10,110,40], 'trigger':'ocr_coverage',
                       'reason_code':'recovery_context_ambiguous', 'has_conflicting_candidates':True,
                       'accepted_count':1, 'candidates':[{'text':'Alternative','bbox':[20,20,60,30]}]}
            with patch.object(parser, 'ocr_region', return_value=([primary], [primary], pix)), patch.object(
                    parser, 'recover_ocr_coverage', return_value=([recovered], [recovered], [attempt])), patch.object(
                    parser, 'uncovered_image', return_value=([], pix)), patch.object(
                    parser, 'raster_cells', return_value=[]), patch.object(parser, 'borderless_tables', return_value=([], [])):
                pages = parser.extract_pdf_pages(str(path), ocr_client=Mock(timeout_seconds=120), force_ocr=True, dpi=72)
            self.assertEqual(pages[0]['status'], 'partial')
            self.assertIn('Original', pages[0]['text'])
            self.assertIn('Added', pages[0]['text'])
            conflicts = [e for e in pages[0]['errors'] if e.get('recovery_conflicts')]
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]['recovery_conflicts'][0]['candidates'], attempt['candidates'])
            self.assertTrue(source_issues({'content_status':'partial'}, 'submission', pages))

    def test_later_ambiguous_candidate_cannot_erase_earlier_text_conflict(self):
        primary = {'text':'原文字','bbox':[20,20,36,28],'confidence':99}
        conflicting = {'text':'另一字','bbox':[20,20,36,28],'confidence':99}
        ambiguous = {'text':'孤立候选','bbox':[50,50,70,80],'confidence':99}
        (added, _, attempts), _ = self.run_recovery([[18,18,80,82]], [conflicting, ambiguous], [primary])
        self.assertEqual(added, [])
        self.assertEqual(primary['text'], '原文字')
        self.assertTrue(attempts[0].get('has_conflicting_candidates'))
        self.assertEqual(attempts[0]['reason_code'], 'recovery_context_ambiguous')
        self.assertEqual(len(attempts[0]['candidates']), 2)

    def test_quality_word_box_extends_unique_layout_without_crossing_another_block(self):
        from copy import deepcopy
        blocks = [{'bbox':[20,50,120,79.8],'mode':6,'dpi':300},
                  {'bbox':[20,100,120,120],'mode':6,'dpi':180}]
        original = deepcopy(blocks)
        expanded = parser.recovery_layout_quality_bounds(blocks,[{'bbox':[30,65,90,80]}],fitz.Rect(0,0,200,200))
        self.assertEqual(expanded[0]['bbox'],[20,50,120,80])
        self.assertEqual(expanded[1],blocks[1])
        self.assertEqual(blocks,original)
        # 不合并跨块诊断，不把独立低质量区牵入其他版面块。
        for box in ([30,65,90,110],[150,65,190,80]):
            self.assertEqual(parser.recovery_layout_quality_bounds(blocks,[{'bbox':box}],fitz.Rect(0,0,200,200)),blocks)

    def test_fractional_quality_edge_reuses_existing_block_request(self):
        missing = [[30+i*10,60,32+i*10,62] for i in range(5)]
        def run(expand):
            with patch.object(parser,'uncovered_image',return_value=(missing,object())), patch.object(
                    parser,'recovery_layout_blocks',return_value=[{'bbox':[20,50,120,79.8],'mode':6,'dpi':300}]), patch.object(
                    parser,'recovery_layout_quality_bounds',side_effect=expand), patch.object(
                    parser,'ocr_region',return_value=([],[],None)) as call, patch.object(parser.time,'monotonic',return_value=10):
                parser.recover_ocr_coverage(object(),fitz.Rect(0,0,200,200),Mock(),'eng',180,[],100,
                    {'execution_budget_enforced':True},quality_lines=[{'bbox':[30,65,90,80]}])
                return call.call_count
        original = parser.recovery_layout_quality_bounds
        self.assertEqual(run(lambda layout,*args:layout),2)
        self.assertEqual(run(original),1)

    def test_good_page_average_does_not_hide_low_quality_table_cell(self):
        words = [{'text':'正文','bbox':[50,10+i,60,11+i],'confidence':95} for i in range(100)]
        cell_words = [{'text':'格内','bbox':[5,5+i,15,6+i],'confidence':18 if i<2 else 95} for i in range(12)]
        words.extend(cell_words)
        self.assertFalse(parser.ocr_quality_evidence(words)[2])
        self.assertEqual(parser.quality_recovery_lines(words,[]),[])
        result = parser.quality_recovery_lines(words,[],[[0,0,20,30]])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['quality_scope'],'table_cell')
        self.assertEqual(result[0]['word_count'],12)
        self.assertEqual(result[0]['low_confidence_word_count'],2)
        self.assertTrue(fitz.Rect(0,0,20,30).contains(fitz.Rect(result[0]['bbox'])))

    def test_region_budget_selects_larger_diagnostics_but_runs_in_reading_order(self):
        boxes = [[10,10,11,11]] + [[10,y,20,y+10] for y in (50,100,150,200)]
        jobs = [(fitz.Rect(box),fitz.Rect(box)) for box in boxes]
        with patch.object(parser,'uncovered_image',return_value=(boxes,None)), patch.object(
                parser,'recovery_boxes',return_value=jobs), patch.object(
                parser,'ocr_region',return_value=([],[],None)) as call, patch.object(
                parser.time,'monotonic',return_value=10):
            _,_,attempts = parser.recover_ocr_coverage(None,fitz.Rect(0,0,300,300),Mock(),
                'eng',180,[],100,{'execution_budget_enforced':True})
        self.assertEqual(call.call_count,4)
        self.assertEqual([list(args.args[1]) for args in call.call_args_list],boxes[1:])
        self.assertEqual(len(attempts),5)
        self.assertEqual(attempts[0]['reason_code'],'recovery_region_limit')
        self.assertEqual(attempts[0]['uncovered_bbox_pdf'],boxes[0])

    def test_layout_crossing_table_preserves_whole_outside_text_band(self):
        from copy import deepcopy
        block = {'bbox':[0,0,100,100],'mode':6,'dpi':180}
        cells = [[10,50,50,100],[50,50,100,100]]
        original = deepcopy([block,cells])
        pieces = parser.recovery_layout_outside_cells([block],cells)
        self.assertIn([0,0,100,50],[p['bbox'] for p in pieces])
        self.assertEqual(sum(fitz.Rect(p['bbox']).get_area() for p in pieces),5500)
        for piece in pieces:
            self.assertEqual(piece['dpi'],180)
            for cell in cells:
                self.assertEqual((fitz.Rect(piece['bbox'])&fitz.Rect(cell)).get_area(),0)
        self.assertEqual([block,cells],original)

    def test_layout_inside_one_cell_or_outside_all_cells_unchanged(self):
        block = {'bbox':[10,10,20,20],'mode':7,'dpi':216}
        for cells in ([],[[0,0,30,30]],[[50,50,60,60]]):
            self.assertEqual(parser.recovery_layout_outside_cells([block],cells),[block])

    def test_fractional_cell_interior_uses_same_geometry_as_quality_region(self):
        from agent.agent_backend.utils.parser import ocr_reflow_geometry
        cell = [471.4,246.4,526,347.6]
        interior = fitz.Rect(cell)+(1,1,-1,-1)
        words = [{'text':'A','bbox':[480,250,495,260],'confidence':90},
                 {'text':'B','bbox':[480,280,495,290],'confidence':90}]
        client = Mock(); client.supports_execution_budget = True; client.supports_preprocessing = True
        with patch.object(parser,'uncovered_image',return_value=([],None)), patch.object(
                ocr_reflow_geometry,'recognize_cell_candidates',return_value=[]) as recover, patch.object(
                parser,'ocr_region') as normal, patch.object(parser.time,'monotonic',return_value=10):
            _,_,attempts = parser.recover_ocr_coverage(object(),fitz.Rect(0,0,600,800),client,
                'eng',180,words,100,{'execution_budget_enforced':True,'recovery_cells':[cell]},
                quality_lines=[{'bbox':list(interior)}])
        recover.assert_called_once(); normal.assert_not_called()
        self.assertTrue(fitz.Rect(cell).contains(fitz.Rect(attempts[0]['bbox_pdf'])))

    def test_reflow_respects_four_regions_and_expired_shared_budget(self):
        from agent.agent_backend.utils.parser import ocr_reflow_geometry
        cells = [[i*50,0,i*50+40,100] for i in range(6)]
        words = [{'text':'原文','bbox':[i*50+5,y,i*50+20,y+10],'confidence':90}
                 for i in range(6) for y in (10,40)]
        missing = [word['bbox'] for word in words[::2]]
        client = Mock(); client.supports_execution_budget = True; client.supports_preprocessing = True
        for deadline,count,reason in [(100,4,'recovery_region_limit'),(5,0,'recovery_budget_exhausted')]:
            with self.subTest(deadline=deadline), patch.object(parser,'uncovered_image',return_value=(missing,None)), patch.object(
                    ocr_reflow_geometry,'recognize_cell_candidates',return_value=[]) as recover, patch.object(
                    parser.time,'monotonic',return_value=10):
                added,_,attempts = parser.recover_ocr_coverage(object(),fitz.Rect(0,0,350,120),client,
                    'eng',180,words,deadline,{'execution_budget_enforced':True,'recovery_cells':cells})
            self.assertEqual(added,[])
            self.assertEqual(recover.call_count,count)
            self.assertEqual(attempts[-1]['reason_code'],reason)

    def test_narrow_cell_reflow_is_shared_and_never_auto_accepted(self):
        from copy import deepcopy
        from agent.agent_backend.utils.parser import ocr_reflow_geometry
        original = [{'text':'旧值','bbox':[10,10,20,20],'confidence':90},
                    {'text':'另一行','bbox':[10,40,25,50],'confidence':90}]
        before = deepcopy(original)
        client = Mock(); client.supports_execution_budget = True; client.supports_preprocessing = True
        candidate = {'text':'候选','source_bboxes_pdf':[[10,10,20,20],[10,40,25,50]],'needs_review':True}
        with patch.object(parser,'uncovered_image',return_value=([[10,10,20,20],[10,40,25,50]],None)), patch.object(
                ocr_reflow_geometry,'recognize_cell_candidates',return_value=[candidate]) as recover, patch.object(
                parser,'ocr_region') as normal, patch.object(parser.time,'monotonic',return_value=10):
            added, lines, attempts = parser.recover_ocr_coverage(object(),fitz.Rect(0,0,100,150),client,
                'eng',180,original,100,{'execution_budget_enforced':True,'recovery_cells':[[0,0,40,100]]})
        self.assertEqual(added,[]); self.assertEqual(lines,[]); self.assertEqual(original,before)
        recover.assert_called_once(); normal.assert_not_called()
        self.assertEqual(len(attempts),2)
        self.assertEqual(attempts[1]['reused_attempt_index'],0)
        for attempt in attempts:
            self.assertEqual(attempt['reason_code'],'recovery_reflow_review_required')
            self.assertEqual(attempt['status'],'unresolved')

    def test_complete_grid_also_supplies_recovery_cell_ownership(self):
        with fitz.open() as document:
            page = document.new_page(width=160,height=200)
            for x in (10,60,110):
                page.draw_line((x,10),(x,170),color=(0,0,0),width=1)
            for y in (10,90,170):
                page.draw_line((10,y),(110,y),color=(0,0,0),width=1)
            client = Mock()
            client.supports_execution_budget = True
            client.image_to_data.return_value = {'text':[],'execution_budget_enforced':True}
            metadata = {}
            with patch.object(parser.time,'monotonic',return_value=10):
                parser.ocr_region(page,page.rect,client,'eng',180,6,deadline=100,metadata=metadata)
        self.assertEqual(len(metadata.get('recovery_cells',[])),4)
        for cell in metadata['recovery_cells']:
            self.assertGreater(cell[2],cell[0])
            self.assertGreater(cell[3],cell[1])

    def test_same_pass_ordered_text_neighbors_are_not_conflicting_alternatives(self):
        original = [{'text':'已有正文','bbox':[10,10,50,20],'confidence':95,'source':'ocr'}]
        candidates = [
            {'text':'/登记','bbox':[10,50,43,60],'confidence':95,'source':'ocr'},
            {'text':'号','bbox':[35,49,43,62],'confidence':93,'source':'ocr'}]
        for ordered, numeric, expected in [(True,False,2),(False,False,1),(True,True,1)]:
            with self.subTest(ordered=ordered,numeric=numeric):
                from copy import deepcopy
                items = deepcopy(candidates)
                if numeric:
                    items[0]['text'] = '12.5'
                    items[1]['text'] = '5'
                    for item in items:
                        item['numeric_verification']={'status':'verified'}
                lines = [{'_ordered_words': items}] if ordered else []
                with patch.object(parser,'uncovered_image',return_value=([[9,48,45,64]],None)), patch.object(
                        parser,'ocr_region',return_value=(items,lines,None)), patch.object(
                        parser.time,'monotonic',return_value=10):
                    added, _, attempts = parser.recover_ocr_coverage(None,fitz.Rect(0,0,100,100),Mock(),
                        'chi_sim+eng',180,deepcopy(original),100,{'execution_budget_enforced':True})
                self.assertEqual(len(added),expected)
                if expected == 1:
                    self.assertEqual(attempts[0]['reason_code'],'recovery_conflict')

    def test_single_glyphs_can_use_later_accepted_same_line_anchor(self):
        original = [{'text': '已有正文', 'bbox': [10, 10, 50, 20], 'confidence': 95}]
        candidates = [
            {'text': '原', 'bbox': [10, 50, 20, 60], 'confidence': 92},
            {'text': '药', 'bbox': [21, 50, 31, 60], 'confidence': 93},
            {'text': '品', 'bbox': [32, 50, 42, 60], 'confidence': 92},
            {'text': '批准', 'bbox': [43, 50, 63, 60], 'confidence': 93},
        ]
        for candidate in candidates:
            candidate['source'] = 'ocr'
        with patch.object(parser, 'uncovered_image', return_value=([[9,49,65,61]], None)), patch.object(
                parser, 'ocr_region', return_value=(candidates, [], None)), patch.object(
                parser.time, 'monotonic', return_value=10):
            added, recovered_lines, _ = parser.recover_ocr_coverage(None, fitz.Rect(0,0,100,100), Mock(),
                'chi_sim+eng', 180, original, 100, {'execution_budget_enforced': True})
        self.assertEqual(parser.words_text(added).replace(' ', ''), '原药品批准')
        self.assertEqual(len(added), 4)
        # 不能以每个字框顶边排序，微小纵向偏差不应把“品”移至“原”前。
        candidates[2]['bbox'][1] = 49.8
        with patch.object(parser, 'uncovered_image', return_value=([[9,49,65,61]], None)), patch.object(
                parser, 'ocr_region', return_value=(candidates, [], None)), patch.object(
                parser.time, 'monotonic', return_value=10):
            added, recovered_lines, _ = parser.recover_ocr_coverage(None, fitz.Rect(0,0,100,100), Mock(),
                'chi_sim+eng', 180, original, 100, {'execution_budget_enforced': True})
        text, errors = parser.assemble_page_text(recovered_lines, added, [])
        self.assertEqual(''.join(text.split()), '原药品批准')
        self.assertEqual(errors, [])

    def test_separated_repeated_character_remains_a_new_candidate(self):
        original = [{'text':'人','bbox':[10,20,20,30],'confidence':95}]
        candidate = {'text':'人','bbox':[22,20,32,30],'confidence':95}
        with patch.object(parser,'uncovered_image',return_value=([candidate['bbox']],None)), patch.object(
                parser,'ocr_region',return_value=([candidate],[],None)), patch.object(
                parser.time,'monotonic',return_value=10):
            added, _, _ = parser.recover_ocr_coverage(None,fitz.Rect(0,0,100,100),Mock(),
                'chi_sim+eng',180,original,100,{'execution_budget_enforced':True})
        self.assertEqual([w['text'] for w in added],['人'])

    def test_same_text_subpixel_neighbor_is_not_added_twice(self):
        original = [{'text':'来','bbox':[146.4,520,152,530.4],'confidence':95}]
        candidate = {'text':'来','bbox':[152.16,520.08,154.8,529.92],'confidence':93}
        with patch.object(parser,'uncovered_image',return_value=([candidate['bbox']],None)), patch.object(
                parser,'ocr_region',return_value=([candidate],[],None)), patch.object(
                parser.time,'monotonic',return_value=10):
            added, _, attempts = parser.recover_ocr_coverage(None,fitz.Rect(0,0,600,842),Mock(),
                'chi_sim+eng',180,original,100,{'execution_budget_enforced':True})
        self.assertEqual(added,[])
        self.assertEqual(attempts[0]['reason_code'],'recovery_conflict')
        self.assertEqual(attempts[0]['candidates'][0]['text'],'来')
        self.assertEqual(len(original),1)

    def test_reused_crop_preserves_full_evidence_once_and_region_candidates(self):
        crop = fitz.Rect(0,0,100,100)
        regions = [fitz.Rect(10,10,20,20),fitz.Rect(10,70,20,80)]
        candidates = [{'text':'first','bbox':list(regions[0]),'confidence':70},
                      {'text':'second','bbox':list(regions[1]),'confidence':70}]
        with patch.object(parser,'uncovered_image',return_value=([list(r) for r in regions],None)), patch.object(
                parser,'recovery_boxes',return_value=[(r,crop) for r in regions]), patch.object(
                parser,'ocr_region',return_value=(candidates,[],None)) as call, patch.object(
                parser.time,'monotonic',return_value=10):
            _, _, attempts = parser.recover_ocr_coverage(None,crop,Mock(),'eng',180,[],100,
                {'execution_budget_enforced':True})
        self.assertEqual(call.call_count,1)
        self.assertEqual(attempts[0]['candidates'],candidates)
        self.assertEqual(attempts[1]['candidates'],[candidates[1]])
        self.assertEqual(attempts[1]['reused_attempt_index'],0)

    def test_fragmented_layout_reuses_blocks_without_dropping_problem_regions(self):
        regions = [fitz.Rect(x,y,x+2,y+2) for x,y in [(10,10),(20,10),(30,10),(40,10),(10,70),(20,70)]]
        blocks = [{'bbox':[0,0,100,50],'mode':6,'dpi':300},
                  {'bbox':[0,60,100,100],'mode':6,'dpi':300}]
        with patch.object(parser,'uncovered_image',return_value=([list(r) for r in regions],object())), patch.object(
                parser,'recovery_boxes',return_value=[(r,r) for r in regions]), patch.object(
                parser,'recovery_layout_blocks',return_value=blocks), patch.object(
                parser,'ocr_region',return_value=([],[],None)) as call, patch.object(
                parser.time,'monotonic',return_value=10):
            _, _, attempts = parser.recover_ocr_coverage(Mock(),fitz.Rect(0,0,100,100),Mock(),
                'eng',180,[],100,{'execution_budget_enforced':True})
        self.assertEqual(call.call_count,2)
        self.assertEqual(len(attempts),6)
        self.assertEqual([a['uncovered_bbox_pdf'] for a in attempts],[list(r) for r in regions])
        self.assertTrue(all(a['recovery_path']=='original_layout_block' for a in attempts))
        self.assertTrue(all(c.kwargs['deadline']==100 for c in call.call_args_list))

    def test_layout_block_crossing_cells_falls_back_to_original_regions(self):
        regions = [fitz.Rect(10,y,12,y+2) for y in (10,20,30,40,50)]
        with patch.object(parser,'uncovered_image',return_value=([list(r) for r in regions],object())), patch.object(
                parser,'recovery_boxes',return_value=[(r,r) for r in regions]), patch.object(
                parser,'recovery_layout_blocks',return_value=[{'bbox':[0,0,100,100],'mode':6,'dpi':300}]), patch.object(
                parser,'ocr_region',return_value=([],[],None)) as call, patch.object(
                parser.time,'monotonic',return_value=10):
            _, _, attempts = parser.recover_ocr_coverage(Mock(),fitz.Rect(0,0,100,100),Mock(),
                'eng',180,[],100,{'execution_budget_enforced':True,'recovery_cells':[[0,0,40,100],[40,0,100,100]]})
        self.assertEqual(call.call_count,4)
        self.assertFalse(any('recovery_path' in a for a in attempts))
        self.assertEqual(attempts[-1]['reason_code'],'recovery_region_limit')

    def test_coverage_crossing_known_cells_keeps_evidence_without_assignment(self):
        missing = [[30,20,50,30]]
        candidate = {'text':'crossing','bbox':missing[0],'confidence':99}
        original = [{'text':'reference','bbox':[5,80,25,90],'confidence':99}]
        with patch.object(parser,'uncovered_image',return_value=(missing,None)), patch.object(
                parser,'ocr_region',return_value=([candidate],[],None)) as call, patch.object(
                parser.time,'monotonic',return_value=10):
            added, _, attempts = parser.recover_ocr_coverage(None,fitz.Rect(0,0,100,100),
                Mock(),'eng',180,original,100,{'execution_budget_enforced':True,
                'recovery_cells':[[0,0,40,60],[40,0,100,60]]})
        self.assertEqual(call.call_count,1)
        self.assertEqual(added,[])
        self.assertEqual(attempts[0]['reason_code'],'recovery_context_ambiguous')
        self.assertEqual(attempts[0]['uncovered_bbox_pdf'],missing[0])
        self.assertEqual(attempts[0]['candidates'][0]['text'],'crossing')
        self.assertEqual(attempts[0]['ambiguous_cell_candidates'][0]['candidate_cell_bboxes'],
                         [[0,0,40,60],[40,0,100,60]])

    def test_coverage_outside_known_table_can_still_be_recovered(self):
        candidate = {'text':'outside','bbox':[20,80,40,90],'confidence':99}
        with patch.object(parser,'uncovered_image',return_value=([[20,80,40,90]],None)), patch.object(
                parser,'ocr_region',return_value=([candidate],[],None)) as call, patch.object(
                parser.time,'monotonic',return_value=10):
            added, _, _ = parser.recover_ocr_coverage(None,fitz.Rect(0,0,100,100),Mock(),'eng',180,
                [{'text':'reference','bbox':[5,20,25,30],'confidence':99}],100,
                {'execution_budget_enforced':True,'recovery_cells':[[0,0,40,60],[40,0,100,60]]})
        self.assertEqual(call.call_count,1)
        self.assertEqual([word['text'] for word in added],['outside'])

    def test_unique_cell_candidate_and_duplicate_geometry_remain_usable(self):
        for cells in ([[0,0,60,60]], [[0,0,60,60],[0,0,60,60]]):
            with self.subTest(cells=cells), patch.object(parser,'uncovered_image',return_value=(
                    [[20,20,40,30]],None)), patch.object(parser,'ocr_region',return_value=(
                    [{'text':'inside','bbox':[20,20,40,30],'confidence':99}],[],None)), patch.object(
                    parser.time,'monotonic',return_value=10):
                added, _, _ = parser.recover_ocr_coverage(None,fitz.Rect(0,0,100,100),Mock(),
                    'eng',180,[{'text':'reference','bbox':[5,80,25,90],'confidence':99}],100,
                    {'execution_budget_enforced':True,'recovery_cells':cells})
            self.assertEqual([word['text'] for word in added],['inside'])

    def test_quality_crop_excludes_far_away_other_column(self):
        words = [{'text':'wrong','bbox':[10,20,30,30],'confidence':10},
                 {'text':'near','bbox':[34,20,54,30],'confidence':99},
                 {'text':'other','bbox':[300,20,330,30],'confidence':99}]
        lines = [{'text':'wrong near other','bbox':[10,20,330,30]}]
        actual = parser.quality_recovery_lines(words,lines)
        self.assertEqual([line['bbox'] for line in actual],[[10,20,54,30]])
        self.assertEqual(lines[0]['bbox'],[10,20,330,30])
        self.assertEqual(words[0]['text'],'wrong')

    def test_quality_geometry_keeps_both_problem_columns_and_ignores_good_page(self):
        words = [{'text':'left','bbox':[10,20,30,30],'confidence':10},
                 {'text':'right','bbox':[300,20,330,30],'confidence':20}]
        lines = [{'text':'left right','bbox':[10,20,330,30]}]
        self.assertEqual([line['bbox'] for line in parser.quality_recovery_lines(words,lines)],
                         [[10,20,30,30],[300,20,330,30]])
        good = [{**word,'confidence':99} for word in words]
        self.assertEqual(parser.quality_recovery_lines(good,lines),[])

    def test_quality_regions_share_four_call_limit_and_preserve_unprocessed(self):
        quality = [{'text':'uncertain','bbox':[20,y,60,y+10]} for y in (20,70,120,170,220)]
        with patch.object(parser,'uncovered_image',return_value=([],None)), patch.object(
                parser,'ocr_region',return_value=([],[],None)) as call, patch.object(
                parser.time,'monotonic',return_value=10):
            _, _, attempts = parser.recover_ocr_coverage(None,fitz.Rect(0,0,500,500),
                Mock(),'eng',180,[],100,{'execution_budget_enforced':True},quality_lines=quality)
        self.assertEqual(call.call_count,4)
        self.assertEqual(len(attempts),5)
        self.assertEqual(attempts[-1]['reason_code'],'recovery_region_limit')
        self.assertEqual(attempts[-1]['quality_bbox_pdf'],quality[-1]['bbox'])

    def test_quality_line_crossing_cells_is_not_ocr_assigned(self):
        with patch.object(parser,'uncovered_image',return_value=([],None)), patch.object(
                parser,'ocr_region') as call:
            _, _, attempts = parser.recover_ocr_coverage(None,fitz.Rect(0,0,100,100),
                Mock(),'eng',180,[],100,{'execution_budget_enforced':True,
                'recovery_cells':[[0,0,40,100],[40,0,100,100]]},
                quality_lines=[{'text':'uncertain','bbox':[20,20,60,30]}])
        call.assert_not_called()
        self.assertEqual(attempts[0]['reason_code'],'recovery_context_ambiguous')

    def test_covered_low_quality_text_gets_local_candidate_without_overwrite(self):
        original = [{'text':'wrong', 'bbox':[20,20,50,30], 'confidence':10}]
        candidate = {'text':'correct', 'bbox':[20,20,50,30], 'confidence':95}
        with patch.object(parser, 'uncovered_image', return_value=([],None)), patch.object(
                parser, 'ocr_region', return_value=([candidate],[],None)) as call, patch.object(
                parser.time, 'monotonic', return_value=10):
            added, _, attempts = parser.recover_ocr_coverage(None,fitz.Rect(0,0,100,100),
                Mock(),'eng',180,original,100,{'execution_budget_enforced':True},
                quality_lines=[{'text':'wrong','bbox':[20,20,50,30]}])
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.args[5],7)
        self.assertTrue(call.call_args.kwargs['original'])
        self.assertEqual(added,[])
        self.assertEqual(original[0]['text'],'wrong')
        self.assertEqual(attempts[0]['trigger'],'ocr_quality')
        self.assertEqual(attempts[0]['reason_code'],'recovery_conflict')
        self.assertEqual(attempts[0]['candidates'][0]['text'],'correct')

    def test_identical_crop_reuses_result_but_keeps_each_missing_region(self):
        crop = fitz.Rect(10,10,50,50)
        regions = [(fitz.Rect(20,y,21,y+2), crop) for y in (20,30)]
        with patch.object(parser, 'recovery_boxes', return_value=regions):
            (_, _, attempts), call = self.run_recovery([[20,20,21,22],[20,30,21,32]])
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[1]['reused_attempt_index'], 0)
        self.assertNotEqual(attempts[0]['uncovered_bbox_pdf'], attempts[1]['uncovered_bbox_pdf'])

    def test_duplicate_crop_does_not_consume_distinct_region_budget(self):
        boxes = [fitz.Rect(10,10+y,20,20+y) for y in (0,0,50,100,150,200)]
        with patch.object(parser, 'recovery_boxes', return_value=[(box,box) for box in boxes]):
            (_, _, attempts), call = self.run_recovery([list(box) for box in boxes])
        self.assertEqual(call.call_count, 4)
        self.assertEqual(attempts[4]['status'], 'unresolved')
        self.assertEqual(attempts[5]['reason_code'], 'recovery_region_limit')

    def test_failed_identical_crop_is_not_retried(self):
        box = fitz.Rect(10,10,20,20)
        with patch.object(parser, 'uncovered_image', return_value=([list(box)], None)), patch.object(
                parser, 'recovery_boxes', return_value=[(box,box),(box,box)]), patch.object(
                parser, 'ocr_region', side_effect=TimeoutError('synthetic')) as call, patch.object(
                parser.time, 'monotonic', return_value=10):
            _, _, attempts = parser.recover_ocr_coverage(None, fitz.Rect(0,0,100,100), Mock(),
                'eng', 180, [], 100, {'execution_budget_enforced':True})
        self.assertEqual(call.call_count, 1)
        self.assertEqual([a['reason_code'] for a in attempts], ['recovery_failed']*2)
        self.assertEqual(attempts[1]['exception_type'], 'TimeoutError')

    def test_same_crop_different_segmentation_is_not_reused(self):
        crop = fitz.Rect(10,10,50,50)
        regions = [(fitz.Rect(20,20,21,22),crop),(fitz.Rect(20,30,30,32),crop)]
        # 模式来自实际行数，不再由问题框宽高猜测；验证不同模式仍不共用缓存。
        with patch.object(parser, 'recovery_boxes', return_value=regions), patch(
                'agent.agent_backend.utils.parser.pdf_scan_evidence.recovery_segmentation',
                side_effect=[(7,'image_lines'),(6,'image_lines')]):
            (_, _, attempts), call = self.run_recovery([[20,20,21,22]])
        self.assertEqual(call.call_count, 2)
        self.assertNotIn('reused_attempt_index', attempts[1])

    def test_small_stroke_crop_preserves_adjacent_glyph_context(self):
        existing = [{'text':'标题', 'bbox':[20,20,40,36], 'confidence':99}]
        (_, _, attempts), call = self.run_recovery([[40.5,24,41.5,27]], words=existing)
        crop = call.call_args.args[1]
        self.assertTrue(crop.contains(fitz.Rect(20,20,40,36)))
        self.assertEqual(attempts[0]['uncovered_bbox_pdf'], [40.5,24,41.5,27])
        self.assertEqual(call.call_count, 1)

    def test_small_stroke_context_does_not_cross_known_cell(self):
        existing = [{'text':'邻列', 'bbox':[20,20,40,36], 'confidence':99}]
        with patch.object(parser, 'uncovered_image', return_value=([[42,24,43,27]], None)), patch.object(
                parser, 'ocr_region', return_value=([], [], None)) as call, patch.object(
                parser.time, 'monotonic', return_value=10):
            parser.recover_ocr_coverage(None, fitz.Rect(0,0,100,100), Mock(), 'eng', 180,
                existing, 100, {'execution_budget_enforced':True,
                               'recovery_cells':[[0,10,41,40],[41,10,80,40]]})
        self.assertGreaterEqual(call.call_args.args[1].x0, 42)
        self.assertEqual(existing[0]['bbox'], [20,20,40,36])

    def test_merging_does_not_create_new_neighbors_in_empty_bounding_box(self):
        from itertools import permutations
        boxes=[[10,10,20,20],[24,17,34,27],[48,29,58,39]]
        for ordering in permutations(boxes):
            actual=parser.recovery_boxes(ordering,fitz.Rect(0,0,100,100))
            self.assertEqual([list(ink) for ink,_ in actual],[[10,10,34,27],[48,29,58,39]])
            for source in boxes:
                self.assertTrue(any(crop.contains(fitz.Rect(source)) for _,crop in actual))

    def test_cell_inset_scales_without_cutting_ink(self):
        with patch.object(parser, 'uncovered_image', return_value=([[46,40,58,56]], None)), patch.object(
                parser, 'ocr_region', return_value=([], [], None)) as call, patch.object(
                parser.time, 'monotonic', return_value=10):
            parser.recover_ocr_coverage(None, fitz.Rect(0,0,200,200), Mock(), 'eng', 108, [], 100,
                {'execution_budget_enforced':True, 'recovery_cells':[[40,20,120,100]], 'recovery_cell_inset':2})
            crop = call.call_args.args[1]
            self.assertEqual(crop.x0, 42)
            self.assertTrue(crop.contains(fitz.Rect(46,40,58,56)))

    def test_cell_interior_crop_never_cuts_candidate_ink(self):
        cell = [20, 10, 60, 50]
        for missing, cells, expected_left in (([23,20,29,28], [cell], 21),
                                               ([20,20,26,28], [cell], 12),
                                               ([23,20,29,28], [cell, cell], 15)):
            with self.subTest(missing=missing, owners=len(cells)), patch.object(
                    parser, 'uncovered_image', return_value=([missing], None)), patch.object(
                    parser, 'ocr_region', return_value=([], [], None)) as call, patch.object(
                    parser.time, 'monotonic', return_value=10):
                parser.recover_ocr_coverage(None, fitz.Rect(0,0,100,100), Mock(), 'eng', 216, [], 100,
                    {'execution_budget_enforced': True, 'recovery_cells': cells})
                crop = call.call_args.args[1]
                self.assertTrue(crop.contains(fitz.Rect(missing)))
                self.assertEqual(crop.x0, expected_left)

    def run_recovery(self, missing, candidates=(), words=None, deadline=100, supported=True):
        with patch.object(parser, 'uncovered_image', return_value=(missing, None)), patch.object(
                parser, 'ocr_region', return_value=(list(candidates), [], None)) as call, patch.object(
                parser.time, 'monotonic', return_value=10):
            result = parser.recover_ocr_coverage(None, fitz.Rect(0, 0, 500, 500), Mock(), 'eng', 216,
                words or [], deadline, {'execution_budget_enforced': supported})
        return result, call

    def test_complete_page_has_no_extra_call(self):
        result, call = self.run_recovery([])
        self.assertEqual(result, ([], [], []))
        call.assert_not_called()

    def test_coverage_failure_does_not_discard_primary_words(self):
        words = [{'text':'original', 'bbox':[20,20,26,28], 'confidence':99}]
        metadata = {}
        with patch.object(parser, 'uncovered_image', side_effect=ValueError('synthetic')), patch.object(parser,'ocr_region') as call:
            added, _, attempts = parser.recover_ocr_coverage(None, fitz.Rect(0,0,50,50), Mock(), 'eng', 216, words, 100, metadata)
        self.assertEqual(words[0]['text'],'original')
        self.assertEqual(added,[])
        self.assertTrue(metadata['coverage_check_failed'])
        self.assertEqual(attempts[0]['reason_code'],'recovery_coverage_failed')
        call.assert_not_called()

    def test_original_crop_recovers_positioned_header(self):
        word = {'text':'B', 'bbox':[20,20,26,28], 'confidence':93, 'source':'ocr'}
        (words, lines, attempts), call = self.run_recovery([[20,20,26,28]], [word],
            [{'text':'A','bbox':[5,20,11,28],'confidence':99}])
        self.assertEqual(words[0]['text'], 'B')
        self.assertEqual(lines[0]['bbox'], word['bbox'])
        self.assertTrue(call.call_args.kwargs['original'])
        self.assertEqual(call.call_args.kwargs['deadline'], 100)
        self.assertEqual(attempts[0]['status'], 'recovered')

    def test_isolated_graphic_candidate_is_not_invented_as_text(self):
        word={'text':'O','bbox':[20,20,26,28],'confidence':99}
        (words, _, attempts), _ = self.run_recovery([[20,20,26,28]], [word])
        self.assertEqual(words,[])
        self.assertEqual(attempts[0]['reason_code'],'recovery_context_ambiguous')

    def test_multi_letter_graphic_without_comparable_text_stays_unresolved(self):
        candidate = {'text': 'VA', 'bbox': [49, 316, 141, 384], 'confidence': 88}
        existing = [{'text': 'Note', 'bbox': [40, 240, 63, 250], 'confidence': 99}]
        (words, _, attempts), _ = self.run_recovery([[49,316,141,384]], [candidate], existing)
        self.assertEqual(words, [])
        self.assertEqual(attempts[0]['status'], 'unresolved')
        self.assertEqual(attempts[0]['reason_code'], 'recovery_context_ambiguous')
        self.assertEqual(attempts[0]['candidates'], [candidate])

    def test_missing_word_with_comparable_text_is_preserved(self):
        candidate = {'text': 'Header', 'bbox': [20,20,55,28], 'confidence': 93}
        existing = [{'text': 'Other', 'bbox': [5,40,35,48], 'confidence': 99}]
        (words, _, _), _ = self.run_recovery([[20,20,55,28]], [candidate], existing)
        self.assertEqual(words[0]['text'], 'Header')

    def test_at_most_four_regions_and_no_recursive_retry(self):
        (_, _, attempts), call = self.run_recovery([[20,20+i*50,26,28+i*50] for i in range(6)])
        self.assertEqual(call.call_count, 4)
        self.assertEqual(len(attempts), 6)
        self.assertEqual(attempts[-1]['reason_code'], 'recovery_region_limit')

    def test_expired_or_old_server_never_starts_recovery(self):
        for kwargs in ({'deadline':9}, {'supported':False}):
            (_, _, attempts), call = self.run_recovery([[20,20,26,28]], **kwargs)
            call.assert_not_called()
            self.assertEqual(attempts[0]['status'], 'not_attempted')

    def test_duplicate_outside_low_confidence_and_symbols_not_added(self):
        existing = {'text':'A','bbox':[20,20,26,28],'confidence':99}
        for word in (existing, {'text':'B','bbox':[100,100,106,108],'confidence':99},
                     {'text':'B','bbox':[20,20,26,28],'confidence':79},
                     {'text':'|','bbox':[20,20,26,28],'confidence':99}):
            (added, _, _), _ = self.run_recovery([[20,20,26,28]], [word], [existing])
            self.assertEqual(added, [])

    def test_conflict_preserves_primary_and_marks_numeric_uncertain(self):
        existing = {'text':'1.25','bbox':[20,20,36,28],'confidence':99,
                    'numeric_verification':{'status':'verified','primary':'1.25','secondary':'1.25'}}
        candidate = {'text':'-1.25','bbox':[18,20,36,28],'confidence':70}
        (added, _, attempts), _ = self.run_recovery([[18,20,24,28]], [candidate], [existing])
        self.assertEqual(added, [])
        self.assertEqual(existing['text'], '1.25')
        self.assertEqual(existing['numeric_verification']['status'], 'numeric_uncertain')
        self.assertEqual(existing['numeric_verification']['recovery_candidates'][0]['text'], '-1.25')
        self.assertEqual(attempts[0]['candidates'][0]['text'], '-1.25')


if __name__ == '__main__':
    unittest.main()
