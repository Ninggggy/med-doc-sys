"""原图正文复核与独立章风险；机制测试不冒充真实识别正确率。"""
import sys,unittest,tempfile,copy
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from PIL import Image,ImageDraw
from cell_adjudication import decide_cell
from bounded_review import sequence_assessments
class MixedBodyConfirmation(unittest.TestCase):
 def test_preprocessed_red_edge_is_not_body_cut_but_black_edge_still_is(self):
  from glyph_adjudication import input_evidence
  from PIL import ImageOps
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);raw=root/'source.png';view=root/'view.png'
   geometry=dict(preprocessing='neutral_preserving',pixel_preservation={'eligible':True},
       original_source_input={'image':str(raw)},source_bbox_px=[0,0,100,50],padding_px=8,
       target_polygon_input_px=[[12,12],[102,12],[102,52],[12,52]])
   for color,expected in [((100,20,20),True),((30,30,30),False)]:
    im=Image.new('RGB',(100,50),'white');draw=ImageDraw.Draw(im)
    draw.rectangle([0,15,25,30],fill=color);draw.rectangle([40,15,75,32],fill='black');im.save(raw)
    ImageOps.expand(im.getchannel('R').convert('RGB'),8,fill='white').save(view)
    self.assertEqual(input_evidence(view,metadata=geometry)['eligible'],expected)
 def fixture(self,root):
  path=Path(root)/'cell.png';im=Image.new('RGB',(240,90),'white');d=ImageDraw.Draw(im);d.rectangle([80,25,210,45],fill='black');d.rectangle([12,10,40,25],fill='red');im.save(path)
  def span(sid,text,b,score):return dict(span_id=sid,text_raw=text,score_raw=score,score_kind='paddle_rec_score',coordinate_source='engine_polygon',polygon_page=[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]],polygon_input_px=[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]])
  body=span('body','完整正文',[76,21,215,50],.99);seal=span('seal','编号',[10,8,44,28],.2)
  raw=dict(status='completed',input_size=[240,90],input={'image':str(path),'input_to_page':[[1,0,0],[0,1,0],[0,0,1]]},spans=[body,seal],text_assembled='完整正文\n编号')
  cell=dict(result={**copy.deepcopy(raw),'body_spans':[body],'text_assembled':'完整正文'},original_result=raw,content_candidates=[])
  response=dict(status='completed',text='完整正文',sequence_response={'candidate_texts':['完整正文'],'sequence_evidence':{},'source_geometry':{}})
  decision=dict(accepted=True,text='完整正文',whole_sequence_review={'complete':True},verification_scope={'kind':'whole_object'})
  return cell,{(-1,0):response},decision,path,span
 def test_original_body_can_finish_without_discarding_seal(self):
  with tempfile.TemporaryDirectory() as t:
   cell,responses,decision,_,_=self.fixture(t)
   with patch('sequence_adjudication.decide_sequence',return_value=decision):d=decide_cell(cell,responses)
   self.assertTrue(d['resolved']);self.assertEqual(d['evidence']['arm_index'],-1)
   self.assertEqual([s['span_id'] for s in d['result']['spans']],['body','seal'])
   self.assertEqual(d['result']['spans'][1]['text_raw'],'编号')
 def test_partial_sequence_cannot_complete_original(self):
  with tempfile.TemporaryDirectory() as t:
   cell,responses,decision,_,_=self.fixture(t);decision['whole_sequence_review']['complete']=False
   with patch('sequence_adjudication.decide_sequence',return_value=decision):d=decide_cell(cell,responses)
   self.assertFalse(d['accepted'])
 def test_tiny_required_date_is_not_allowed_to_hide_under_coverage_ratio(self):
  with tempfile.TemporaryDirectory() as t:
   cell,responses,decision,path,span=self.fixture(t);im=Image.open(path);ImageDraw.Draw(im).rectangle([220,65,223,69],fill='black');im.save(path)
   date=span('date','日期',[218,63,226,72],.99);cell['original_result']['spans'].append(date);cell['result']['spans'].append(date);cell['result']['body_spans'].append(date)
   with patch('sequence_adjudication.decide_sequence',return_value=decision):d=decide_cell(cell,responses)
   self.assertFalse(d.get('resolved',False));self.assertFalse(d['choices'][0]['complete'])
 def test_body_completion_does_not_clear_seal_quality_or_another_cell(self):
  d=dict(kind='cell',accepted=True,resolved=True,table_index=0,cell_index=1)
  issues=[dict(code='ocr_overlay_conflict',table_index=0,cell_index=1),dict(code='ocr_quality',source_span_id='seal',table_index=0,cell_index=1),dict(code='ocr_overlay_conflict',table_index=0,cell_index=2)]
  assessed=sequence_assessments({},dict(automatic_adjudications=[d]),issues)
  self.assertIsNotNone(assessed[0][1]);self.assertIsNone(assessed[1][1]);self.assertIsNone(assessed[2][1])
if __name__=='__main__':unittest.main()
