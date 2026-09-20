// 只读展示原始证据；不计算解析是否通过、不修改人工草稿。
export function recoveryEvidence(chunk, code) {
  chunk = chunk && typeof chunk === 'object' ? chunk : {};
  if (!['ocr_quality', 'ocr_coverage'].includes(code)) return [];
  const reasons = {
    recovery_region_limit: '达到本轮补识别区域上限，尚未处理',
    recovery_budget_exhausted: '执行预算已用尽，尚未处理',
    recovery_budget_unsupported: '服务不支持受控执行预算',
    recovery_context_ambiguous: '文字上下文或单元格归属不明确',
    recovery_conflict: '候选与已有文字冲突，未自动覆盖',
    recovery_reflow_review_required: '窄列重排候选尚未确认，未写入正文或用于自动判定',
    recovery_failed: '局部复核失败，需核对或重试',
    recovery_coverage_failed: '覆盖检查失败'
  };
  const valid = box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite) && box[2] > box[0] && box[3] > box[1];
  const words = Array.isArray(chunk.words) ? chunk.words : [];
  const attempts = Array.isArray(chunk.ocr_recovery) ? chunk.ocr_recovery : [];
  const texts = values => (Array.isArray(values) ? values : []).filter(w => w && typeof w === 'object').map(w => String(w.text || '')).join(' ');
  const embedded = (Array.isArray(chunk.embedded_ocr_evidence) ? chunk.embedded_ocr_evidence : [])
    .filter(item => item && (item.trigger || 'ocr_quality') === code)
    .map((item,index) => ({
      index:`embedded-${index}`, box:valid(item.bbox_pdf) ? item.bbox_pdf.slice() : null,
      location:valid(item.bbox_pdf) ? item.bbox_pdf.map(n=>Math.round(n*10)/10).join(', ') : '未提供可靠坐标',
      status:({matched:'新旧一致',revalidated:'原图复核一致',conflict:'新旧文字冲突',missing:'旧层文字尚未由图像识别恢复'})[item.status] || '待核验',
      reason:item.reason || '旧文本只作证据，不能以旧层内容代替图像核验',
      embedded:String(item.embedded_text || ''), primary:[String(item.primary_text || ''),
        texts(item.ambiguous_primary_words) ? `跨格未采用候选：${texts(item.ambiguous_primary_words)}` : ''].filter(Boolean).join('\n'),
      candidates:texts(item.recovery_candidates), sharedCandidates:'',sharedBox:null,
      cellCandidates:[],fragmentCandidates:[],reused:false
    }));
  return embedded.concat(attempts.map((item,index) => ({item,index}))
    .filter(({item}) => item && typeof item === 'object' && (item.trigger || 'ocr_coverage') === code)
    .map(({item,index}) => {
      const source = item.quality_bbox_pdf || item.uncovered_bbox_pdf || item.bbox_pdf;
      const box = valid(source) ? source.slice() : null;
      const primary = box ? words.filter(word => word && valid(word.bbox) &&
        Math.min(word.bbox[2],box[2]) > Math.max(word.bbox[0],box[0]) &&
        Math.min(word.bbox[3],box[3]) > Math.max(word.bbox[1],box[1])) : [];
      const sharedIndex = item.reused_attempt_index;
      const shared = item.candidate_scope === 'issue_region' && Number.isInteger(sharedIndex) && sharedIndex >= 0 && sharedIndex < index ? attempts[sharedIndex] : null;
      const cellCandidates = (Array.isArray(item.ambiguous_cell_candidates) ? item.ambiguous_cell_candidates : [])
        .filter(entry => entry && typeof entry === 'object').map(entry => ({text:String(entry.text || ''),
          boxes:(Array.isArray(entry.candidate_cell_bboxes) ? entry.candidate_cell_bboxes : []).filter(valid).map(b => b.slice())}));
      return {
        index, box, location: box ? box.map(n => Math.round(n*10)/10).join(', ') : '未提供可靠坐标',
        status: ({not_attempted:'未处理',attempted:'已尝试',unresolved:'待核对',recovered:'已补入候选，仍以本页诊断为准'})[item.status] || '状态待核查',
        reason: reasons[item.reason_code] || '请对照原图核对，不代表内容已完整',
        primary: primary.map(w => String(w.text || '')).join(' '),
        candidates: texts(item.candidates),
        sharedCandidates: shared ? texts(shared.candidates) : '',
        sharedBox: shared && valid(shared.bbox_pdf) ? shared.bbox_pdf.slice() : null,
        cellCandidates,
        fragmentCandidates: (Array.isArray(item.candidates) ? item.candidates : [])
          .filter(candidate => candidate && Array.isArray(candidate.source_bboxes_pdf))
          .map(candidate => ({text:String(candidate.text || ''),
            boxes:candidate.source_bboxes_pdf.filter(valid).map(box => box.slice()),
            incomplete:!candidate.source_bboxes_pdf.length || candidate.source_bboxes_pdf.some(box => !valid(box))})),
        reused: Number.isInteger(item.reused_attempt_index)
      };
    }));
}
