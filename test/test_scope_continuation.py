import unittest
from agent.agent_backend.services.filing_change_extraction_service import extract_document


class ScopeContinuationTests(unittest.TestCase):
    def test_bare_joint_label_and_overlapping_line_do_not_drop_field(self):
        lines=[{'text':'生产地址和生产范围','bbox':[10,10,110,30],'block_id':0},
               {'text':'甲地一号：甲（仅限研究）','bbox':[10,34,210,44],'block_id':0},
               {'text':'签','bbox':[220,33,232,49],'block_id':0},
               {'text':'乙（不得销售）','bbox':[10,47,200,58],'block_id':0},
               {'text':'有效期至','bbox':[10,90,80,110],'block_id':0}]
        fact=self.scopes([{'text':'\n'.join(x['text'] for x in lines),'lines':lines}])[0]
        self.assertEqual(fact['raw_value'],'甲地一号：甲（仅限研究）\n乙（不得销售）')
        self.assertEqual(fact['joint_block']['source']['internal_unresolved'][0]['text'],'签')

    def scopes(self, chunks):
        return [f for f in extract_document('实际输入.pdf', chunks)['facts'] if f['field']=='production_scope']

    def test_restrictions_survive_and_next_field_stops(self):
        facts=self.scopes([{'text':'药品生产许可证\n生产地址：甲地\n生产范围：原料药（甲）\n仅限注册申报使用，不得销售，不含乙。\n有效期至：2027年\n下一字段内容'}])
        self.assertEqual(len(facts),1)
        self.assertEqual(facts[0]['raw_value'],'原料药（甲）\n仅限注册申报使用，不得销售，不含乙。')
        self.assertIn('不得销售',str(facts[0]['scope_entries']))
        self.assertEqual(facts[0]['production_address_evidence'][0]['raw_value'],'甲地')

    def test_next_address_and_unlisted_heading_stop(self):
        facts=self.scopes([{'text':'生产地址：甲地\n生产范围：甲\n仅限研究\n生产地址：乙地\n生产范围：乙\n不得销售\n签发机关：某局'}])
        self.assertEqual([f['raw_value'] for f in facts],['甲\n仅限研究','乙\n不得销售'])
        self.assertEqual([f['production_address_evidence'][0]['raw_value'] for f in facts],['甲地','乙地'])

    def test_columns_and_chunk_boundaries(self):
        lines=[{'text':'生产范围：甲','bbox':[0,0,100,10]}, {'text':'不得销售','bbox':[200,12,290,22]}]
        f=self.scopes([{'text':'\n'.join(l['text'] for l in lines),'lines':lines},{'text':'不含乙'}])
        self.assertEqual(f[0]['raw_value'],'甲')

    def test_real_cell_multiline_and_addresses(self):
        rows=[['生产地址','生产范围'],['甲地','甲\n仅限注册；乙（不含丙）'],['乙地','丁\n不得销售']]
        f=self.scopes([{'page':1,'tables':[{'raw_rows':rows,'table_index':1}]}])
        self.assertEqual(len(f),2)
        self.assertIn('仅限注册',f[0]['raw_value'])
        self.assertNotIn('丁',f[0]['raw_value'])
        self.assertEqual(f[1]['production_address_evidence'][0]['raw_value'],'乙地')

    def test_source_is_not_rewritten(self):
        x=extract_document('x',[{'text':'生产范围：甲\n仅限研究'}])
        self.assertEqual([s['text'] for s in x['source_statements']],['生产范围：甲','仅限研究'])

    def test_zero_index_table_address_stays_in_its_row(self):
        f=self.scopes([{'page':1,'tables':[{'table_index':0,'raw_rows':[
            ['生产地址','生产范围'],['甲地','甲（仅限注册）'],['乙地','乙（不得销售）']]}]}])
        self.assertEqual([x['production_address_evidence'][0]['raw_value'] for x in f],['甲地','乙地'])

    def test_unnumbered_tables_do_not_share_address(self):
        f=self.scopes([{'page':1,'tables':[
            {'raw_rows':[['生产地址','生产范围'],['甲地','甲']]},
            {'raw_rows':[['生产地址','生产范围'],['乙地','乙']]}]}])
        self.assertEqual([x['production_address_evidence'][0]['raw_value'] for x in f],['甲地','乙地'])

    def test_combined_scope_retains_but_does_not_guess_addresses(self):
        f=self.scopes([{'text':'生产地址和生产范围：\n受托生产企业：甲企业，一街1号，甲；乙企业，\n二街2号，乙：丙企业，三街3号，丙\n仅限注册，不得销售\n发证机关：某局'}])[0]
        self.assertIn('仅限注册，不得销售',f['raw_value'])
        self.assertNotIn('某局',f['raw_value'])
        self.assertEqual(f['attribution_status'],'combined_address_scope_requires_review')

    def test_blank_gap_and_page_boundary_stop(self):
        facts=self.scopes([{'page':1,'text':'生产范围：甲\n\n不得销售'},{'page':2,'text':'不含乙'}])
        self.assertEqual(facts[0]['raw_value'],'甲')

    def test_explicit_entries_keep_restriction_with_own_item(self):
        f=self.scopes([{'text':'生产范围：甲（仅限注册）；乙（不含丙）\n不得销售'}])[0]
        self.assertEqual(len(f['scope_entries']),2)
        self.assertNotIn('不含丙',f['scope_entries'][0]['raw_value'])
        self.assertIn('不得销售',f['scope_entries'][1]['raw_value'])
        self.assertEqual(f['scope_entries'][1]['attribution'],'requires_review')
        self.assertIn('restriction_target_not_explicit',f['scope_entries'][1]['unresolved'])

    def test_standalone_restriction_after_multiple_items_is_not_guessed(self):
        f=self.scopes([{'text':'生产范围：甲；乙；不得销售'}])[0]
        self.assertEqual(f['raw_value'],'甲；乙；不得销售')
        self.assertEqual(f['scope_entries'][-1]['attribution'],'requires_review')

if __name__=='__main__': unittest.main()
