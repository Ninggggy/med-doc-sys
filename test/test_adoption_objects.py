import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from adoption_replay import candidate_objects,qualified_projection
class Qualification(unittest.TestCase):
 def result(self):
  return dict(status='completed',input_size=[400,80],input={'input_to_page':[[1,0,0],[0,1,0],[0,0,1]]},spans=[dict(span_id='address',text_raw='测试市科技路18号',score_raw=.99,score_kind='paddle_rec_score',coordinate_source='engine_polygon',polygon_page=[[10,10],[200,10],[200,30],[10,30]]),dict(span_id='empty',text_raw='',score_raw=0,score_kind='paddle_rec_score',polygon_page=[[210,10],[230,10],[230,30],[210,30]])])
 def test_empty_object_not_whole_failure(self):
  p=qualified_projection(self.result());self.assertEqual(p['text_assembled'],'测试市科技路18号');self.assertEqual(len(p['object_qualification']),2);self.assertFalse(p['object_qualification'][1]['eligible'])
 def test_failed_request_no_eligible_objects(self):
  p=self.result();p['status']='failed';self.assertFalse(any(o['eligible'] for o in candidate_objects(p)))
 def test_one_low_arm_does_not_reject_other(self):
  a=self.result();b=self.result();b['spans'][0]['score_raw']=.79648
  self.assertTrue(candidate_objects(a)[0]['eligible']);self.assertFalse(candidate_objects(b)[0]['eligible'])
 def test_eligibility_does_not_authorize_replacement(self):
  p=qualified_projection(self.result());self.assertNotIn('adopted',p)
if __name__=='__main__':unittest.main()
