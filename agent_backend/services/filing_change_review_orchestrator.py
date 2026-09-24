from copy import deepcopy
from agent.agent_backend.services.filing_parse_readiness import effective_parse_ready
from agent.agent_backend.llm.errors import LLMContextError, LLMExecutionError
from agent.agent_backend.services.filing_change_result_service import aggregate_result
from typing import Any, Dict, List

from agent.agent_backend.services.filing_change_evidence_service import FilingChangeEvidenceService
from agent.agent_backend.services.filing_change_extraction_service import FilingChangeExtractionService
from agent.agent_backend.services.filing_change_llm_gate_service import FilingChangeLLMGateService
from agent.agent_backend.services.filing_change_llm_reasoning_service import FilingChangeLLMReasoningService
from agent.agent_backend.services.filing_change_rule_review_service import FilingChangeRuleReviewService
from agent.agent_backend.services.filing_change_technical_assessment_service import FilingChangeTechnicalAssessmentService


class FilingChangeReviewOrchestrator:
    def __init__(self, form_parser: Any, quality_service: Any, stability_service: Any, *, enable_language_summary: bool = True) -> None:
        self.form_parser = form_parser
        self.extraction = FilingChangeExtractionService()
        self.rule_review = FilingChangeRuleReviewService()
        self.technical = FilingChangeTechnicalAssessmentService(quality_service=quality_service, stability_service=stability_service)
        self.evidence = FilingChangeEvidenceService()
        self.llm_gate = FilingChangeLLMGateService()
        self.enable_language_summary = enable_language_summary
        self.llm_reasoning = FilingChangeLLMReasoningService() if enable_language_summary else None

    def run(
        self,
        project_id: str,
        application_form: Dict[str, Any],
        submissions: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        references: List[Dict[str, Any]],
        parsed_submission_map: Dict[str, Any],
        completeness: Dict[str, Any],
    ) -> Dict[str, Any]:
        form_json = (application_form or {}).get("form_json", {}) if isinstance(application_form, dict) else {}
        form_json = self.form_parser.normalize_form_json(form_json if isinstance(form_json, dict) else {})
        extracted = self.extraction.run(
            application_form={"form_json": form_json},
            submissions=submissions or [],
            parsed_submission_map=parsed_submission_map or {},
        )
        submissions = extracted.get('submissions', submissions)
        technical = self.technical.run(form_json=form_json, submissions=submissions or [], rules=[], project_id=project_id)
        review = self.rule_review.run(extracted=extracted, submissions=submissions or [], rules=rules or [],
                                      completeness=completeness or {}, stability=technical.get('stability_trend_analysis', {}),
                                      quality=technical.get('quality_standard_check', {}))
        merged_rules = review.get('matched_rules', [])
        evidence_block = self.evidence.collect(
            submissions=submissions or [],
            references=references or [],
            matched_rules=review.get("rule_results", []),
            module_results={**review, **technical},
            parsed_submission_map=parsed_submission_map or {},
            form_json=form_json,
        )
        llm_calls = []

        risk_points = []
        risk_points.extend((technical.get("technical_risk_points", []) if isinstance(technical, dict) else []))
        risk_points.extend((review.get("consistency_check", {}) or {}).get("inconsistent_items", []))
        manual_review_items = [r.get('rule_code','') + '：' + r.get('review_conclusion','') for r in merged_rules]

        parse_issues = list((review.get('formal_review') or {}).get('parse_issues', []))
        form_attempt = (application_form or {}).get('latest_attempt') or {}
        if not effective_parse_ready(application_form or {}):
            parse_issues.append({'file_name': '申请表', 'status': form_attempt.get('content_status') or (application_form or {}).get('parse_status', 'not_parsed'),
                                 'message': form_attempt.get('message', '申请表尚未完整解析'), 'latest_attempt': form_attempt})
        review.setdefault('formal_review', {})['parse_issues'] = parse_issues
        review['formal_review']['parse_resolution'] = {
            'application_form': deepcopy((application_form or {}).get('parse_resolution') or {}),
            'submissions': [{'doc_id': s.get('doc_id'), **deepcopy(s['parse_resolution'])}
                            for s in submissions or [] if s.get('parse_resolution')]}
        if parse_issues:
            review['formal_review']['result'] = '需人工确认'
        for issue in parse_issues:
            manual_review_items.append(f"{issue.get('file_name') or issue.get('doc_id')}: {issue['message']} 请先核对原件或重试解析。")
        if (review.get("formal_review", {}) or {}).get("result") != "通过":
            manual_review_items.append("资料完整性或字段一致性未通过")
        if bool((technical.get("change_identification", {}) or {}).get("need_manual_review", False)):
            manual_review_items.append("变更事项识别需人工确认")
        final = aggregate_result(review, technical, rules or [])
        overall_result = final['overall_conclusion']['result']
        manual_review_items = [x['module'] + '：' + x['reason'] for x in final['issues']]
        correction_items = [{'issue': x['reason'], 'basis': x['module'],
                             'required_supplement': x['required_action'], 'evidence_refs': x['evidence'],
                             'kind': x['kind']} for x in final['issues']]
        correction_notice = {'title': '补正与人工核查要求草稿', 'items': correction_items,
                             'draft_text': final['recommended_action']}
        review_report_draft = {
            "report_title": "药品备案变更审评报告草稿",
            "sections": [
                {
                    "name": "一、总体审评建议",
                    "conclusion": overall_result,
                    "evidence_refs": evidence_block.get("evidence_refs", []),
                },
                {
                    "name": "二、资料形式审查",
                    "conclusion": review.get("formal_review", {}),
                    "evidence_refs": evidence_block.get("module_evidence", {}).get("formal_review", []),
                },
                {
                    "name": "三、一致性核验",
                    "conclusion": review.get("consistency_check", {}),
                    "evidence_refs": evidence_block.get("module_evidence", {}).get("consistency_check", []),
                },
                {
                    "name": "四、变更事项与管理类别建议",
                    "conclusion": technical.get("change_category_suggestion", {}),
                    "evidence_refs": evidence_block.get("module_evidence", {}).get("change_category_suggestion", []),
                },
                {
                    "name": "五、质量标准比对",
                    "conclusion": technical.get("quality_standard_check", {}),
                    "evidence_refs": evidence_block.get("module_evidence", {}).get("quality_standard_check", []),
                },
                {
                    "name": "六、稳定性趋势分析",
                    "conclusion": technical.get("stability_trend_analysis", {}),
                    "evidence_refs": evidence_block.get("module_evidence", {}).get("stability_trend_analysis", []),
                },
                {
                    "name": "七、风险点与需人工确认事项",
                    "conclusion": {"risk_points": risk_points, "manual_review_items": manual_review_items},
                    "evidence_refs": evidence_block.get("evidence_refs", []),
                },
            ],
            "ai_preliminary_conclusion": overall_result,
            "manual_review_placeholder": "【审评员复核意见】",
        }

        # 模型只能安排完整事实句的顺序，不能重写数值、状态或引用。
        overall_summary = {**final['overall_conclusion'], 'overall_result': overall_result}
        clauses = final['summary_points']
        gate = self.llm_gate.should_call_llm(scene='overall_summary', context={'risk_points': len(clauses)})
        if not getattr(self, 'enable_language_summary', True):
            gate = {**gate, 'allow': False, 'reason': '本地实验显式使用确定性审评及事实顺序，未执行语言模型综合解释'}
        call = self.llm_gate.build_call_record('overall_summary', gate, '本轮已确定事实的解释顺序')
        call['status'] = 'not_called'
        if gate.get('allow'):
            try:
                response = self.llm_reasoning.summarize_overall({'facts': clauses, 'overall_result': overall_result})
                order = response.get('explanation_order') if isinstance(response, dict) else None
                if (not isinstance(response, dict) or set(response) != {'explanation_order'}
                        or not isinstance(order, list) or any(type(i) is not int for i in order)
                        or sorted(order) != list(range(len(clauses)))):
                    raise LLMExecutionError('model_invalid_output', stage='filing_overall_summary')
                overall_summary['summary'] = '；'.join(clauses[i] for i in order)
                call['status'] = 'completed'
            except LLMContextError as exc:
                # 预算/模型窗口错误不是可用的解释结果，交给任务层明确结束。
                exc.stage = 'filing_overall_summary'
                exc.partial_result = {
                    'formal_review': review.get('formal_review', {}),
                    'consistency_check': review.get('consistency_check', {}),
                    'quality_standard_check': technical.get('quality_standard_check', {}),
                    'stability_trend_analysis': technical.get('stability_trend_analysis', {}),
                    'rule_results': review.get('rule_results', []),
                    'evidence_refs': evidence_block.get('evidence_refs', []),
                }
                raise
            except Exception as exc:
                call.update(status='fallback',
                            reason='解释排序未完成，使用原始事实顺序；不改变已完成的规则检查结论。',
                            reason_code=exc.code if isinstance(exc, LLMExecutionError) else 'model_call_failed')
        llm_calls.append(call)
        final['overall_conclusion']['summary'] = overall_summary['summary']
        review_report_draft['sections'][0]['conclusion'] = overall_result + '；' + overall_summary['summary']
        review_report_draft['ai_preliminary_conclusion'] = overall_result
        review_report_draft['sections'].insert(1, {'name': '技术支持与处理建议', 'conclusion':
            final['technical_support']['status'] + '；' + final['recommended_action']})
        overall_evidence_chain = self._build_overall_evidence_chain(
            category_suggestion=technical.get("change_category_suggestion", {}),
            evidence_refs=evidence_block.get("evidence_refs", []),
            matched_rules=merged_rules,
        )
        correction_notice_markdown = self._build_correction_notice_markdown(correction_notice)
        review_report_markdown = self._build_review_report_markdown(
            project_id=project_id,
            overall_summary=overall_summary,
            review_report_draft=review_report_draft,
            matched_rules=merged_rules,
            risk_points=risk_points,
            manual_review_items=manual_review_items,
            evidence_refs=evidence_block.get("evidence_refs", []),
        )

        complete_rule_lines = ['\n## 完整规则执行明细']
        for rule in review.get('rule_results', []):
            complete_rule_lines.append(f"\n### {rule['rule_code']} {rule.get('rule_name', '')}\n执行：{rule['execution_status']}；结论：{rule.get('status') or '无业务结论'}")
            for sub in rule.get('subchecks', []):
                complete_rule_lines.append(f"- {sub['name']}：{sub['status']}。{sub['reason']}")
        review_report_markdown += '\n'.join(complete_rule_lines)
        if call['status'] == 'fallback':
            review_report_markdown += '\n\n## 非关键步骤降级说明\n' + call['reason']
            review_report_draft['sections'].append({'name': '非关键步骤降级说明', 'conclusion': call['reason']})
        return {
            **final,
            "project_id": project_id,
            "task_type": "extend_validity_period",
            "overall_conclusion": {
                "result": overall_summary.get("overall_result", overall_result),
                "summary": overall_summary.get("summary", ""),
                "need_manual_review": bool(overall_summary.get("need_manual_review", bool(manual_review_items))),
                "evidence_chain": overall_evidence_chain,
            },
            "formal_review": review.get("formal_review", {}),
            "field_check": review.get("field_check", {}),
            "consistency_check": review.get("consistency_check", {}),
            "change_identification": technical.get("change_identification", {}),
            "change_category_suggestion": technical.get("change_category_suggestion", {}),
            "quality_standard_check": technical.get("quality_standard_check", {}),
            "stability_trend_analysis": technical.get("stability_trend_analysis", {}),
            "risk_points": risk_points,
            "manual_review_items": manual_review_items,
            "correction_notice_draft": correction_notice,
            "correction_notice_markdown": correction_notice_markdown,
            "review_report_draft": review_report_draft,
            "review_report_markdown": review_report_markdown,
            "matched_rules": merged_rules,
            "rule_results": review.get("rule_results", []),
            "rule_summary": review.get("rule_summary", {}),
            "evidence_refs": evidence_block.get("evidence_refs", []),
            "module_evidence": evidence_block.get("module_evidence", {}),
            "llm_calls": llm_calls,
            "production_scope_evidence": deepcopy([f for f in extracted.get('facts', []) if f.get('field') == 'production_scope']),
            "conclusion": {"ai_judgement": overall_summary.get("overall_result", overall_result), "suggestion": final["recommended_action"]},
        }

    @staticmethod
    def _merge_rules(*rule_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for group in rule_groups:
            for item in group or []:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("rule_code", "") or "")
                if code and code in seen:
                    continue
                if code:
                    seen.add(code)
                merged.append(item)
        return merged

    @staticmethod
    def _build_overall_evidence_chain(
        category_suggestion: Dict[str, Any],
        evidence_refs: List[Dict[str, Any]],
        matched_rules: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        docs = []
        for e in evidence_refs or []:
            if not isinstance(e, dict):
                continue
            docs.append(
                {
                    "doc_id": e.get("doc_id", ""),
                    "doc_name": e.get("title", ""),
                    "position": e.get("position", ""),
                    "snippet": e.get("snippet", ""),
                }
            )
        rules = []
        for r in matched_rules or []:
            if not isinstance(r, dict):
                continue
            rules.append(
                {
                    "rule_code": r.get("rule_code", ""),
                    "rule_name": r.get("rule_name", ""),
                    "rule_category": r.get("rule_category", ""),
                }
            )
        return [
            {
                "claim": "变更管理类别建议",
                "claim_value": (category_suggestion or {}).get("suggested_category", ""),
                "reason": (category_suggestion or {}).get("reason", ""),
                "evidence_docs": docs,
                "evidence_rules": rules,
            }
        ]

    @staticmethod
    def _build_correction_notice_markdown(correction_notice: Dict[str, Any]) -> str:
        def _clean(value: Any) -> str:
            text = str(value or "")
            out = []
            for ch in text:
                code = ord(ch)
                if ch in "\t\n\r" or code >= 32:
                    out.append(ch)
            return "".join(out).replace("\x00", "")

        lines = ["# 补正通知书草稿", ""]
        lines.append(_clean((correction_notice or {}).get("draft_text", "请根据以下事项补正后重新提交。")))
        lines.append("")
        lines.append("## 一、补正事项")
        for idx, item in enumerate((correction_notice or {}).get("items", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            lines.append(f"### {idx}. {_clean(item.get('issue', ''))}")
            lines.append(f"- 依据：{_clean(item.get('basis', ''))}")
            lines.append(f"- 需补充资料：{_clean(item.get('required_supplement', ''))}")
            refs = item.get("evidence_refs", []) or []
            if refs:
                lines.append("- 证据链：")
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    source = ref.get('source') if isinstance(ref.get('source'), dict) else ref.get('source_position') or {}
                    title = ref.get('title') or source.get('file_name') or ref.get('file_name')
                    snippet = _clean(ref.get('snippet') or ref.get('raw_value') or ref.get('result_text') or '')
                    if source.get('doc_id') == 'application_form':
                        lines.append('  - 申请表字段：' + str(source.get('field') or ref.get('field') or '字段名称未记录') + ' / ' + str(source.get('subfield') or '') + '；实际值：' + snippet)
                    elif title and snippet:
                        lines.append('  - 材料：' + _clean(title) + '；位置：' + _clean(ref.get('position') or source))
                        lines.append('    - 原文：' + snippet)
                    else:
                        lines.append('  - 尚无可定位原文证据；请按上述具体问题核对材料或补充证据。')
            lines.append("")
        lines.append("## 二、提交要求")
        lines.append("请在规定期限内提交补正资料，并确保申请表与申报资料一致。")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _build_review_report_markdown(
        project_id: str,
        overall_summary: Dict[str, Any],
        review_report_draft: Dict[str, Any],
        matched_rules: List[Dict[str, Any]],
        risk_points: List[Any],
        manual_review_items: List[Any],
        evidence_refs: List[Dict[str, Any]],
    ) -> str:
        def _fmt_scalar(value: Any) -> str:
            return str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")

        def _clean(value: Any) -> str:
            text = str(value or "")
            out = []
            for ch in text:
                code = ord(ch)
                if ch in "\t\n\r" or code >= 32:
                    out.append(ch)
            return "".join(out).replace("\x00", "")

        lines = ["# 药品备案变更审评报告（草稿）", ""]
        has_overall_section = False
        section_names = [
            str((sec or {}).get("name", ""))
            for sec in ((review_report_draft or {}).get("sections", []) or [])
            if isinstance(sec, dict)
        ]
        if any("总体审评建议" in name for name in section_names):
            has_overall_section = True
        if not has_overall_section:
            lines.append("## 一、总体审评建议")
            lines.append(f"- AI初步建议：{_clean((overall_summary or {}).get('overall_result', '需人工确认'))}")
            lines.append(f"- 建议说明：{_clean((overall_summary or {}).get('summary', ''))}")
            lines.append("")

        sections = (review_report_draft or {}).get("sections", []) or []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec_name = _clean(sec.get("name", "未命名章节"))
            lines.append(f"## {sec_name}")
            conclusion = sec.get("conclusion", "")
            if "资料形式审查" in sec_name and isinstance(conclusion, dict):
                lines.append(f"- 审查结论：{_clean(conclusion.get('result', '需人工确认'))}")
                resolution = conclusion.get('parse_resolution') or {}
                records = [('申请表', resolution.get('application_form') or {})] + [
                    (item.get('doc_id', '资料'), item) for item in resolution.get('submissions', [])]
                for label, record in records:
                    if record.get('revision'):
                        state = '人工核对已完成' if record.get('manually_reviewed') else '人工核对尚未完成'
                        lines.append(f"- 解析核对来源：{_clean(label)}，{state}，修订{record['revision']}；原始解析状态：{_clean(record.get('original_status', ''))}。")
                missing = conclusion.get("missing_materials", []) or []
                for issue in conclusion.get('parse_issues', []):
                    lines.append(f"- 解析范围提示：{_clean(issue.get('file_name') or issue.get('doc_id'))}：{_clean(issue.get('message'))}")
                    pages = (issue.get('parse_diagnostics') or {}).get('failed_pages', [])
                    if pages:
                        lines.append(f"- 未完整解析页：{', '.join(str(p) for p in pages)}")
                lines.append(f"- 缺失资料项：{len(missing)}")
                for m in missing:
                    if isinstance(m, dict):
                        lines.append(f"  - {_clean(m.get('message', m.get('label', '')))}")
                    else:
                        lines.append(f"  - {_clean(m)}")
                for s in conclusion.get("suggestions", []) or []:
                    lines.append(f"- 处理建议：{_clean(s)}")
            elif "一致性核验" in sec_name and isinstance(conclusion, dict):
                inconsistent = conclusion.get("inconsistent_items", []) or []
                lines.append(f"- 一致性结论：{'存在不一致' if inconsistent else ('需人工确认' if conclusion.get('need_manual_review') else ('已核验' if conclusion.get('comparisons') else '历史结果未记录'))}")
                for item in inconsistent:
                    lines.append(f"  - 不一致项：{_clean(item)}")
                for item in (conclusion.get("need_manual_review", []) or []):
                    lines.append(f"  - 需人工确认：{_clean(item)}")
            elif "变更事项" in sec_name and isinstance(conclusion, dict):
                lines.append(f"- 建议管理类别：{_clean(conclusion.get('suggested_category', '需人工确认'))}")
                lines.append(f"- 建议理由：{_clean(conclusion.get('reason', ''))}")
                conf = conclusion.get("confidence", "")
                if conf != "":
                    lines.append(f"- 建议置信度：{_clean(conf)}")
            elif "质量标准比对" in sec_name and isinstance(conclusion, dict):
                lines.append(f"- 比对结论：{_clean(conclusion.get('result', '需人工确认'))}")
                for item in (conclusion.get("items", []) or []):
                    if not isinstance(item, dict):
                        continue
                    lines.append(
                        f"  - 项目：{_clean(item.get('test_item',''))}；标准：{_clean(item.get('standard',''))}；结果：{_clean(item.get('judgement', item.get('result','')))}"
                    )
            elif "稳定性趋势分析" in sec_name and isinstance(conclusion, dict):
                lines.append(f"- 分析结论：{_clean(conclusion.get('result', '需人工确认'))}")
                lines.append(f"- 结论说明：{_clean(conclusion.get('summary', ''))}")
                limit_check = conclusion.get("limit_check", {}) if isinstance(conclusion.get("limit_check", {}), dict) else {}
                if limit_check:
                    lines.append(
                        f"- 超限提醒：{_clean(limit_check.get('status', '待确认'))}；{_clean(limit_check.get('reminder', ''))}"
                    )
                    for detail in (limit_check.get("out_of_spec_details", []) or []):
                        if not isinstance(detail, dict):
                            continue
                        lines.append(
                            "  - 超限明细："
                            f"指标 {_clean(detail.get('indicator', ''))}；"
                            f"{_clean(detail.get('context', ''))}；"
                            f"检测结果 {_clean(detail.get('result_text', ''))}；"
                            f"可接受标准 {_clean(detail.get('limit_text', ''))}"
                        )
                    for detail in (limit_check.get("undecidable_details", []) or []):
                        if not isinstance(detail, dict):
                            continue
                        lines.append(
                            "  - 待确认明细："
                            f"指标 {_clean(detail.get('indicator', ''))}；"
                            f"{_clean(detail.get('context', ''))}；"
                            f"检测结果 {_clean(detail.get('result_text', ''))}；"
                            f"可接受标准 {_clean(detail.get('limit_text', ''))}；"
                            f"原因 {_clean(detail.get('reason_text', detail.get('reason', '')))}"
                        )
                for k in (conclusion.get("key_indicators", []) or []):
                    if not isinstance(k, dict):
                        continue
                    lines.append(
                        f"  - 指标：{_clean(k.get('indicator',''))}；趋势：{_clean(k.get('trend',''))}；"
                        f"风险等级：{_clean(k.get('risk_level',''))}；限度判定：{_clean(k.get('limit_reminder', k.get('limit_status','待确认')))}"
                    )
            elif "风险点与需人工确认事项" in sec_name and isinstance(conclusion, dict):
                for item in (conclusion.get("risk_points", []) or []):
                    lines.append(f"- 风险点：{_clean(item)}")
                for item in (conclusion.get("manual_review_items", []) or []):
                    lines.append(f"- 需人工确认：{_clean(item)}")
            else:
                lines.append(f"- 内容：{_clean(_fmt_scalar(conclusion))}")
            refs = sec.get("evidence_refs", []) or []
            if refs:
                lines.append("### 证据引用")
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    doc_name = _clean(ref.get("doc_name", "") or ref.get("title", ""))
                    pos = _clean(ref.get("position", ""))
                    location_text = f"（位置：{pos}）" if pos else ""
                    lines.append(f"- 文档：{doc_name}{location_text}")
                    snippet = _clean(ref.get("snippet", "") or "").replace("\n", " ")[:200]
                    if snippet:
                        lines.append(f"  - 片段：{snippet}")
            lines.append("")
        if not any("风险点与需人工确认事项" in name for name in section_names):
            lines.append("## 八、风险点与需人工确认事项")
            if risk_points:
                for x in risk_points:
                    lines.append(f"- 风险点：{_clean(x)}")
            if manual_review_items:
                for x in manual_review_items:
                    lines.append(f"- 需人工确认：{_clean(x)}")
            lines.append("")
        lines.append("## 九、命中规则")
        if not matched_rules:
            lines.append("- （无）")
        for r in matched_rules or []:
            if not isinstance(r, dict):
                continue
            lines.append(f"- {_clean(r.get('rule_code',''))} {_clean(r.get('rule_name',''))}（{_clean(r.get('rule_category',''))}）")
            rule_content = _clean(r.get("rule_content", ""))
            if rule_content:
                lines.append(f"  - 规则内容：{rule_content[:260]}")
            hit_result = _clean(r.get("hit_result", ""))
            if hit_result:
                lines.append(f"  - 命中结论：{hit_result}")
            basis_source = _clean(r.get("basis_source", ""))
            if basis_source:
                lines.append(f"  - 依据来源：{basis_source}")
            facts = r.get("facts", []) or []
            if facts:
                lines.append("  - 申报资料事实：")
                for fact in facts:
                    lines.append(f"    - {_clean(fact)}")
            review_conclusion = _clean(r.get("review_conclusion", ""))
            if review_conclusion:
                lines.append(f"  - 审评判断：{review_conclusion}")
        lines.append("")
        lines.append("## 十、证据依据")
        ref_docs = []
        submit_docs = []
        for ref in evidence_refs or []:
            if not isinstance(ref, dict):
                continue
            item = {
                "title": _clean(ref.get("title", "")),
                "pos": _clean(ref.get("position", "")),
                "snippet": _clean(ref.get("snippet", "")).replace("\n", " ")[:180],
            }
            if ref.get("source_type") == "reference":
                ref_docs.append(item)
            else:
                submit_docs.append(item)
        lines.append("### 10.1 参考资料文件")
        if not ref_docs:
            lines.append("- （无）")
        for d in ref_docs:
            location_text = f"（位置：{d['pos']}）" if d["pos"] else ""
            lines.append(f"- 文件：{d['title']}{location_text}")
        lines.append("### 10.2 申报资料证据")
        if not submit_docs:
            lines.append("- （无）")
        for d in submit_docs:
            location_text = f"（位置：{d['pos']}）" if d["pos"] else ""
            lines.append(f"- 文件：{d['title']}{location_text}")
            if d["snippet"]:
                lines.append(f"  - 片段：{d['snippet']}")
        lines.append("")
        lines.append("## 十一、审评员复核意见")
        lines.append("【审评员复核意见】")
        lines.append("")
        return "\n".join(lines)
