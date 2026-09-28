"""原始底图只服务无PDF叠加的局部，不以隐藏OCR文本作为候选。"""
import io
import sys
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path
import pymupdf as fitz
from PIL import Image, ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from imaging import original_page_raster
from bounded_review import field_value_input,title_input
from glyph_adjudication import input_evidence


class OriginalRasterFieldsTests(unittest.TestCase):
 def test_complete_title_supersedes_only_its_prior_region_attempt(self):
  from product_consumer import verified_region_supersedes
  entry={'region':{'region_id':'layout:4','bbox_pdf':[10,10,90,30]}}
  d=dict(accepted=True,kind='region',text='完整标题',input_evidence={'eligible':True},
         source_span_ids=['page:1','page:2'],target={'span_id':'layout:4:complete-title','polygon_page':[[10,10],[90,10],[90,30],[10,30]]},
         whole_sequence_review={'complete':True,'selected_sequence':'完整标题'})
  lines=[{'source_span_id':'page:1'},{'source_span_id':'page:2'}]
  self.assertIs(verified_region_supersedes(entry,[d],lines),d)
  for altered in [dict(d,accepted=False),dict(d,whole_sequence_review={'complete':False}),
                  dict(d,input_evidence={'eligible':False}),dict(d,source_span_ids=['other-page:1'])]:
   self.assertIsNone(verified_region_supersedes(entry,[altered],lines))
  other=deepcopy(entry);other['region']['region_id']='layout:5'
  self.assertIsNone(verified_region_supersedes(other,[d],lines))
  outside=deepcopy(entry);outside['region']['bbox_pdf'][2]=100
  self.assertIsNone(verified_region_supersedes(outside,[d],lines))

 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.doc=fitz.open();self.addCleanup(self.doc.close);self.page=self.doc.new_page(width=200,height=100)
  im=Image.new('RGB',(200,100),'white');ImageDraw.Draw(im).rectangle((50,40,70,55),fill='black')
  buf=io.BytesIO();im.save(buf,format='PNG');self.page.insert_image(self.page.rect,stream=buf.getvalue())
  self.page.get_pixmap(matrix=fitz.Matrix(3,3)).save(self.root/'render.png')
 def source(self,native):
  return dict(sample_id='page',input_size=[600,300],input=dict(image=str(self.root/'render.png'),input_to_page=[[1/3,0,0],[0,1/3,0],[0,0,1]],original_raster=native),spans=[])
 def test_hidden_text_does_not_become_recognition_content(self):
  self.page.insert_text((20,20),'WRONG HIDDEN ANSWER',render_mode=3)
  n=original_page_raster(self.page,self.root/'native.png')
  self.assertIsNotNone(n);self.assertFalse(n['hidden_text_used']);self.assertEqual(n['excluded_overlay_bboxes'],[])
  self.assertNotIn('WRONG',str(n))
 def test_overlay_outside_target_does_not_disable_original_pixels(self):
  self.page.draw_line((0,90),(190,90),color=(1,0,0))
  n=original_page_raster(self.page,self.root/'native.png')
  g=field_value_input(self.source(n),dict(span_id='value',polygon_page=[[48,38],[74,38],[74,58],[48,58]]),self.root/'value.png')
  self.assertEqual(g['source_sampling'],'original_page_raster')
 def test_overlapping_visible_text_forces_full_render(self):
  self.page.insert_text((48,50),'VISIBLE',fontsize=12)
  self.page.get_pixmap(matrix=fitz.Matrix(3,3)).save(self.root/'render.png')
  n=original_page_raster(self.page,self.root/'native.png')
  g=field_value_input(self.source(n),dict(span_id='value',polygon_page=[[48,38],[74,38],[74,58],[48,58]]),self.root/'value.png')
  self.assertEqual(g['source_sampling'],'page_render')
 def test_rotation_keeps_native_pixel_to_page_mapping(self):
  self.page.set_rotation(90)
  n=original_page_raster(self.page,self.root/'native.png')
  self.assertIsNotNone(n);m=n['input_to_page']
  self.assertAlmostEqual(m[0][0]*50+m[0][1]*40+m[0][2],60)
  self.assertAlmostEqual(m[1][0]*50+m[1][1]*40+m[1][2],50)
 def test_multiple_visible_rasters_are_not_flattened_by_omission(self):
  self.page.insert_image(fitz.Rect(100,0,200,100),filename=str(self.root/'render.png'))
  self.assertIsNone(original_page_raster(self.page,self.root/'native.png'))
 def layered_title(self,color,stamp=True):
  self.page.draw_circle((60,48),12,color=color,fill=color,fill_opacity=.5)
  pix=self.page.get_pixmap(matrix=fitz.Matrix(3,3));pix.save(self.root/'render.png')
  n=original_page_raster(self.page,self.root/'native.png',dict(scale=3))
  primary=self.source(n);primary['input']['scale']=3
  page=dict(page_result=primary,stamp_regions=[dict(source='closed_red_circular_outline',polygon_page=[[45,33],[75,33],[75,63],[45,63]])] if stamp else [])
  target=dict(span_id='title',polygon_page=[[35,25],[95,25],[95,70],[35,70]])
  return title_input(page,target,self.root/'title.png')
 def test_red_layer_is_source_evidence_not_seal_text_confirmation(self):
  g=self.layered_title((1,0,0))
  self.assertIn('raster_layer_evidence',g)
  self.assertFalse(g['raster_layer_evidence']['seal_text_verified'])
 def test_black_overlay_cannot_reveal_old_text_as_final(self):
  self.assertNotIn('raster_layer_evidence',self.layered_title((0,0,0)))
 def test_white_erasure_cannot_reveal_old_text_as_final(self):
  self.assertNotIn('raster_layer_evidence',self.layered_title((1,1,1)))
 def test_missing_same_page_seal_cannot_accept_red_layer(self):
  self.assertNotIn('raster_layer_evidence',self.layered_title((1,0,0),stamp=False))
 def neighbor_input(self):
  path=self.root/'neighbor.png';im=Image.new('RGB',(100,60),'white')
  d=ImageDraw.Draw(im);d.rectangle((35,6,43,40),fill='black');im.save(path)
  geometry=dict(source_span_id='target',padding_px=8,target_polygon_input_px=[[25,20],[55,20],[55,45],[25,45]],
      detected_neighbors=[dict(source_span_id='previous-line',polygon_input_px=[[25,0],[55,0],[55,14],[25,14]])])
  return path,geometry
 def test_connected_neighbor_ink_at_edge_does_not_clip_target(self):
  path,g=self.neighbor_input();e=input_evidence(path,metadata=g)
  self.assertTrue(e['eligible']);self.assertIn('neighbor_boundary_evidence',e)
 def test_missing_neighbor_proof_keeps_cut_unresolved(self):
  path,g=self.neighbor_input();g['detected_neighbors']=[]
  self.assertFalse(input_evidence(path,metadata=g)['eligible'])
 def test_neighbor_cannot_excuse_pixels_cut_inside_target(self):
  path,g=self.neighbor_input();g['target_polygon_input_px']=[[25,6],[55,6],[55,45],[25,45]]
  self.assertFalse(input_evidence(path,metadata=g)['eligible'])
 def test_retained_value_competition_uses_only_actual_weak_position_alternatives(self):
  from bounded_review import complete_weak_value_competition
  response=dict(image='actual.png',source_geometry={},sequence_evidence={'nbest':['甲乙','丙乙','甲丁']})
  decision=dict(accepted=True,text='甲乙',reason='retained_support_low',input_evidence={'eligible':True},
    whole_sequence_review={'complete':False,'selected_alignment':[{'posterior':.91},{'posterior':.99}],
      'primary_selected_alignment':[{'posterior':.96},{'posterior':.99}]})
  class Aux:
   def predict(self,image,candidates,geometry):
    self.candidates=candidates;return dict(image=image,source_geometry=geometry)
  aux=Aux()
  with patch('bounded_review.complete_sequence_reviews'),patch('sequence_adjudication.decide_sequence',return_value={'accepted':False}) as decide:
   revised,out=complete_weak_value_competition({},'value',response,decision,None,aux)
  self.assertEqual(aux.candidates,['甲乙','丙乙']);self.assertFalse(out['accepted'])
 def test_recovered_title_extent_does_not_reorder_other_objects(self):
  from assembly import assemble_chunk
  original=dict(layout_regions=[dict(region_id='title',order=0,label='doc_title',bbox_pdf=[0,0,160,25]),
      dict(region_id='middle',order=1,label='text',bbox_pdf=[0,30,40,50]),
      dict(region_id='body',order=2,label='text',bbox_pdf=[80,70,160,90])],tables=[],lines=[
      dict(source_span_id='a',text='标题',bbox=[10,10,30,20]),
      dict(source_span_id='b',text='其他',bbox=[10,32,30,45]),
      dict(source_span_id='c',text='正文',bbox=[80,72,150,85]),
      dict(source_span_id='d',text='残余',bbox=[90,40,120,55])])
  changed=deepcopy(original);changed['lines'][0].update(text='完整标题',bbox=[10,10,150,24],ordering_bbox=[10,10,30,20])
  assemble_chunk(original);assemble_chunk(changed)
  self.assertEqual([e['region_id'] for e in original['readable_elements']],[e['region_id'] for e in changed['readable_elements']])


if __name__=='__main__':unittest.main()
