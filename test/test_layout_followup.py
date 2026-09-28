"""区域候选、残余字段和处理对象的行为回归；样本文字仅用于断言。"""
import sys
from pathlib import Path
import unittest
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from assembly import assemble_chunk
from regional_adoption import adopt_region,crop_scope
from agent.agent_backend.services.filing_review_targets import review_issues,apply_targets,target_value


def line(text,bbox,sid):
    return dict(text=text,bbox=bbox,source_span_id=sid,polygon=[[bbox[0],bbox[1]],[bbox[2],bbox[1]],[bbox[2],bbox[3]],[bbox[0],bbox[3]]])


def span(text,bbox,sid,score=.99):
    return dict(text_raw=text,polygon_page=line(text,bbox,sid)['polygon'],span_id=sid,score_raw=score,score_kind='paddle_rec_score',granularity='line',coordinate_source='engine_polygon')


def complete_input(entry):
    entry['result'].update(input={'input_to_page':[[1,0,-10],[0,1,-10],[0,0,1]]},input_size=[400,160])
    return entry


class LayoutFollowup(unittest.TestCase):
    def test_candidate_does_not_replace_existing_even_with_higher_score(self):
        for dx,dy in [(0,0),(160,35),(-40,120)]:
            b=[336+dx,432+dy,528+dx,447+dy];old=line('经济产业园9幢1602室（自主申报）',b,'old')
            entry=dict(region=dict(region_id='address',bbox_pdf=[140+dx,407+dy,623+dx,441+dy]),
                       result=dict(spans=[span('经济产业园9幢J602室（自主用报）',b,'new')]))
            output,decision=adopt_region([old],entry,lambda s:line(s['text_raw'],b,s['span_id']))
            self.assertEqual(output,[old]);self.assertTrue(decision['conflicts'][0]['differences'])
            scope=crop_scope(entry,[span(old['text'],b,'old')]);self.assertGreater(scope['bbox_pdf'][3],b[3])

    def test_adjacent_glyph_rows_may_have_overlapping_detection_envelopes(self):
        old=line('已有上一行',[0,10,300,30],'old')
        candidate=span('真实漏掉的下一行',[0,25,300,44],'new')
        entry=dict(region=dict(region_id='body',bbox_pdf=[0,0,310,100]),result=dict(spans=[candidate]))
        output,decision=adopt_region([old],complete_input(entry),lambda s:line(s['text_raw'],[0,25,300,44],s['span_id']))
        self.assertEqual([l['text'] for l in output],['已有上一行','真实漏掉的下一行'])
        self.assertEqual(decision['added_span_ids'],['new'])

    def test_partial_initial_glyph_adds_prefix_without_rewriting_suffix(self):
        old=line('所测试地址',[20,10,150,30],'old');candidate=span('住所测试地址',[0,10,150,30],'new')
        entry=dict(trigger='internal_partial_glyph_coverage',uncovered_bbox_pdf=[0,10,15,30],
                   region=dict(region_id='addr',bbox_pdf=[0,0,160,40]),result=dict(spans=[candidate]))
        output,decision=adopt_region([old],complete_input(entry),lambda s:dict(text=s['text_raw'],bbox=[0,10,15,30],source_span_id=s['span_id'],evidence=s))
        self.assertIn(old,output);self.assertEqual(output[-1]['text'],'住')
        self.assertFalse(decision['conflicts'])
        self.assertIsNone(output[-1]['evidence']['score_raw'])
        self.assertEqual(output[-1]['evidence']['supporting_line_score'],.99)

    def test_prefix_all_adoption_paths_require_score_and_complete_input(self):
        old=line('所测试地址',[20,10,150,30],'old')
        for score in [.1,None,'0.99',True,float('nan'),float('inf'),-1,101]:
            e=complete_input(dict(trigger='internal_partial_glyph_coverage',uncovered_bbox_pdf=[0,10,15,30],
                region={'region_id':'a'},result={'spans':[span('住所测试地址',[0,10,150,30],'new',score)]}))
            with self.subTest(score=score):
                output,decision=adopt_region([old],e,lambda s:s)
                self.assertEqual(output,[old]);self.assertTrue(decision['conflicts'])
        for change in [{'input_size':None},{'input_size':[30,20]}]:
            e=complete_input(dict(trigger='internal_partial_glyph_coverage',uncovered_bbox_pdf=[0,10,15,30],
                region={'region_id':'a'},result={'spans':[span('住所测试地址',[0,10,150,30],'new')]}))
            e['result'].update(change)
            output,decision=adopt_region([old],e,lambda s:s)
            self.assertEqual(output,[old]);self.assertTrue(decision['conflicts'])

    def test_affine_boundary_uses_pixels_and_not_crop_region_polygon(self):
        from tempfile import TemporaryDirectory
        from PIL import Image,ImageDraw
        from regional_adoption import candidate_qualification
        with TemporaryDirectory() as tmp:
            image=Path(tmp)/'line.png'
            for touching in [False,True]:
                im=Image.new('L',(100,40),255);ImageDraw.Draw(im).rectangle([10,0 if touching else 5,80,30],fill=0);im.save(image)
                e={'result':{'input':{'image':str(image),'input_to_page':[[1,.3,10],[.2,1,20],[0,0,1]]},'input_size':[100,40]}}
                s=span('已知测试',[10,20,122,80],'x');s['coordinate_source']='crop_region'
                points=[[x+.3*y+10,.2*x+y+20] for x,y in [(5,5),(95,5),(95,35),(5,35)]]
                old=line('已知',[16.5,26,115.5,74],'old');old['polygon']=points
                reason,_=candidate_qualification(s,e,[old])
                self.assertEqual(bool(reason),touching)

    def test_address_tail_overlapping_next_label_and_scope_residual(self):
        ls=[line('注册地址：某省某市科技路1号',[70,450,510,478],'a'),line('产业园9幢1602室（自主申报）',[232,478,387,490],'b'),
            line('法定代表人：甲',[70,481,270,504],'c'),line('生产地址和生产范围：',[70,550,240,570],'d'),
            line('某市园区：口服溶液剂；',[70,574,440,584],'e'),line('仅限注册申报使用。',[70,587,220,599],'f')]
        ch=dict(page=1,lines=ls,layout_regions=[dict(region_id='a',label='text',order=1,bbox_pdf=[70,450,510,478]),
             dict(region_id='c',label='text',order=2,bbox_pdf=[70,481,270,504]),
             dict(region_id='footer',label='footer',order=3,bbox_pdf=[70,700,510,720])])
        assemble_chunk(ch);fields={e.get('label'):e for e in ch['readable_elements']}
        self.assertTrue(fields['注册地址']['value'].endswith('1602室（自主申报）'))
        self.assertEqual(set(fields['注册地址']['source_span_ids']),{'a','b'})
        self.assertEqual(fields['法定代表人']['value'],'甲')
        self.assertEqual(fields['生产地址和生产范围']['value'],'某市园区：口服溶液剂；仅限注册申报使用。')

    def test_target_edit_resolves_only_explicit_children_and_keeps_other_regions(self):
        a=line('经营范围：生产；仅限外用。',[10,10,300,30],'a');b=line('名称：乙企业',[10,60,180,80],'b')
        ch=dict(page=1,status='partial',lines=[a,b],words=deepcopy([a,b]),errors=[dict(code='ocr_candidate_conflict',reason='文字差异',bbox_pdf=a['bbox'],source_span_id='a')])
        assemble_chunk(ch);issues=review_issues(dict(doc_id='d',parse_status='partial'),'submission',[ch]);issue=issues[0]
        target=next(x for x in issues if x['issue_key']==issue['edit_target_key']);before=deepcopy(ch['readable_elements'][1])
        item=dict(issue_key=target['issue_key'],source_value=target['value'],action='edit_value',reason='已对原页',
                  text='经营范围：生产；禁止内服。',verified_value='经营范围：生产；禁止内服。',related_issues=[dict(issue_key=issue['issue_key'],reason='完整核对限制语')])
        updated,resolved,records=apply_targets([ch],issues,[item])
        self.assertIn(issue['issue_key'],resolved);self.assertEqual(updated[0]['readable_elements'][1],before)
        self.assertEqual(target_value(updated,target),item['text']);self.assertEqual(ch['lines'][0]['text'],a['text'])
        bad=deepcopy(item);bad['related_issues'][0]['issue_key']='unrelated'
        with self.assertRaises(ValueError):apply_targets([ch],issues,[bad])
        repeat=deepcopy(updated[0]);assemble_chunk(repeat,effective_revision=True)
        self.assertEqual(repeat['text'],updated[0]['text'])

    def test_overlay_target_does_not_replace_table_body(self):
        overlay=span('1010',[10,10,20,20],'seal',.6)
        cell=dict(row=0,column=0,rowspan=1,colspan=1,bbox_pdf=[0,0,200,80],text='某市生产地址',
                  result=dict(spans=[overlay]),role_evidence=[dict(span_id='seal',role='red_overlay')])
        ch=dict(page=1,status='partial',lines=[],tables=[dict(bbox_pdf=[0,0,200,80],cells=[cell],raw_rows=[['某市生产地址']])],
                errors=[dict(code='ocr_quality',reason='低分',source_span_id='seal',bbox_pdf=[10,10,20,20],table_index=0,cell_index=0)])
        assemble_chunk(ch);issues=review_issues(dict(doc_id='d',parse_status='partial'),'submission',[ch])
        issue=issues[0];target=next(t for t in issues if t['issue_key']==issue['edit_target_key'])
        self.assertEqual(target['target_kind'],'overlay')
        item=dict(issue_key=target['issue_key'],source_value='1010',action='edit_value',text='测试章',verified_value='测试章',
                  reason='合成章样例',related_issues=[dict(issue_key=issue['issue_key'],reason='已对该叠印')])
        updated,resolved,_=apply_targets([ch],issues,[item])
        self.assertEqual(updated[0]['tables'],ch['tables']);self.assertIn(issue['issue_key'],resolved)
        self.assertEqual(target_value(updated,target),'测试章')

    def test_manual_edit_envelope_is_not_a_new_glyph_height(self):
        ls=[line('注册地址：测试省测试市科技路1号',[100,100,500,130],'a'),line('测试园区二楼',[200,132,400,142],'b'),line('同高右栏监管信息',[535,144,640,156],'c')]
        ch=dict(page=1,lines=ls,words=deepcopy(ls),layout_regions=[
            dict(region_id='addr',label='text',order=0,bbox_pdf=[100,100,500,142]),
            dict(region_id='right',label='text',order=1,bbox_pdf=[535,144,640,156])])
        assemble_chunk(ch);before=deepcopy(ch['readable_elements']);issues=review_issues(dict(doc_id='d',parse_status='success'),'submission',[ch])
        target=next(i for i in issues if i.get('region_id')=='addr')
        item=dict(issue_key=target['issue_key'],source_value=target['value'],text=target['value'],verified_value=target['value'],action='confirm_value',reason='已知原文回归')
        output,_,_=apply_targets([ch],issues,[item])
        self.assertEqual(output[0]['text'],ch['text']);self.assertEqual(output[0]['readable_elements'][1],before[1])

    def test_date_is_not_borrowed_as_authority_name(self):
        ch=dict(lines=[line('登记机关',[100,100,180,118],'a'),line('2024年11月25日',[140,125,240,143],'b')],layout_regions=[
            dict(region_id='a',label='text',order=0,bbox_pdf=[100,100,180,118]),dict(region_id='b',label='text',order=1,bbox_pdf=[140,125,240,143])])
        assemble_chunk(ch);self.assertEqual(ch['readable_elements'][0]['value'],'');self.assertEqual(ch['readable_elements'][1]['text'],'2024年11月25日')

    def test_residual_caption_precedes_table_and_incomplete_label_keeps_its_tail(self):
        ls=[line('产线情况：',[40,20,100,35],'caption'),
            line('注册地测试省测试市科技路1号8',[20,300,300,320],'address'),
            line('楼802号',[130,321,185,332],'tail'),line('法定代表人：甲',[20,325,120,340],'person')]
        ch=dict(lines=ls,tables=[dict(bbox_pdf=[10,45,350,270],raw_rows=[['类型','生产线']])],layout_regions=[
            dict(region_id='table',label='table',order=0,bbox_pdf=[10,45,350,270]),
            dict(region_id='address',label='text',order=1,bbox_pdf=[20,300,300,320]),
            dict(region_id='person',label='text',order=2,bbox_pdf=[20,325,120,340])])
        assemble_chunk(ch)
        self.assertLess(ch['text'].index('产线情况'),ch['text'].index('| 类型'))
        address=next(e for e in ch['readable_elements'] if e['region_id']=='address')
        self.assertTrue(address['text'].endswith('8楼802号'));self.assertEqual(address['kind'],'paragraph')
        self.assertEqual(set(address['source_span_ids']),{'address','tail'})

    def test_background_requires_repeated_pale_image_evidence(self):
        from tempfile import TemporaryDirectory
        from PIL import Image,ImageDraw,ImageFont
        from background_text import separate_background
        with TemporaryDirectory() as tmp:
            for color in [195,0]:
                im=Image.new('RGB',(600,400),'white');d=ImageDraw.Draw(im);ls=[]
                font=ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',24)
                for i,(x,y) in enumerate([(20,20),(320,20),(20,200),(320,200)]):
                    d.text((x,y),'ABXYZ',font=font,fill=(color,color,color));ls.append(line('ABXYZ',list(d.textbbox((x,y),'ABXYZ',font=font)),str(i)))
                path=Path(tmp)/f'{color}.png';im.save(path)
                result={'input':{'image':str(path),'input_to_page':[[1,0,0],[0,1,0],[0,0,1]]},'input_size':[600,400]}
                output,overlays=separate_background(ls,result)
                self.assertEqual(len(overlays),4 if color==195 else 0)
                self.assertEqual(len(output),0 if color==195 else 4)

if __name__=='__main__':unittest.main()
