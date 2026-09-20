<template>
  <el-dialog
    :visible="visible"
    :title="dialogTitle"
    width="90%"
    top="4vh"
    @close="$emit('update:visible', false)"
  >
    <div class="dialog-body">
      <div class="summary-strip">
        <div class="summary-card primary">
          <div class="label">审评结论</div>
          <div class="value">{{ normalizedConclusionText }}</div>
          <div class="muted">章节：{{ currentBrowseSectionLabel || "-" }}</div>
        </div>
        <div class="summary-card">
          <div class="label">反馈模式</div>
          <div class="value">{{ feedbackLoopModeLabel }}</div>
          <div class="muted">审评生成：{{ llmExecutionLabel }}</div>
        </div>
        <div class="summary-card">
          <div class="label">优化状态</div>
          <div class="value">{{ optimizeStatusText }}</div>
          <div class="muted">候选注册：{{ candidateStatusText }}</div>
        </div>
      </div>

      <div class="dialog-actions">
        <el-button
          size="mini"
          type="primary"
          plain
          :disabled="!hasExecutionAudits"
          @click="auditDialogVisible = true"
        >
          查看历史审计
        </el-button>
      </div>

      <div class="content-layout">
        <div class="content-main">
          <div class="panel overview-panel">
            <div class="panel-title">核心结论</div>
            <div class="conclusion-block">
              <div class="conclusion-label">审评判断</div>
              <div class="conclusion-text">{{ reviewerConclusionLead }}</div>
            </div>
            <div class="conclusion-block">
              <div class="conclusion-label">形成结论的主要依据</div>
              <div class="conclusion-text">{{ reviewerConclusionBasis }}</div>
            </div>
            <div class="conclusion-block">
              <div class="conclusion-label">{{ judgmentOverview.risky > 0 ? "尚待补充" : "审评建议" }}</div>
              <div class="conclusion-text">{{ reviewerConclusionFollowUp }}</div>
            </div>
            <div class="section-line"><strong>审评生成：</strong>{{ llmExecutionLabel }}</div>
            <div v-if="llmExecutionHint !== '-'" class="muted">模型状态：{{ llmExecutionHint }}</div>
            <div class="overview-stats">
              <div class="mini-stat">
                <div class="mini-label">判断项</div>
                <div class="mini-value">{{ judgmentOverview.total }}</div>
              </div>
              <div class="mini-stat">
                <div class="mini-label">满足</div>
                <div class="mini-value success">{{ judgmentOverview.supported }}</div>
              </div>
              <div class="mini-stat">
                <div class="mini-label">未通过/待补充</div>
                <div class="mini-value warning">{{ judgmentOverview.risky }}</div>
              </div>
              <div class="mini-stat">
                <div class="mini-label">通过证据</div>
                <div class="mini-value">{{ approvedEvidenceCount }}</div>
              </div>
            </div>
          </div>

          <div v-if="structuredRiskItems.length" class="panel risk-panel">
            <div class="panel-head">
              <div>
                <div class="panel-title">结构化风险项</div>
                <div class="muted">按风险等级、问题类型和证据来源自动汇总，兼容旧版输出。</div>
              </div>
              <div class="risk-summary-strip">
                <div class="mini-stat compact">
                  <div class="mini-label">总数</div>
                  <div class="mini-value">{{ riskSummary.total }}</div>
                </div>
                <div class="mini-stat compact">
                  <div class="mini-label">高风险</div>
                  <div class="mini-value warning">{{ riskSummary.high }}</div>
                </div>
                <div class="mini-stat compact">
                  <div class="mini-label">中风险</div>
                  <div class="mini-value">{{ riskSummary.medium }}</div>
                </div>
                <div class="mini-stat compact">
                  <div class="mini-label">低风险</div>
                  <div class="mini-value success">{{ riskSummary.low }}</div>
                </div>
              </div>
            </div>
            <div class="risk-list">
              <div v-for="(item, index) in structuredRiskItems.slice(0, 6)" :key="`risk-${index}-${item.issue_type}-${item.title}`" class="risk-card">
                <div class="risk-card-head">
                  <div class="risk-card-title">{{ item.title }}</div>
                  <div class="risk-card-tags">
                    <el-tag size="mini" :type="riskLevelTagType(item.risk_level)">{{ item.risk_level_label }}</el-tag>
                    <el-tag size="mini" type="info">{{ item.issue_type_label }}</el-tag>
                    <el-tag size="mini" type="success">{{ item.risk_domain_label }}</el-tag>
                  </div>
                </div>
                <div class="risk-card-grid">
                  <div class="risk-card-cell full">
                    <div class="detail-label">证据</div>
                    <div class="detail-text">{{ item.evidence || '-' }}</div>
                  </div>
                  <div class="risk-card-cell full">
                    <div class="detail-label">建议</div>
                    <div class="detail-text">{{ item.recommendation || '-' }}</div>
                  </div>
                  <div class="risk-card-cell">
                    <div class="detail-label">来源</div>
                    <div class="detail-text">{{ item.source_type_label || '-' }}</div>
                  </div>
                  <div class="risk-card-cell">
                    <div class="detail-label">置信度</div>
                    <div class="detail-text">{{ item.confidence || '-' }}</div>
                  </div>
                </div>
                <div class="judgment-feedback">
                  <div class="detail-label">风险项反馈文本</div>
                  <el-input
                    v-model="judgmentFeedbackEntry(riskFeedbackPseudoItem(item, index), `risk-${index}`).feedback_text"
                    type="textarea"
                    :rows="2"
                    placeholder="请补充该风险项的反馈，例如规则适用、事实抽取、证据使用或结论表达存在的问题。"
                  />
                </div>
              </div>
            </div>
          </div>

          <div v-if="!isP52Section" class="panel">
            <div class="panel-head">
              <div class="panel-title">判断项</div>
              <div class="muted">按未通过/待补充与已通过分开展示。每个判断项默认折叠，展开后查看规则、事实、证据、推理和反馈。</div>
            </div>
            <div v-if="taskVerdictItems.length" class="verdict-groups">
              <div v-if="riskyTaskVerdictItems.length" class="verdict-group">
                <div class="group-head">
                  <div class="sub-title">未通过 / 证据不足</div>
                  <el-tag size="mini" type="warning">{{ riskyTaskVerdictItems.length }}</el-tag>
                </div>
                <div v-for="(item, index) in riskyTaskVerdictItems" :key="`task-risk-${index}`" class="issue-card verdict-card">
                  <div class="issue-head">
                    <div class="issue-title">{{ item.task_question || item.problem || "-" }}</div>
                    <div class="issue-head-actions">
                      <el-tag size="mini" :type="judgmentStatusType(item.status)">{{ judgmentStatusText(item.status) }}</el-tag>
                      <el-button type="text" size="mini" @click="toggleJudgmentItem(item, index)">
                        {{ isJudgmentItemExpanded(item, index) ? "收起" : "展开" }}
                      </el-button>
                    </div>
                  </div>
                  <div class="verdict-summary">{{ item.problem || item.task_question || "-" }}</div>
                  <div v-if="isJudgmentItemExpanded(item, index)" class="detail-grid">
                    <div class="detail-block full">
                      <div class="detail-label">规则依据</div>
                      <div class="detail-text">{{ item.basis || item.rule_requirement || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">章节事实</div>
                      <div class="detail-text">{{ item.material_fact || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">支持证据</div>
                      <div class="detail-text">{{ item.evidence_support || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">对比分析</div>
                      <div class="detail-text">{{ item.comparison || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">判断原因</div>
                      <div class="detail-text">{{ item.reason || item.judgment_reason || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">建议</div>
                      <div class="detail-text">{{ item.advice || "-" }}</div>
                    </div>
                  </div>
                  <div v-if="isJudgmentItemExpanded(item, index)" class="judgment-feedback">
                    <div class="detail-label">判断项反馈文本</div>
                    <el-input
                      v-model="judgmentFeedbackEntry(item, index).feedback_text"
                      type="textarea"
                      :rows="2"
                      placeholder="请补充该判断项的反馈，例如规则理解、事实抽取、证据使用或结论表达存在的问题。"
                    />
                    <div class="judgment-feedback-actions">
                      <el-button type="text" size="mini" @click="toggleJudgmentFeedback(judgmentFeedbackKey(item, index))">
                        {{ isJudgmentFeedbackExpanded(judgmentFeedbackKey(item, index)) ? "收起详细反馈" : "详细反馈" }}
                      </el-button>
                    </div>
                    <div v-if="isJudgmentFeedbackExpanded(judgmentFeedbackKey(item, index))" class="judgment-feedback-detail">
                      <div class="field">
                        <label>整体判断是否正确</label>
                        <el-radio-group v-model="judgmentFeedbackEntry(item, index).verdict" size="mini">
                          <el-radio-button label="correct">正确</el-radio-button>
                          <el-radio-button label="incorrect">错误</el-radio-button>
                        </el-radio-group>
                      </div>
                      <div class="judgment-feedback-grid">
                        <div class="field">
                          <label>规则反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.rule" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>事实反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.fact" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>证据反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.evidence" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>推理反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.reasoning" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>结论反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.conclusion" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                      </div>
                      <div v-if="judgmentNeedsErrorReason(judgmentFeedbackEntry(item, index))" class="field full">
                        <label>错误原因</label>
                        <el-input
                          v-model="judgmentFeedbackEntry(item, index).error_reason"
                          type="textarea"
                          :rows="3"
                          placeholder="请说明该判断项错误的具体原因，例如规则适用错误、事实识别遗漏、证据引用不当或推理结论不成立。"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="supportedTaskVerdictItems.length" class="verdict-group">
                <div class="group-head">
                  <div class="sub-title">已通过</div>
                  <el-tag size="mini" type="success">{{ supportedTaskVerdictItems.length }}</el-tag>
                </div>
                <div v-for="(item, index) in supportedTaskVerdictItems" :key="`task-supported-${index}`" class="issue-card verdict-card supported">
                  <div class="issue-head">
                    <div class="issue-title">{{ item.task_question || item.problem || "-" }}</div>
                    <div class="issue-head-actions">
                      <el-tag size="mini" :type="judgmentStatusType(item.status)">{{ judgmentStatusText(item.status) }}</el-tag>
                      <el-button type="text" size="mini" @click="toggleJudgmentItem(item, 'supported-' + index)">
                        {{ isJudgmentItemExpanded(item, 'supported-' + index) ? "收起" : "展开" }}
                      </el-button>
                    </div>
                  </div>
                  <div class="verdict-summary">{{ item.problem || item.task_question || "-" }}</div>
                  <div v-if="isJudgmentItemExpanded(item, 'supported-' + index)" class="detail-grid">
                    <div class="detail-block full">
                      <div class="detail-label">规则依据</div>
                      <div class="detail-text">{{ item.basis || item.rule_requirement || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">章节事实</div>
                      <div class="detail-text">{{ item.material_fact || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">支持证据</div>
                      <div class="detail-text">{{ item.evidence_support || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">对比分析</div>
                      <div class="detail-text">{{ item.comparison || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">判断原因</div>
                      <div class="detail-text">{{ item.reason || item.judgment_reason || "-" }}</div>
                    </div>
                    <div class="detail-block full">
                      <div class="detail-label">建议</div>
                      <div class="detail-text">{{ item.advice || "-" }}</div>
                    </div>
                  </div>
                  <div v-if="isJudgmentItemExpanded(item, 'supported-' + index)" class="judgment-feedback">
                    <div class="detail-label">判断项反馈文本</div>
                    <el-input
                      v-model="judgmentFeedbackEntry(item, index).feedback_text"
                      type="textarea"
                      :rows="2"
                      placeholder="请补充该判断项的反馈，例如还需补强的事实、证据或表达方式。"
                    />
                    <div class="judgment-feedback-actions">
                      <el-button type="text" size="mini" @click="toggleJudgmentFeedback(judgmentFeedbackKey(item, index))">
                        {{ isJudgmentFeedbackExpanded(judgmentFeedbackKey(item, index)) ? "收起详细反馈" : "详细反馈" }}
                      </el-button>
                    </div>
                    <div v-if="isJudgmentFeedbackExpanded(judgmentFeedbackKey(item, index))" class="judgment-feedback-detail">
                      <div class="field">
                        <label>整体判断是否正确</label>
                        <el-radio-group v-model="judgmentFeedbackEntry(item, index).verdict" size="mini">
                          <el-radio-button label="correct">正确</el-radio-button>
                          <el-radio-button label="incorrect">错误</el-radio-button>
                        </el-radio-group>
                      </div>
                      <div class="judgment-feedback-grid">
                        <div class="field">
                          <label>规则反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.rule" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>事实反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.fact" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>证据反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.evidence" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>推理反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.reasoning" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                        <div class="field">
                          <label>结论反馈</label>
                          <el-radio-group v-model="judgmentFeedbackEntry(item, index).field_feedback.conclusion" size="mini">
                            <el-radio-button label="correct">正确</el-radio-button>
                            <el-radio-button label="incorrect">错误</el-radio-button>
                            <el-radio-button label="unknown">未判断</el-radio-button>
                          </el-radio-group>
                        </div>
                      </div>
                      <div v-if="judgmentNeedsErrorReason(judgmentFeedbackEntry(item, index))" class="field full">
                        <label>错误原因</label>
                        <el-input
                          v-model="judgmentFeedbackEntry(item, index).error_reason"
                          type="textarea"
                          :rows="3"
                          placeholder="请说明该判断项错误的具体原因，例如规则适用错误、事实识别遗漏、证据引用不当或推理结论不成立。"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="empty">当前还没有结构化判断项。</div>
          </div>

        </div>

        <div class="content-side">
          <div class="panel side-panel">
            <div class="panel-head">
              <div class="panel-title">审评规则</div>
              <div class="muted">这些规则会直接影响检索规划、检索评估和章节结论。</div>
            </div>
            <div v-if="displayedReviewRuleItems.length" class="issue-list">
              <div v-for="(item, index) in displayedReviewRuleItems" :key="item.feedback_key || `review-rule-${index}`" class="review-rule-card">
                <div class="review-rule-head">
                  <div class="review-rule-text">{{ index + 1 }}. {{ item.display_text }}</div>
                  <el-tag v-if="item.status" size="mini" :type="judgmentStatusType(item.status)">
                    {{ judgmentStatusText(item.status) }}
                  </el-tag>
                </div>
                <div v-if="item.issue || item.evidence" class="review-rule-finding">
                  <div v-if="item.issue"><strong>命中结果：</strong>{{ item.issue }}</div>
                  <div v-if="item.evidence"><strong>命中证据：</strong>{{ item.evidence }}</div>
                </div>
                <div class="judgment-feedback review-rule-feedback">
                  <div class="detail-label">该规则的命中与应用是否正确</div>
                  <el-radio-group v-model="judgmentFeedbackEntry(item, index).verdict" size="mini">
                    <el-radio-button label="correct">正确</el-radio-button>
                    <el-radio-button label="incorrect">错误</el-radio-button>
                    <el-radio-button label="unknown">无法判断</el-radio-button>
                  </el-radio-group>
                  <el-input
                    v-model="judgmentFeedbackEntry(item, index).feedback_text"
                    type="textarea"
                    :rows="2"
                    placeholder="可逐条说明规则是否应命中、规则理解或引用是否正确。"
                  />
                  <el-input
                    v-if="judgmentNeedsErrorReason(judgmentFeedbackEntry(item, index))"
                    v-model="judgmentFeedbackEntry(item, index).error_reason"
                    type="textarea"
                    :rows="2"
                    placeholder="请说明该规则命中或应用错误的具体原因。"
                  />
                </div>
              </div>
            </div>
            <div v-else class="empty">当前章节还没有加载到审评规则。</div>
          </div>

          <div class="panel side-panel">
            <div class="panel-head">
              <div class="panel-title">检索证据</div>
              <div class="muted">只展示综合得分 ≥ 0.5 且通过 retrieval_evaluator 的资料。</div>
            </div>
            <div class="evidence-section">
              <div class="evidence-section-head">
                <div>
                  <div class="sub-title">通过证据</div>
                  <div class="muted">这些资料通过了 retrieval_evaluator，会送入 reviewer 参与结论生成。</div>
                </div>
                <el-button type="text" size="mini" @click="toggleEvidenceSection('approved')">
                  {{ isEvidenceSectionCollapsed('approved') ? "展开" : "折叠" }}
                </el-button>
              </div>
              <div v-if="!isEvidenceSectionCollapsed('approved') && visibleApprovedEvidenceGroups.length">
                <div v-for="group in visibleApprovedEvidenceGroups" :key="group.expandKey" class="evidence-group">
                  <div class="evidence-group-head">
                    <strong>{{ group.source }}</strong>
                    <span class="muted">{{ group.total }} 条</span>
                    <el-button
                      v-if="group.total > defaultEvidenceLimit"
                      type="text"
                      size="mini"
                      @click="toggleEvidenceGroup(group.expandKey)"
                    >
                      {{ isEvidenceGroupExpanded(group.expandKey) ? "收起" : "查看全部" }}
                    </el-button>
                  </div>
                  <div
                    v-for="item in group.visibleItems"
                    :key="`approved:${evidenceKey(item)}`"
                    class="evidence-card"
                  >
                    <div class="evidence-head">
                      <div>
                        <div class="evidence-name">{{ evidenceName(item) }}</div>
                        <div class="muted">标题：{{ evidenceTitle(item) }}</div>
                      </div>
                      <div class="score-box">
                        <div>综合分：{{ numberText(item.score) }}</div>
                        <div class="muted">向量分 / 词法分 {{ numberText(item.vector_score) }} / {{ numberText(item.lexical_score) }}</div>
                      </div>
                    </div>
                    <div class="muted">doc_id：{{ item.doc_id || "-" }} / chunk_id：{{ item.chunk_id || "-" }}</div>
                    <div v-if="evidenceReasonText(item, 'approved')" class="muted">保留原因：{{ evidenceReasonText(item, 'approved') }}</div>
                    <el-tooltip effect="dark" placement="top-start" :content="fullEvidenceText(item)" :disabled="!fullEvidenceText(item)">
                      <div class="snippet">{{ evidenceSnippet(item) }}</div>
                    </el-tooltip>
                    <div class="evidence-feedback">
                      <span>证据反馈</span>
                      <el-select v-model="feedbackForm.evidence_feedback_map[evidenceKey(item)]" size="mini" style="width: 140px" placeholder="请选择">
                        <el-option label="未判断" value="unknown" />
                        <el-option label="检索正确" value="correct" />
                        <el-option label="检索错误" value="incorrect" />
                        <el-option label="部分正确" value="partial" />
                      </el-select>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else-if="!isEvidenceSectionCollapsed('approved')" class="empty">当前章节没有通过检索评估的证据。</div>
            </div>
          </div>
        </div>
      </div>
      <div class="feedback-grid">
        <div class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-title">反馈输入</div>
              <div class="muted">基于每个判断项的反馈结果、最终建议和漏项说明提交反馈，驱动后续优化。</div>
            </div>
            <el-button type="text" size="mini" @click="togglePanel('feedbackInput')">
              {{ isPanelCollapsed('feedbackInput') ? "展开" : "收起" }}
            </el-button>
          </div>
          <div v-if="!isPanelCollapsed('feedbackInput')">
            <div class="field full">
              <label>最终反馈建议</label>
              <el-input
                v-model="feedbackForm.feedback_text"
                type="textarea"
                :rows="4"
                placeholder="请总结本章节最重要的修正建议、希望系统后续吸收的规则/推理优化点，避免只重复判断项原文。"
              />
            </div>
            <div class="field full">
              <label>漏项反馈</label>
              <el-input
                v-model="feedbackForm.missing_item_feedback_text"
                type="textarea"
                :rows="3"
                placeholder="请填写系统遗漏但应纳入审评的问题项、判断项或结论，例如遗漏的规则要求、关键事实或未识别的风险点。"
              />
            </div>
            <div class="field full">
              <label>漏项原因 / 依据</label>
              <el-input
                v-model="feedbackForm.missing_item_feedback_reason"
                type="textarea"
                :rows="3"
                placeholder="请说明为什么这是漏项，可补充对应规则、章节事实、证据或期望修正方式。"
              />
            </div>
            <div class="actions">
              <el-button type="primary" size="small" :loading="submittingFeedback" @click="$emit('submit-feedback')">提交反馈</el-button>
              <el-button
                v-if="isP52Section && feedbackLoopMode !== 'feedback_only'"
                type="warning"
                size="small"
                :loading="submittingP52FeedbackOptimize"
                @click="$emit('submit-p52-feedback-optimize')"
              >
                生成 P52 候选 Patch
              </el-button>
            </div>
          </div>
        </div>

      </div>

      <div class="panel">
        <div class="panel-head">
          <div class="panel-title">高级信息</div>
          <div class="muted">每个区域单独点击按钮后展示。</div>
        </div>
        <div class="advanced-actions">
          <el-button size="mini" :type="isAdvancedSectionVisible('metrics') ? 'primary' : 'default'" @click="toggleAdvancedSection('metrics')">运行指标</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('retrieval') ? 'primary' : 'default'" @click="toggleAdvancedSection('retrieval')">检索轨迹</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('chapterProfile') ? 'primary' : 'default'" @click="toggleAdvancedSection('chapterProfile')">章节画像</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('extractedFacts') ? 'primary' : 'default'" @click="toggleAdvancedSection('extractedFacts')">实体抽取</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('sectionRules') ? 'primary' : 'default'" @click="toggleAdvancedSection('sectionRules')">章节规则</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('promptRules') ? 'primary' : 'default'" @click="toggleAdvancedSection('promptRules')">Prompt 规则</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('optimize') ? 'primary' : 'default'" @click="toggleAdvancedSection('optimize')">反馈优化</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('experience') ? 'primary' : 'default'" @click="toggleAdvancedSection('experience')">经验记忆</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('examples') ? 'primary' : 'default'" @click="toggleAdvancedSection('examples')">参考示例</el-button>
          <el-button size="mini" :type="isAdvancedSectionVisible('history') ? 'primary' : 'default'" @click="toggleAdvancedSection('history')">反馈历史</el-button>
        </div>
      </div>

      <div v-if="hasVisibleAdvancedSection" class="advanced-grid">
        <run-metrics-panel
          v-if="isAdvancedSectionVisible('metrics')"
          :metrics="metrics"
          :metric-percent="metricPercent"
          :metric-number="metricNumber"
          :ablation-detail="ablationDetail"
          :loading-ablation="loadingAblation"
          @run-ablation="$emit('run-ablation')"
        />
        <retrieval-trace-panel
          v-if="isAdvancedSectionVisible('retrieval')"
          :detail="retrievalDetail"
          :metric-percent="metricPercent"
          :metric-number="metricNumber"
          :trace-artifact="traceArtifact"
          :loading-artifact="loadingTraceArtifact"
          @view-trace-artifact="$emit('view-trace-artifact')"
        />

        <div v-if="isAdvancedSectionVisible('chapterProfile')" class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-title">章节画像</div>
              <div class="muted">展示本章在注册论证链中的作用、核心审评问题和必须回答的问题。</div>
            </div>
          </div>
          <div v-if="hasSectionReviewProfile" class="result-summary">
            <div v-if="chapterRoleText !== '-'" class="section-line"><strong>本章作用：</strong>{{ chapterRoleText }}</div>
            <div v-if="coreReviewQuestionText !== '-'" class="section-line"><strong>核心审评问题：</strong>{{ coreReviewQuestionText }}</div>
            <div v-if="mustAnswerCoverage.length" class="overview-stats">
              <div class="mini-stat">
                <div class="mini-label">必答问题</div>
                <div class="mini-value">{{ mustAnswerCoverageOverview.total }}</div>
              </div>
              <div class="mini-stat">
                <div class="mini-label">已覆盖</div>
                <div class="mini-value success">{{ mustAnswerCoverageOverview.covered }}</div>
              </div>
              <div class="mini-stat">
                <div class="mini-label">部分覆盖</div>
                <div class="mini-value warning">{{ mustAnswerCoverageOverview.partial }}</div>
              </div>
              <div class="mini-stat">
                <div class="mini-label">未覆盖</div>
                <div class="mini-value warning">{{ mustAnswerCoverageOverview.uncovered }}</div>
              </div>
            </div>
            <div v-if="mustAnswerCoverage.length" class="detail-block full">
              <div class="detail-label">必答问题覆盖情况</div>
              <div class="issue-list compact-list">
                <div v-for="(item, index) in mustAnswerCoverage" :key="`must-answer-coverage-${index}`" class="issue-card">
                  <div class="issue-head">
                    <div class="issue-title">{{ item.question }}</div>
                    <el-tag size="mini" :type="mustAnswerCoverageTagType(item.coverage_status)">{{ mustAnswerCoverageLabel(item.coverage_status) }}</el-tag>
                  </div>
                  <div class="muted">{{ item.reason }}</div>
                </div>
              </div>
            </div>
            <div v-else-if="mustAnswerQuestions.length" class="detail-block full">
              <div class="detail-label">必须回答的问题</div>
              <div class="tag-list">
                <el-tag v-for="(item, index) in mustAnswerQuestions" :key="`must-answer-${index}`" size="mini" type="info">{{ item }}</el-tag>
              </div>
            </div>
            <div v-if="reasoningPrinciples.length" class="detail-block full">
              <div class="detail-label">核心审评原则</div>
              <div class="tag-list">
                <el-tag v-for="(item, index) in reasoningPrinciples" :key="`reasoning-principle-${index}`" size="mini">{{ item }}</el-tag>
              </div>
            </div>
            <div v-if="commonRisks.length" class="detail-block full">
              <div class="detail-label">常见风险</div>
              <div class="tag-list">
                <el-tag v-for="(item, index) in commonRisks" :key="`common-risk-${index}`" size="mini" type="warning">{{ item }}</el-tag>
              </div>
            </div>
          </div>
          <div v-else class="empty">当前章节还没有加载到章节画像。</div>
        </div>

        <div v-if="isAdvancedSectionVisible('extractedFacts')" class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-title">实体抽取结果</div>
              <div class="muted">展示 reviewer 已识别的关键实体和关键数据点，供审评与反馈优化复核。</div>
            </div>
          </div>
          <div v-if="hasExtractedMedicalFacts" class="panel-grid extracted-grid">
            <div class="panel">
              <div class="sub-title">关键实体</div>
              <div v-if="medicalKeyEntities.length" class="issue-list compact-list">
                <div v-for="(item, index) in medicalKeyEntities" :key="`entity-${index}`" class="issue-card">
                  <div class="issue-title">{{ item.normalized_name || item.entity_name || "-" }}</div>
                  <div class="muted">实体类型：{{ item.entity_type || "-" }}</div>
                  <div v-if="item.value" class="section-line"><strong>值：</strong>{{ item.value }}<span v-if="item.unit"> {{ item.unit }}</span></div>
                  <div v-if="item.context" class="section-line"><strong>上下文：</strong>{{ item.context }}</div>
                  <div v-if="item.evidence" class="section-line"><strong>依据：</strong>{{ item.evidence }}</div>
                </div>
              </div>
              <div v-else class="empty">当前没有结构化关键实体。</div>
            </div>
            <div class="panel">
              <div class="sub-title">关键数据点</div>
              <div v-if="medicalKeyDataPoints.length" class="issue-list compact-list">
                <div v-for="(item, index) in medicalKeyDataPoints" :key="`data-point-${index}`" class="issue-card">
                  <div class="issue-title">{{ item.metric_name || "-" }}</div>
                  <div class="muted">数据类型：{{ item.data_type || "-" }}</div>
                  <div class="section-line"><strong>值：</strong>{{ item.value || "-" }}<span v-if="item.unit"> {{ item.unit }}</span></div>
                  <div v-if="item.comparator" class="section-line"><strong>比较符：</strong>{{ item.comparator }}</div>
                  <div v-if="item.context" class="section-line"><strong>上下文：</strong>{{ item.context }}</div>
                  <div v-if="item.evidence" class="section-line"><strong>依据：</strong>{{ item.evidence }}</div>
                </div>
              </div>
              <div v-else class="empty">当前没有结构化关键数据点。</div>
            </div>
          </div>
          <div v-else class="empty">当前章节还没有抽取到可展示的关键实体或关键数据点。</div>
        </div>

        <div v-if="isAdvancedSectionVisible('optimize')" class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-title">反馈优化结果</div>
              <div class="muted">只展示本次反馈优化的核心归因、Patch 方向和知识沉淀。</div>
            </div>
          </div>
          <div v-if="hasFeedbackOptimizeSummary" class="result-summary">
            <div v-if="feedbackIssueFamilyLabel !== '-'" class="section-line"><strong>问题归因：</strong>{{ feedbackIssueFamilyLabel }}</div>
            <div v-if="feedbackTargetLayerText !== '-'" class="section-line"><strong>主修层：</strong>{{ feedbackTargetLayerText }}</div>
            <div v-if="primaryErrorTypeLabel !== '-'" class="section-line"><strong>主错误类型：</strong>{{ primaryErrorTypeLabel }}</div>
            <div v-if="patchTargetAgentText !== '-'" class="section-line"><strong>优化目标 Agent：</strong>{{ patchTargetAgentText }}</div>
            <div v-if="patchTypeText !== '-'" class="section-line"><strong>Patch 类型：</strong>{{ patchTypeText }}</div>
            <div v-if="patchScopeText !== '-'" class="section-line"><strong>Patch 作用域：</strong>{{ patchScopeText }}</div>
            <div v-if="knowledgeCategoryText !== '-'" class="section-line"><strong>知识沉淀分类：</strong>{{ knowledgeCategoryText }}</div>
            <div v-if="patchCount > 0" class="section-line"><strong>Patch 数量：</strong>{{ patchCount }}</div>
            <div v-if="optimizeEvaluationVerdictText !== '未判断'" class="section-line"><strong>优化评价：</strong>{{ optimizeEvaluationVerdictText }}</div>
            <div v-if="optimizeEvaluationScoreText !== '-'" class="section-line"><strong>评价分数：</strong>{{ optimizeEvaluationScoreText }}</div>
            <div v-if="metaReflectionSummary !== '-'" class="section-line"><strong>元反思摘要：</strong>{{ metaReflectionSummary }}</div>
            <div v-if="isP52Section && p52ErrorConfidenceText !== '-'" class="section-line"><strong>归因置信度：</strong>{{ p52ErrorConfidenceText }}</div>
            <div v-if="isP52Section && p52ErrorMethodProfilesText !== '-'" class="section-line"><strong>归因方法域：</strong>{{ p52ErrorMethodProfilesText }}</div>
            <div v-if="isP52Section && p52ErrorRuleIdsText !== '-'" class="section-line"><strong>候选规则：</strong>{{ p52ErrorRuleIdsText }}</div>
            <div v-if="isP52Section && p52ErrorReasonList.length" class="detail-block full">
              <div class="detail-label">反馈归因明细</div>
              <div class="issue-list compact-list">
                <div v-for="(item, index) in p52ErrorReasonList" :key="`p52-error-reason-${index}`" class="issue-card">
                  <div class="detail-text">{{ item }}</div>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty">当前还没有可展示的反馈优化结果。</div>
          <div v-if="feedbackOptimizeResult.error_message" class="error-box">{{ feedbackOptimizeResult.error_message }}</div>
          <div class="actions">
            <el-button
              size="small"
              :loading="isP52Section ? submittingP52FeedbackOptimize : replayingFeedbackOptimize"
              :disabled="!selectedRun || !selectedRun.run_id"
              @click="isP52Section ? $emit('submit-p52-feedback-optimize') : $emit('replay-feedback-optimize')"
            >
              {{ isP52Section ? '生成候选 Patch' : '回放反馈优化' }}
            </el-button>
            <el-button
              v-if="isP52Section"
              size="small"
              :loading="replayingP52FeedbackVerify"
              :disabled="!selectedRun || !selectedRun.run_id"
              @click="$emit('replay-verify-p52-feedback')"
            >
              回放验证
            </el-button>
            <el-button
              v-if="isP52Section"
              size="small"
              :loading="loadingP52FeedbackPatches"
              :disabled="!selectedRun || !selectedRun.run_id"
              @click="$emit('refresh-p52-feedback-patches')"
            >
              刷新 Patch
            </el-button>
            <el-button size="small" :loading="replayingMetaReflection" :disabled="!selectedRun || !selectedRun.run_id" @click="$emit('replay-meta-reflection')">回放元反思</el-button>
          </div>
          <div v-if="isP52Section" class="p52-optimize-grid">
            <div class="panel">
              <div class="sub-title">候选 Patch</div>
              <div v-if="p52PatchGroups.length" class="form-grid">
                <div class="field">
                  <label>补丁主题</label>
                  <el-select v-model="selectedP52PatchGroupKey" size="small" placeholder="选择补丁主题">
                    <el-option
                      v-for="item in p52PatchGroups"
                      :key="item.group_key"
                      :label="`${item.group_label}（${item.versions.length} 版）`"
                      :value="item.group_key"
                    />
                  </el-select>
                </div>
                <div class="field">
                  <label>候选版本</label>
                  <el-select v-model="selectedP52PatchVersionKey" size="small" placeholder="选择版本">
                    <el-option
                      v-for="item in selectedP52PatchGroupVersions"
                      :key="item.version_key"
                      :label="item.version_label"
                      :value="item.version_key"
                    />
                  </el-select>
                </div>
              </div>
              <div v-if="selectedP52PatchVersionDiffText !== '-'" class="section-line"><strong>版本差异：</strong>{{ selectedP52PatchVersionDiffText }}</div>
              <div v-if="p52CandidatePatches.length" class="issue-list compact-list">
                <div v-for="(item, index) in p52VisibleCandidatePatches" :key="item.patch_id || index" class="issue-card">
                  <div class="issue-head">
                    <div class="issue-title">{{ item.patch_id || `patch-${index + 1}` }}</div>
                    <div class="issue-head-actions">
                      <el-tag size="mini" :type="p52PatchStatusType(item.status)">{{ p52PatchStatusLabel(item.status) }}</el-tag>
                      <el-tag size="mini" type="info">{{ p52PatchVersionLabel(item) }}</el-tag>
                      <el-button
                        type="text"
                        size="mini"
                        class="collapse-toggle"
                        @click="toggleP52PatchExpanded(item.patch_id || `patch-${index + 1}`)"
                      >
                        {{ isP52PatchExpanded(item.patch_id || `patch-${index + 1}`) ? "收起" : "展开" }}
                      </el-button>
                    </div>
                  </div>
                  <div class="muted">Patch 类型：{{ item.patch_type || "-" }}</div>
                  <div class="muted">目标 Agent：{{ targetAgentLabel(item.target_agent) }}</div>
                  <div class="muted">目标范围：{{ item.target_scope || "-" }}</div>
                  <div v-if="isP52PatchExpanded(item.patch_id || `patch-${index + 1}`)">
                    <div class="section-line"><strong>触发条件：</strong>{{ item.trigger_condition || "-" }}</div>
                    <div class="section-line"><strong>Patch 内容：</strong></div>
                    <div class="detail-text">{{ item.patch_content || "-" }}</div>
                    <div class="detail-grid compact">
                      <div class="detail-block">
                        <div class="detail-label">目标文件</div>
                        <div class="detail-text">{{ p52PatchTargetFileText(item) }}</div>
                      </div>
                      <div class="detail-block">
                        <div class="detail-label">目标键</div>
                        <div class="detail-text">{{ p52PatchTargetKeyText(item) }}</div>
                      </div>
                      <div class="detail-block">
                        <div class="detail-label">方法域</div>
                        <div class="detail-text">{{ p52PatchMethodProfilesText(item) }}</div>
                      </div>
                      <div class="detail-block">
                        <div class="detail-label">候选规则</div>
                        <div class="detail-text">{{ p52PatchRuleIdsText(item) }}</div>
                      </div>
                      <div class="detail-block">
                        <div class="detail-label">补丁版本</div>
                        <div class="detail-text">{{ p52PatchVersionLabel(item) }}</div>
                      </div>
                      <div class="detail-block full">
                        <div class="detail-label">原始反馈</div>
                        <div class="detail-text">{{ p52PatchFeedbackText(item) }}</div>
                      </div>
                      <div class="detail-block full">
                        <div class="detail-label">当前结论摘要</div>
                        <div class="detail-text">{{ p52PatchTraceSummaryText(item) }}</div>
                      </div>
                      <div v-if="p52PatchStatusUpdateText(item) !== '-'" class="detail-block full">
                        <div class="detail-label">状态备注</div>
                        <div class="detail-text">{{ p52PatchStatusUpdateText(item) }}</div>
                      </div>
                    </div>
                  </div>
                  <div class="actions">
                    <el-button
                      size="mini"
                      type="success"
                      :loading="actingP52PatchId === item.patch_id"
                      :disabled="item.status === 'approved' || item.status === 'rejected'"
                      @click="$emit('approve-p52-feedback-patch', item)"
                    >
                      批准
                    </el-button>
                    <el-button
                      size="mini"
                      type="danger"
                      plain
                      :loading="actingP52PatchId === item.patch_id"
                      :disabled="item.status === 'approved' || item.status === 'rejected'"
                      @click="$emit('reject-p52-feedback-patch', item)"
                    >
                      拒绝
                    </el-button>
                  </div>
                </div>
              </div>
              <div v-else class="empty">当前还没有生成 P52 候选 patch。</div>
            </div>
            <div class="panel">
              <div class="sub-title">元反思</div>
              <div v-if="p52MetaReflectionAvailable" class="result-summary">
                <div class="section-line"><strong>错误家族：</strong>{{ p52MetaErrorFamilyText }}</div>
                <div class="section-line"><strong>方法域：</strong>{{ p52MetaMethodProfilesText }}</div>
                <div class="section-line"><strong>规则候选：</strong>{{ p52MetaRuleIdsText }}</div>
                <div class="section-line"><strong>通过证据数：</strong>{{ p52MetaApprovedMaterialCountText }}</div>
                <div v-if="p52MetaReflectionFromHistory" class="section-line"><strong>结果来源：</strong>反馈历史回填</div>
                <div v-if="p52MetaReflectionSummaryText !== '-'" class="section-line"><strong>反思摘要：</strong>{{ p52MetaReflectionSummaryText }}</div>
                <div v-if="p52MetaRecommendedFocus.length" class="detail-block full">
                  <div class="detail-label">推荐优化焦点</div>
                  <div class="tag-list">
                    <el-tag v-for="(item, index) in p52MetaRecommendedFocus" :key="`p52-meta-focus-${index}`" size="mini" type="warning">{{ item }}</el-tag>
                  </div>
                </div>
                <div v-if="p52MetaDistilledExperiences.length" class="detail-block full">
                  <div class="detail-label">经验沉淀</div>
                  <div class="issue-list compact-list">
                    <div v-for="(item, index) in p52MetaDistilledExperiences" :key="`p52-meta-exp-${index}`" class="issue-card">
                      <div class="issue-title">{{ item.knowledge_category || item.experience_type || `经验-${index + 1}` }}</div>
                      <div class="detail-text">{{ item.content || '-' }}</div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="empty">当前还没有 P52 元反思结果。</div>
            </div>
            <div class="panel">
              <div class="sub-title">回放验证</div>
              <div v-if="p52VerificationFromHistory" class="muted">结果来源：反馈历史回填</div>
              <div v-if="p52VerificationPlanAvailable" class="result-summary">
                <div class="verification-overview">
                  <div class="mini-stat">
                    <div class="mini-label">验证结论</div>
                    <div class="mini-value" :class="verificationVerdictClass(p52VerificationVerdictCode)">{{ p52VerificationVerdictLabel }}</div>
                  </div>
                  <div class="mini-stat">
                    <div class="mini-label">自动回放</div>
                    <div class="mini-value">{{ p52VerificationAutoReplayText }}</div>
                  </div>
                  <div class="mini-stat">
                    <div class="mini-label">当前章节</div>
                    <div class="mini-value">{{ p52CurrentCaseChangedText }}</div>
                  </div>
                  <div class="mini-stat">
                    <div class="mini-label">规则合并</div>
                    <div class="mini-value">{{ p52RuleMergeSummaryText }}</div>
                  </div>
                </div>
                <div class="section-line"><strong>验证目标：</strong>{{ p52VerificationGoalsText }}</div>
                <div v-if="p52VerificationOverlaySummaryText !== '-'" class="section-line"><strong>Patch Overlay：</strong>{{ p52VerificationOverlaySummaryText }}</div>
                <div v-if="p52VerificationNotesText !== '-'" class="section-line"><strong>说明：</strong>{{ p52VerificationNotesText }}</div>
                <div class="verification-case-groups">
                  <div v-if="p52VerificationCurrentCaseCard" class="verification-case-group">
                    <div class="group-head">
                      <div class="sub-title">当前章节</div>
                      <el-tag size="mini" :type="verificationChangedTagType(p52VerificationCurrentCaseCard.changed)">
                        {{ p52VerificationCurrentCaseCard.changed ? '已变化' : '未变化' }}
                      </el-tag>
                    </div>
                    <div class="verification-case-card">
                      <div class="verification-case-head">
                        <div class="issue-title">{{ p52VerificationCurrentCaseCard.section_name || p52VerificationCurrentCaseCard.section_id || '-' }}</div>
                        <div class="muted">{{ p52VerificationCurrentCaseCard.section_id || '-' }}</div>
                      </div>
                      <div class="verification-compare-grid">
                        <div class="detail-block">
                          <div class="detail-label">变更前</div>
                          <div class="muted">结论：{{ verificationBaselineConclusionText(p52VerificationCurrentCaseCard.baseline, p52VerificationCurrentCaseCard) }}</div>
                          <div class="detail-text">{{ verificationBaselineSummary(p52VerificationCurrentCaseCard.baseline, p52VerificationCurrentCaseCard) }}</div>
                        </div>
                        <div class="detail-block">
                          <div class="detail-label">变更后</div>
                          <div class="detail-text">{{ verificationCaseSummary(p52VerificationCurrentCaseCard.patched) }}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div v-if="p52VerificationSameProfileCards.length" class="verification-case-group">
                    <div class="group-head">
                      <div class="sub-title">同方法域对照</div>
                      <el-tag size="mini" type="info">{{ p52VerificationSameProfileCards.length }}</el-tag>
                    </div>
                    <div class="verification-case-list">
                      <div v-for="(item, index) in p52VerificationSameProfileCards" :key="`p52-verify-same-${index}`" class="verification-case-card">
                        <div class="verification-case-head">
                          <div class="issue-title">{{ item.section_name || item.section_id || '-' }}</div>
                          <div class="verification-case-tags">
                            <el-tag size="mini" :type="verificationChangedTagType(item.changed)">{{ item.changed ? '已变化' : '未变化' }}</el-tag>
                            <el-tag size="mini" type="info">{{ (item.method_profiles || []).join(' / ') || '未识别方法域' }}</el-tag>
                          </div>
                        </div>
                        <div class="verification-compare-grid">
                          <div class="detail-block">
                            <div class="detail-label">变更前</div>
                            <div class="muted">结论：{{ verificationBaselineConclusionText(item.baseline, item) }}</div>
                            <div class="detail-text">{{ verificationBaselineSummary(item.baseline, item) }}</div>
                          </div>
                          <div class="detail-block">
                            <div class="detail-label">变更后</div>
                            <div class="detail-text">{{ verificationCaseSummary(item.patched) }}</div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div v-if="p52VerificationAdversarialCards.length" class="verification-case-group">
                    <div class="group-head">
                      <div class="sub-title">对抗样本</div>
                      <el-tag size="mini" type="warning">{{ p52VerificationAdversarialCards.length }}</el-tag>
                    </div>
                    <div class="verification-case-list">
                      <div v-for="(item, index) in p52VerificationAdversarialCards" :key="`p52-verify-adv-${index}`" class="verification-case-card">
                        <div class="verification-case-head">
                          <div class="issue-title">{{ item.section_name || item.section_id || '-' }}</div>
                          <div class="verification-case-tags">
                            <el-tag size="mini" :type="verificationChangedTagType(item.changed)">{{ item.changed ? '已变化' : '未变化' }}</el-tag>
                            <el-tag size="mini" type="warning">{{ adversarialRiskText(item) }}</el-tag>
                          </div>
                        </div>
                        <div class="verification-compare-grid">
                          <div class="detail-block">
                            <div class="detail-label">变更前</div>
                            <div class="muted">结论：{{ verificationBaselineConclusionText(item.baseline, item) }}</div>
                            <div class="detail-text">{{ verificationBaselineSummary(item.baseline, item) }}</div>
                          </div>
                          <div class="detail-block">
                            <div class="detail-label">变更后</div>
                            <div class="detail-text">{{ verificationCaseSummary(item.patched) }}</div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="empty">当前还没有 P52 回放验证计划。</div>
            </div>
            <div class="panel">
              <div class="panel-head">
                <div class="sub-title">优化流程回放</div>
                <el-button type="text" size="mini" @click="togglePanel('optimizeReplay')">
                  {{ isPanelCollapsed('optimizeReplay') ? "展开" : "收起" }}
                </el-button>
              </div>
              <div v-if="!isPanelCollapsed('optimizeReplay')">
                <div class="section-line"><strong>链路参数：</strong>{{ p52ProcessParameterSummary }}</div>
                <div v-if="p52ProcessChainCards.length" class="issue-list compact-list">
                <div v-for="(item, index) in p52ProcessChainCards" :key="`chain-${index}`" class="issue-card">
                  <div class="issue-head">
                    <div class="issue-title">{{ item.symbol }} · {{ item.label }}</div>
                    <div class="issue-head-actions">
                      <el-tag size="mini" type="info">{{ item.stage }}</el-tag>
                      <el-button type="text" size="mini" class="collapse-toggle" @click="toggleProcessChainCard(index)">
                        {{ isProcessChainCardExpanded(index) ? "收起" : "展开" }}
                      </el-button>
                    </div>
                  </div>
                  <div v-if="isProcessChainCardExpanded(index)" class="detail-text">{{ item.summary }}</div>
                </div>
                </div>
              </div>
            </div>
            <div class="panel">
              <div class="panel-head">
                <div class="sub-title">查看版本差异</div>
                <el-button type="text" size="mini" @click="togglePanel('versionDiff')">
                  {{ isPanelCollapsed('versionDiff') ? "展开" : "收起" }}
                </el-button>
              </div>
              <div v-if="!isPanelCollapsed('versionDiff')">
                <div class="form-grid">
                <div class="field">
                  <label>基准版本</label>
                  <el-select v-model="selectedP52BaselineVersionKey" size="small" placeholder="选择基准版本">
                    <el-option v-for="item in p52VersionNodes" :key="`base-${item.version_key}`" :label="item.label" :value="item.version_key" />
                  </el-select>
                </div>
                <div class="field">
                  <label>目标版本</label>
                  <el-select v-model="selectedP52TargetVersionKey" size="small" placeholder="选择目标版本">
                    <el-option v-for="item in p52VersionNodes" :key="`target-${item.version_key}`" :label="item.label" :value="item.version_key" />
                  </el-select>
                </div>
                </div>
                <div v-if="p52VersionDiffItems.length" class="issue-list compact-list">
                <div v-for="(item, index) in p52VersionDiffItems" :key="`ver-diff-${index}`" class="issue-card">
                  <div class="issue-head">
                    <div class="issue-title">{{ item.title }}</div>
                    <el-button type="text" size="mini" class="collapse-toggle" @click="toggleVersionDiffCard(index)">
                      {{ isVersionDiffCardExpanded(index) ? "收起" : "展开" }}
                    </el-button>
                  </div>
                  <div v-if="isVersionDiffCardExpanded(index)" class="detail-text">{{ item.detail }}</div>
                </div>
                </div>
                <div v-else class="empty">当前版本组合暂无可比较差异。</div>
              </div>
            </div>
          </div>
          <div v-if="feedbackOptimizeResult.feedback_key" class="evaluation-box">
            <div class="sub-title">优化结果评价</div>
            <div class="form-grid">
              <div class="field">
                <label>整体评价</label>
                <el-select v-model="optimizeEvaluationForm.overall_verdict" size="small">
                  <el-option label="未判断" value="unknown" />
                  <el-option label="有帮助" value="helpful" />
                  <el-option label="部分有帮助" value="partial" />
                  <el-option label="无帮助" value="unhelpful" />
                </el-select>
              </div>
            </div>
            <div class="field full">
              <label>评价说明</label>
              <el-input v-model="optimizeEvaluationForm.comment" type="textarea" :rows="3" placeholder="说明这次 patch 和元反思是否真的改善了结果。" />
            </div>
            <div class="actions">
              <el-button type="primary" size="small" @click="$emit('submit-optimize-evaluation', optimizeEvaluationForm)">提交评价</el-button>
            </div>
          </div>
        </div>

        <div v-if="isAdvancedSectionVisible('sectionRules')" class="panel">
          <div class="panel-title">章节规则</div>
          <div class="rule-summary">
            <span>总数 {{ sectionRulesSummary.total || 0 }}</span>
            <span>历史导入 {{ sectionRulesSummary.seed_rule_count || 0 }}</span>
            <span>导入 {{ sectionRulesSummary.import_rule_count || sectionRulesSummary.seed_rule_count || 0 }}</span>
            <span>Patch {{ sectionRulesSummary.feedback_patch_count || 0 }}</span>
          </div>
          <div v-if="sectionRuleItems.length">
            <div v-for="item in sectionRuleItems" :key="item.rule_id || item.rule_code" class="issue-card">
              <div class="issue-title">{{ item.rule_code || "-" }}</div>
              <div class="muted">来源类型：{{ item.source_type || "-" }}</div>
              <div class="muted">来源引用：{{ item.source_ref || "-" }}</div>
              <div class="list-item">{{ item.rule_text || "-" }}</div>
            </div>
          </div>
          <div v-else class="empty">当前章节没有可展示的章节规则。</div>
        </div>

        <prompt-rules-audit-panel
          v-if="isAdvancedSectionVisible('promptRules')"
          :selected-prompt-rules="selectedPromptRules"
          :prompt-rules-detail="promptRulesDetail"
        />
        <experience-memory-panel v-if="isAdvancedSectionVisible('experience')" :detail="experienceMemoryDetail" />

        <div v-if="isAdvancedSectionVisible('examples')" class="panel">
          <div class="panel-title">参考示例与评估样本</div>
          <div v-if="exampleItems.length">
            <div v-for="item in exampleItems" :key="item.example_id || item.title" class="issue-card">
              <div class="issue-title">{{ item.title || "未命名示例" }}</div>
              <div class="muted">类型：{{ exampleTypeLabel(item.example_type) }}</div>
              <div class="muted">更新时间：{{ item.update_time || "-" }}</div>
              <div class="list-item">{{ item.content || "-" }}</div>
            </div>
          </div>
          <div v-else class="empty">当前章节还没有参考示例或评估样本。</div>
        </div>

        <div v-if="isAdvancedSectionVisible('history')" class="panel">
          <div class="panel-title">反馈历史</div>
          <div class="rule-summary">
            <span>反馈 {{ feedbackHistorySummary.feedback_total || 0 }}</span>
            <span>优化事件 {{ feedbackHistorySummary.optimize_event_total || 0 }}</span>
            <span>Patch {{ feedbackHistorySummary.patch_total || 0 }}</span>
            <span>Accepted {{ feedbackHistorySummary.accepted_patch_total || 0 }}</span>
            <span v-if="feedbackHistorySummary.p52_optimize_event_total">P52 优化 {{ feedbackHistorySummary.p52_optimize_event_total || 0 }}</span>
            <span v-if="feedbackHistorySummary.p52_feedback_verify_event_total">P52 回放验证 {{ feedbackHistorySummary.p52_feedback_verify_event_total || 0 }}</span>
            <span v-if="feedbackHistorySummary.p52_meta_reflection_event_total">P52 元反思 {{ feedbackHistorySummary.p52_meta_reflection_event_total || 0 }}</span>
            <span v-if="feedbackHistorySummary.p52_ablation_event_total">P52 消融 {{ feedbackHistorySummary.p52_ablation_event_total || 0 }}</span>
          </div>
          <div v-if="signalEntries.length">
            <div v-for="(item, index) in signalEntries" :key="item.feedback_key || index" class="issue-card">
              <div class="issue-title">{{ item.feedback_key || `feedback-${index + 1}` }}</div>
              <div v-if="item.analysis_kind" class="muted">事件类型：{{ item.analysis_kind }}</div>
              <div class="muted">结论反馈：{{ feedbackSignalLabel(item.conclusion_feedback) }}</div>
              <div class="muted">检索反馈：{{ feedbackSignalLabel(item.retrieval_feedback) }}</div>
              <div class="muted">反馈类型：{{ item.feedback_type || "-" }} / decision：{{ item.decision || "-" }}</div>
              <div class="muted">优化状态：{{ item.feedback_optimize_status || "-" }} / replay：{{ item.replay_status || "-" }}</div>
              <div v-if="item.p52_error_family" class="muted">P52 错误家族：{{ item.p52_error_family }}</div>
              <div v-if="item.meta_reflection && item.meta_reflection.reflection_summary" class="muted">元反思：{{ item.meta_reflection.reflection_summary }}</div>
              <div v-if="item.ablation_result && item.ablation_result.summary" class="muted">
                消融实验：最佳变体 {{ item.ablation_result.summary.best_variant_id || "-" }} / 变体数 {{ item.ablation_result.summary.variant_count || 0 }}
              </div>
              <div v-if="historyIssueFamilyLabel(item) !== '-'" class="muted">问题归因层级：{{ historyIssueFamilyLabel(item) }}</div>
              <div v-if="item.optimize_evaluation && Object.keys(item.optimize_evaluation).length" class="muted">
                优化评价：{{ feedbackSignalLabel(item.optimize_evaluation.overall_verdict) }} / 分数：{{ numberText(item.optimize_evaluation.score) }}
              </div>
              <div v-if="historyPrimaryErrorTypeLabel(item) !== '-'" class="muted">主错误类型：{{ historyPrimaryErrorTypeLabel(item) }}</div>
              <div v-if="historyPatchTargetAgentText(item) !== '-'" class="muted">优化目标 Agent：{{ historyPatchTargetAgentText(item) }}</div>
              <div v-if="historyPatchCount(item) > 0" class="muted">Patch 数量：{{ historyPatchCount(item) }} / 类型：{{ historyPatchTypeText(item) }}</div>
              <div v-if="item.labels && item.labels.length" class="muted">标签：{{ item.labels.join(" / ") }}</div>
              <div v-if="item.evidence_feedback && item.evidence_feedback.length" class="muted">证据反馈：{{ summarizeEvidenceFeedback(item.evidence_feedback) }}</div>
              <div v-if="item.optimize_evaluation && item.optimize_evaluation.comment" class="muted">评价说明：{{ item.optimize_evaluation.comment }}</div>
              <div v-if="item.error_message" class="error-box">{{ item.error_message }}</div>
            </div>
          </div>
          <div v-else class="empty">当前章节还没有反馈历史。</div>
        </div>
      </div>
    </div>

    <el-dialog
      :visible.sync="auditDialogVisible"
      title="历史审计"
      width="86%"
      append-to-body
    >
      <execution-audit-panel
        :detail="executionAuditDetail"
        :feedback-optimize-result="feedbackOptimizeResult"
        :feedback-history="feedbackHistory"
        :ablation-detail="ablationDetail"
        :loading-ablation="loadingAblation"
        @run-ablation="$emit('run-ablation')"
      />
    </el-dialog>

    <span slot="footer">
      <el-button @click="$emit('update:visible', false)">关闭</el-button>
    </span>
  </el-dialog>
</template>

<script>
import { extractFactBasisPreview } from "@/utils/preReviewDisplay";
import ExecutionAuditPanel from "@/components/pre-review/ExecutionAuditPanel.vue";
import ExperienceMemoryPanel from "@/components/pre-review/ExperienceMemoryPanel.vue";
import PromptRulesAuditPanel from "@/components/pre-review/PromptRulesAuditPanel.vue";
import RetrievalTracePanel from "@/components/pre-review/RetrievalTracePanel.vue";
import RunMetricsPanel from "@/components/pre-review/RunMetricsPanel.vue";

const DEFAULT_EVIDENCE_LIMIT = 3;

export default {
  name: "PreReviewResultDialog",
  components: {
    ExecutionAuditPanel,
    ExperienceMemoryPanel,
    PromptRulesAuditPanel,
    RetrievalTracePanel,
    RunMetricsPanel,
  },
  props: {
    visible: { type: Boolean, default: false },
    review: { type: Object, default: null },
    currentBrowseSectionId: { type: String, default: "" },
    currentBrowseSectionLabel: { type: String, default: "" },
    reviewConclusionLabel: { type: Function, required: true },
    normalizedSupportedPoints: { type: Array, default: () => [] },
    normalizedUnsupportedPoints: { type: Array, default: () => [] },
    normalizedMissingPoints: { type: Array, default: () => [] },
    normalizedRiskPoints: { type: Array, default: () => [] },
    normalizedQuestions: { type: Array, default: () => [] },
    metrics: { type: Object, default: () => ({}) },
    retrievalDetail: { type: Object, default: () => ({}) },
    traceArtifact: { type: Object, default: () => ({}) },
    loadingTraceArtifact: { type: Boolean, default: false },
    approvedEvidenceGroups: { type: Array, default: () => [] },
    rejectedEvidenceGroups: { type: Array, default: () => [] },
    feedbackForm: { type: Object, required: true },
    selectedRun: { type: Object, default: null },
    submittingFeedback: { type: Boolean, default: false },
    feedbackOptimizeResult: { type: Object, default: () => ({}) },
    p52FeedbackPatches: { type: Array, default: () => [] },
    feedbackHistory: { type: Object, default: () => ({}) },
    sectionRulesDetail: { type: Object, default: () => ({}) },
    selectedPromptRules: { type: Object, default: () => ({}) },
    promptRulesDetail: { type: Object, default: () => ({}) },
    sectionExampleDetail: { type: Object, default: () => ({}) },
    experienceMemoryDetail: { type: Object, default: () => ({}) },
    executionAuditDetail: { type: Object, default: () => ({}) },
    ablationDetail: { type: Object, default: () => ({}) },
    loadingAblation: { type: Boolean, default: false },
    optimizeEvaluationForm: { type: Object, required: true },
    feedbackLoopMode: { type: String, default: "feedback_optimize" },
    feedbackLoopLocked: { type: Boolean, default: true },
    replayingFeedbackOptimize: { type: Boolean, default: false },
    replayingMetaReflection: { type: Boolean, default: false },
    replayingP52FeedbackVerify: { type: Boolean, default: false },
    submittingP52FeedbackOptimize: { type: Boolean, default: false },
    loadingP52FeedbackPatches: { type: Boolean, default: false },
    actingP52PatchId: { type: String, default: "" },
  },
  data() {
    return {
      auditDialogVisible: false,
      advancedSectionVisibility: {
        metrics: false,
        retrieval: false,
        chapterProfile: false,
        extractedFacts: false,
        sectionRules: false,
        promptRules: false,
        optimize: false,
        experience: false,
        examples: false,
        history: false,
      },
      expandedEvidenceSources: {},
      expandedJudgmentItems: {},
      expandedJudgmentFeedback: {},
      expandedP52Patches: {},
      collapsedEvidenceSections: {
        approved: false,
        rejected: false,
      },
      collapsedPanels: {
        feedbackInput: true,
        optimizeReplay: true,
        versionDiff: true,
      },
      defaultEvidenceLimit: DEFAULT_EVIDENCE_LIMIT,
      selectedP52PatchGroupKey: "",
      selectedP52PatchVersionKey: "",
      selectedP52BaselineVersionKey: "",
      selectedP52TargetVersionKey: "",
      expandedProcessChainCards: {},
      expandedWorkflowReplayCards: {},
      expandedVersionDiffCards: {},
    };
  },
  watch: {
    taskVerdictItems: {
      immediate: true,
      handler() {
        this.syncJudgmentFeedbackStates();
      },
    },
    structuredRiskItems: {
      immediate: true,
      handler() {
        if (this.isP52Section) {
          this.syncJudgmentFeedbackStates();
        }
      },
    },
    currentBrowseSectionId() {
      this.resetTransientViewState();
      this.syncJudgmentFeedbackStates();
    },
    p52CandidatePatches: {
      immediate: true,
      handler() {
        this.syncP52PatchVersionSelection();
      },
    },
    displayedReviewRuleItems: {
      immediate: true,
      handler() {
        this.syncJudgmentFeedbackStates();
      },
    },
    selectedP52PatchGroupKey() {
      this.syncP52PatchVersionSelection();
    },
    p52VersionNodes: {
      immediate: true,
      handler() {
        this.syncP52VersionCompareSelection();
      },
    },
    visible(value) {
      if (!value) {
        this.auditDialogVisible = false;
      }
    },
  },
  computed: {
    dialogTitle() {
      return `详细审评结论 - ${this.currentBrowseSectionLabel || "-"}`;
    },
    conclusionCode() {
      return String((this.review && (this.review.pre_review_conclusion || this.review.conclusion)) || "").trim();
    },
    normalizedConclusionText() {
      if (this.conclusionCode === "partially_supported") {
        return this.coreIssues.length ? "暂不满足（需补充）" : "部分支持";
      }
      return this.reviewConclusionLabel(this.conclusionCode || "-");
    },
    feedbackLoopModeLabel() {
      return this.feedbackLoopMode === "feedback_only" ? "仅记录反馈" : "反馈优化";
    },
    optimizeStatusText() {
      return this.feedbackOptimizeResult && this.feedbackOptimizeResult.feedback_optimize_status ? this.feedbackOptimizeResult.feedback_optimize_status : "-";
    },
    candidateStatusText() {
      return this.feedbackOptimizeResult && this.feedbackOptimizeResult.candidate_register_status ? this.feedbackOptimizeResult.candidate_register_status : "-";
    },
    replayStatusText() {
      return this.feedbackOptimizeResult && this.feedbackOptimizeResult.replay_status ? this.feedbackOptimizeResult.replay_status : "-";
    },
    primaryJudgmentItem() {
      if (this.riskyTaskVerdictItems.length) {
        return this.riskyTaskVerdictItems[0];
      }
      if (this.supportedTaskVerdictItems.length) {
        return this.supportedTaskVerdictItems[0];
      }
      return null;
    },
    coreConclusionText() {
      const primary = this.primaryJudgmentItem;
      if (primary) {
        if (this.judgmentOverview.risky > 0) {
          const focus = primary.problem || primary.task_question || "-";
          return `共识别 ${this.judgmentOverview.total} 个判断项，其中 ${this.judgmentOverview.risky} 个未通过或待补充。当前优先关注：${focus}`;
        }
        return `共识别 ${this.judgmentOverview.total} 个判断项，当前判断项均已通过，未发现明确不符合项。`;
      }
      return String((this.review && (this.review.section_summary || this.review.summary)) || "").trim();
    },
    reviewerConclusionLead() {
      const sectionSummary = String((this.review && (this.review.section_summary || this.review.summary)) || "").trim();
      if (sectionSummary) {
        return sectionSummary;
      }
      const question = this.coreReviewQuestionText !== "-" ? this.coreReviewQuestionText : (this.currentBrowseSectionLabel || "当前章节");
      if (!this.primaryJudgmentItem && this.reviewerOutline.summary) {
        return this.reviewerOutline.summary;
      }
      const primary = this.primaryJudgmentItem;
      if (primary) {
        const focus = primary.problem || primary.task_question || "当前重点判断项";
        if (this.judgmentOverview.risky > 0) {
          return `围绕“${question}”，当前共识别 ${this.judgmentOverview.total} 个判断项，其中 ${this.judgmentOverview.risky} 个尚未形成充分支撑。现阶段优先关注“${focus}”。`;
        }
        return `围绕“${question}”，当前已识别 ${this.judgmentOverview.total} 个判断项，并已形成对本章核心审评问题的直接支撑，暂未见明确不符合项。`;
      }
      return this.coreConclusionText || "当前没有可展示的审评判断。";
    },
    reviewerConclusionBasis() {
      const parts = [];
      if (this.primaryBasisText) {
        parts.push(`章节原文可直接提取的关键事实为：${this.primaryBasisText}`);
      }
      if (this.primaryRuleText) {
        parts.push(`本次判断主要依据的规则为：${this.primaryRuleText}`);
      }
      if (this.approvedEvidenceCount > 0) {
        parts.push(`本章共有 ${this.approvedEvidenceCount} 条通过检索评估并进入审评链路的证据。`);
      }
      if (!parts.length && this.coreConclusionText) {
        return this.coreConclusionText;
      }
      return parts.join(" ");
    },
    reviewerConclusionFollowUp() {
      if (this.mustAnswerCoverageOverview.uncovered > 0) {
        const pending = this.mustAnswerCoverage
          .filter((item) => item.coverage_status === "uncovered")
          .slice(0, 2)
          .map((item) => item.question)
          .filter(Boolean);
        if (pending.length) {
          return `建议优先围绕“${pending.join("”“")}”补足直接判断项和证据链，避免本章必答问题仍停留在未覆盖状态。`;
        }
      }
      if (this.judgmentOverview.risky > 0) {
        const pendingItems = this.riskyTaskVerdictItems
          .slice(0, 2)
          .map((item) => item.problem || item.task_question || "")
          .filter(Boolean);
        if (pendingItems.length) {
          return `建议优先补强“${pendingItems.join("”“")}”相关的原文事实、规则依据或直接证据，确保判断项能够闭合到明确结论。`;
        }
        return "建议继续补充原文事实、规则依据和直接证据，避免判断停留在概括性描述层面。";
      }
      if (this.mustAnswerQuestions.length) {
        return `建议后续继续围绕“${this.mustAnswerQuestions.slice(0, 2).join("；")}”保持事实、规则和证据链的一致性。`;
      }
      return "建议继续保持当前事实表述、规则依据和证据链的一致性，避免后续版本出现信息漂移。";
    },
    llmExecution() {
      return this.review && typeof this.review.llm_execution === "object" ? this.review.llm_execution : {};
    },
    llmExecutionLabel() {
      if (!this.review) {
        return "-";
      }
      return this.llmExecution.used_default_fallback ? "默认 fallback" : "大模型输出";
    },
    llmExecutionHint() {
      if (!this.review) {
        return "-";
      }
      if (this.llmExecution.used_default_fallback) {
        const reason = String(this.llmExecution.failure_reason || "").trim();
        const preview = String(this.llmExecution.raw_preview || "").trim();
        if (reason && preview) {
          return `${reason}；raw preview：${preview}`;
        }
        return reason || "模型返回未解析为结构化 JSON，当前展示的是默认回退结果。";
      }
      return this.llmExecution.json_parse_ok ? "已成功解析模型输出。" : "-";
    },
    primaryBasisText() {
      if (this.primaryJudgmentItem && this.primaryJudgmentItem.material_fact) {
        return this.primaryJudgmentItem.material_fact;
      }
      return extractFactBasisPreview(this.review && this.review.fact_basis, 3);
    },
    primaryRuleText() {
      if (this.primaryJudgmentItem && (this.primaryJudgmentItem.basis || this.primaryJudgmentItem.rule_requirement)) {
        return this.primaryJudgmentItem.basis || this.primaryJudgmentItem.rule_requirement;
      }
      const rules = Array.isArray(this.review && this.review.linked_rules) ? this.review.linked_rules : [];
      return rules.slice(0, 3).join("；");
    },
    taskDefinition() {
      return this.review && typeof this.review.task_definition === "object" ? this.review.task_definition : {};
    },
    reviewerOutline() {
      return this.review && typeof this.review.reviewer_outline === "object" ? this.review.reviewer_outline : {};
    },
    sectionReviewProfile() {
      return this.review && typeof this.review.section_review_profile === "object" ? this.review.section_review_profile : {};
    },
    chapterRoleText() {
      return String(this.reviewerOutline.chapter_role || this.sectionReviewProfile.chapter_role || this.taskDefinition.chapter_role || "").trim() || "-";
    },
    coreReviewQuestionText() {
      return String(this.reviewerOutline.core_review_question || this.sectionReviewProfile.core_review_question || this.taskDefinition.core_review_question || this.taskDefinition.review_goal || "").trim() || "-";
    },
    mustAnswerQuestions() {
      const values = Array.isArray(this.reviewerOutline.must_answer_questions) && this.reviewerOutline.must_answer_questions.length
        ? this.reviewerOutline.must_answer_questions
        : (Array.isArray(this.sectionReviewProfile.must_answer_questions) && this.sectionReviewProfile.must_answer_questions.length
          ? this.sectionReviewProfile.must_answer_questions
          : this.taskDefinition.must_answer_questions);
      return Array.isArray(values)
        ? values.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
    },
    mustAnswerCoverage() {
      const rows = Array.isArray(this.review && this.review.must_answer_coverage) ? this.review.must_answer_coverage : [];
      return rows
        .filter((item) => item && typeof item === "object" && String(item.question || "").trim())
        .map((item) => ({
          question: String(item.question || "").trim(),
          coverage_status: String(item.coverage_status || "").trim().toLowerCase() || "uncovered",
          verdict_status: String(item.verdict_status || "").trim().toLowerCase() || "unknown",
          reason: String(item.reason || "").trim(),
        }));
    },
    mustAnswerCoverageOverview() {
      return {
        total: this.mustAnswerCoverage.length,
        covered: this.mustAnswerCoverage.filter((item) => item.coverage_status === "covered").length,
        partial: this.mustAnswerCoverage.filter((item) => item.coverage_status === "partial").length,
        uncovered: this.mustAnswerCoverage.filter((item) => item.coverage_status === "uncovered").length,
      };
    },
    reasoningPrinciples() {
      const values = Array.isArray(this.taskDefinition.reasoning_principles) && this.taskDefinition.reasoning_principles.length
        ? this.taskDefinition.reasoning_principles
        : this.sectionReviewProfile.core_review_principles;
      return Array.isArray(values)
        ? values.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
    },
    commonRisks() {
      const values = Array.isArray(this.taskDefinition.common_risks) && this.taskDefinition.common_risks.length
        ? this.taskDefinition.common_risks
        : this.sectionReviewProfile.common_risks;
      return Array.isArray(values)
        ? values.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
    },
    hasSectionReviewProfile() {
      return this.chapterRoleText !== "-"
        || this.coreReviewQuestionText !== "-"
        || this.mustAnswerQuestions.length > 0
        || this.mustAnswerCoverage.length > 0
        || this.reasoningPrinciples.length > 0
        || this.commonRisks.length > 0;
    },
    medicalKeyEntities() {
      return Array.isArray(this.review && this.review.medical_key_entities) ? this.review.medical_key_entities : [];
    },
    medicalKeyDataPoints() {
      return Array.isArray(this.review && this.review.medical_key_data_points) ? this.review.medical_key_data_points : [];
    },
    hasExtractedMedicalFacts() {
      return this.medicalKeyEntities.length > 0 || this.medicalKeyDataPoints.length > 0;
    },
    displayedReviewRules() {
      const review = this.review && typeof this.review === "object" ? this.review : {};
      return [review.section_rules, review.focus_points, review.linked_rules]
        .filter((items) => Array.isArray(items))
        .reduce((rows, items) => rows.concat(items), []);
    },
    displayedReviewRuleItems() {
      const sourceRows = Array.isArray(this.displayedReviewRules) ? this.displayedReviewRules : [];
      const findingRows = Array.isArray(this.review && this.review.rule_findings)
        ? this.review.rule_findings.filter((item) => item && typeof item === "object")
        : [];
      const normalized = [];
      const seen = new Set();
      const addRow = (source, sourceIndex, finding = null) => {
        const row = source && typeof source === "object" ? source : {};
        const raw = typeof source === "string" ? String(source || "").trim() : "";
        const rawCode = raw ? String(raw.split(":", 1)[0] || "").trim() : "";
        const match = finding || findingRows.find((candidate) => {
          const candidateCode = String(candidate.rule_code || candidate.rule_id || "").trim();
          const candidateText = String(candidate.rule_text || candidate.requirement_point || "").trim();
          const rowCode = String(row.rule_code || row.rule_id || row.code || rawCode).trim();
          const rowText = String(row.rule_text || row.requirement_point || row.text || raw).trim();
          return (rowCode && candidateCode === rowCode) || (rowText && candidateText === rowText);
        }) || {};
        const ruleCode = String(row.rule_code || row.rule_id || row.code || match.rule_code || match.rule_id || rawCode).trim();
        const ruleText = String(
          row.rule_text || row.requirement_point || row.rule_clause || row.text || row.name
          || match.rule_text || match.requirement_point || match.rule_clause || raw
        ).trim();
        const key = `${ruleCode}::${ruleText}` || `rule-${sourceIndex}`;
        if ((!ruleCode && !ruleText) || seen.has(key)) {
          return;
        }
        seen.add(key);
        const displayText = ruleCode && ruleText && !ruleText.startsWith(ruleCode)
          ? `${ruleCode} ${ruleText}`
          : (ruleText || ruleCode);
        normalized.push({
          feedback_kind: "rule_finding",
          feedback_key: `rule_finding:${ruleCode || sourceIndex}:${ruleText.slice(0, 64)}`,
          rule_code: ruleCode,
          rule_text: ruleText,
          rule_requirement: ruleText,
          requirement_point: String(match.requirement_point || match.rule_clause || ruleText).trim(),
          display_text: displayText,
          status: String(match.status || match.issue_type || row.status || "").trim().toLowerCase(),
          issue: String(match.issue || match.problem || "").trim(),
          problem: String(match.issue || match.problem || "").trim(),
          evidence: String(match.evidence || "").trim(),
          evidence_support: String(match.evidence || "").trim(),
          reason: String(match.reason || "").trim(),
          advice: String(match.advice || match.suggested_fix || match.requested_action || "").trim(),
          task_code: ruleCode,
          task_question: displayText,
        });
      };
      sourceRows.forEach((item, index) => addRow(item, index));
      findingRows.forEach((item, index) => addRow(item, sourceRows.length + index, item));
      return normalized;
    },
    coreIssues() {
      return [...this.normalizedUnsupportedPoints, ...this.normalizedMissingPoints].slice(0, 5);
    },
    isP52Section() {
      const value = String(this.currentBrowseSectionId || "").trim().toLowerCase();
      return value === "3.2.p.5.2" || value.startsWith("3.2.p.5.2.");
    },
    hasVisibleAdvancedSection() {
      return Object.values(this.advancedSectionVisibility || {}).some(Boolean);
    },
    hasExecutionAudits() {
      return Array.isArray(this.executionAuditDetail && this.executionAuditDetail.items)
        && this.executionAuditDetail.items.length > 0;
    },
    visibleApprovedEvidenceGroups() {
      return this.buildVisibleEvidenceGroups(this.approvedEvidenceGroups, "approved");
    },
    approvedEvidenceItems() {
      const seen = new Set();
      return (Array.isArray(this.approvedEvidenceGroups) ? this.approvedEvidenceGroups : []).reduce((rows, group) => {
        const items = Array.isArray(group && group.items) ? group.items : [];
        items.forEach((item) => {
          const score = Number(item && item.score);
          if (!Number.isFinite(score) || score < 0.5) {
            return;
          }
          const key = this.evidenceKey(item);
          if (!key || seen.has(key)) {
            return;
          }
          seen.add(key);
          rows.push(item);
        });
        return rows;
      }, []);
    },
    approvedEvidenceDisplayMap() {
      const mapping = {};
      this.approvedEvidenceItems.forEach((item) => {
        const label = this.evidenceName(item);
        const evidenceId = String((item && item.evidence_id) || "").trim();
        const docId = String((item && item.doc_id) || "").trim();
        const chunkId = String((item && item.chunk_id) || "").trim();
        const title = String((item && (item.title || item.doc_title)) || "").trim();
        const aliases = [
          evidenceId,
          docId && chunkId ? `${docId}:${chunkId}` : "",
          docId,
          title,
          item && item.source_type && title ? `${String(item.source_type).trim()}:${title}` : "",
        ].filter(Boolean);
        aliases.forEach((alias) => {
          if (!mapping[alias]) {
            mapping[alias] = label;
          }
          if (String(alias).includes(":")) {
            const suffix = String(alias).split(":").slice(1).join(":").trim();
            if (suffix && !mapping[suffix]) {
              mapping[suffix] = label;
            }
          }
        });
      });
      return mapping;
    },
    approvedEvidenceCount() {
      return this.visibleApprovedEvidenceGroups.reduce((total, group) => total + Number(group.total || 0), 0);
    },
    taskVerdictItems() {
      const verdictRows = Array.isArray(this.review && this.review.task_verdicts) ? this.review.task_verdicts : [];
      const reasoningMap = {};
      this.reasoningChainItems.forEach((item) => {
        const key = String(item.task_code || "").trim();
        if (key) {
          reasoningMap[key] = item;
        }
      });
      return verdictRows
        .filter((item) => item && typeof item === "object")
        .map((item) => {
          const taskCode = String(item.task_code || "").trim();
          const reasoning = reasoningMap[taskCode] || {};
          return {
            task_code: taskCode,
            status: String(item.status || "").trim().toLowerCase(),
            task_question: String(item.task_question || "").trim(),
            problem: String(item.problem || item.issue || "").trim(),
            basis: String(item.basis || "").trim(),
            reason: this.normalizeEvidenceMentionsInText(String(item.reason || "").trim()),
            advice: String(item.advice || item.suggested_fix || "").trim(),
            rule_requirement: String(reasoning.rule_requirement || "").trim(),
            material_fact: String(reasoning.material_fact || "").trim(),
            evidence_support: this.resolveEvidenceSupportText(reasoning.evidence_support),
            comparison: String(reasoning.comparison || "").trim(),
            judgment_reason: this.normalizeEvidenceMentionsInText(String(reasoning.judgment_reason || "").trim()),
          };
        })
        .filter((item) => item.task_question || item.problem || item.basis || item.reason);
    },
    judgmentOverview() {
      return {
        total: this.taskVerdictItems.length,
        supported: this.taskVerdictItems.filter((item) => item.status === "supported").length,
        risky: this.taskVerdictItems.filter((item) => item.status && item.status !== "supported").length,
      };
    },
    riskyTaskVerdictItems() {
      return this.taskVerdictItems.filter((item) => item.status && item.status !== "supported");
    },
    supportedTaskVerdictItems() {
      return this.taskVerdictItems.filter((item) => item.status === "supported");
    },
    reasoningChainItems() {
      const reasoningRows = Array.isArray(this.review && this.review.reasoning_chain_items) ? this.review.reasoning_chain_items : [];
      const verdictMap = {};
      const verdictRows = Array.isArray(this.review && this.review.task_verdicts) ? this.review.task_verdicts : [];
      verdictRows.forEach((item) => {
        const key = String((item && item.task_code) || "").trim();
        if (key && !verdictMap[key]) {
          verdictMap[key] = item;
        }
      });
      return reasoningRows
        .filter((item) => item && typeof item === "object")
        .map((item) => {
          const taskCode = String(item.task_code || "").trim();
          const verdict = verdictMap[taskCode] || {};
          return {
            task_code: taskCode,
            task_question: String(verdict.task_question || "").trim(),
            rule_requirement: String(item.rule_requirement || "").trim(),
            material_fact: String(item.material_fact || "").trim(),
            evidence_support: this.resolveEvidenceSupportText(item.evidence_support),
            comparison: String(item.comparison || "").trim(),
            judgment_reason: this.normalizeEvidenceMentionsInText(String(item.judgment_reason || item.reason || "").trim()),
          };
        })
        .filter((item) => item.task_question || item.rule_requirement || item.material_fact || item.evidence_support);
    },
    problemLocationItems() {
      // P52 风险展示优先采用规则层输出，避免 task_verdict 与 rule_findings 同义重复堆叠。
      if (this.ruleFindingItems.length) {
        const dedupedRuleRows = [];
        const seenRule = new Set();
        this.ruleFindingItems.forEach((item) => {
          const ruleCode = String(item.rule_code || "").trim();
          const issue = String(item.issue || "").trim();
          const key = `${ruleCode}::${issue}`;
          if (!key || seenRule.has(key)) {
            return;
          }
          seenRule.add(key);
          dedupedRuleRows.push(item);
        });
        return dedupedRuleRows;
      }
      const rows = [];
      const seen = new Set();
      this.taskVerdictItems
        .filter((item) => item.status && item.status !== "supported")
        .forEach((item) => {
          const row = {
            location: "任务判断未闭合",
            rule_code: item.task_code,
            rule_text: item.rule_requirement,
            requirement_point: item.rule_requirement || item.basis,
            status: item.status,
            task_question: item.task_question,
            issue: item.problem,
            problem: item.problem,
            evidence: item.evidence_support || item.basis,
            reason: item.reason || item.judgment_reason,
            suggested_fix: item.advice,
            advice: item.advice,
          };
          const key = [row.problem, row.requirement_point, row.reason, row.evidence].join("|");
          if (!seen.has(key)) {
            seen.add(key);
            rows.push(row);
          }
        });
      this.ruleFindingItems.forEach((item) => {
        const key = [item.issue, item.requirement_point, item.evidence, item.suggested_fix].join("|");
        if (!seen.has(key)) {
          seen.add(key);
          rows.push(item);
        }
      });
      return rows;
    },
    structuredRiskItems() {
      const sourceRows = Array.isArray(this.review && this.review.risk_items) && this.review.risk_items.length
        ? this.review.risk_items
        : this.problemLocationItems;
      const levelOrder = { high: 0, medium: 1, low: 2, unknown: 3 };
      const levelLabels = { high: '高风险', medium: '中风险', low: '低风险', unknown: '未知' };
      const issueStatusAlias = {
        unsupported: 'non_compliance',
        issue: 'non_compliance',
        non_compliance: 'non_compliance',
        missing: 'missing',
        insufficient_information: 'question',
        question: 'question',
        compliant: 'compliant',
        supported: 'compliant',
        inconsistency: 'inconsistency',
        unreasonable: 'unreasonable',
      };
      const issueLabels = {
        missing: '缺失',
        inconsistency: '不一致',
        unreasonable: '不合理',
        non_compliance: '不满足',
        compliant: '已满足',
        question: '待补充',
        unknown: '未分类',
      };
      const sourceLabels = {
        'CTD申报资料': 'CTD申报资料',
        '指南': '指南',
        '法规': '法规',
        '药典数据': '药典数据',
        '历史经验': '历史经验',
      };
      return (Array.isArray(sourceRows) ? sourceRows : [])
        .filter((item) => item && typeof item === 'object')
        .map((item, index) => {
          const issueTypeRaw = String(item.issue_type || item.problem_type || item.status || '').trim().toLowerCase();
          const issueType = issueStatusAlias[issueTypeRaw] || issueTypeRaw || 'unknown';
          const riskLevel = String(item.risk_level || item.severity || (String(item.status || '').trim().toLowerCase() === 'supported' ? 'low' : 'medium')).trim().toLowerCase() || 'unknown';
          const sourceType = String(item.source_type || (item.trace && item.trace.source_type) || '').trim() || 'CTD申报资料';
          const riskDomain = String(item.risk_domain || (item.trace && item.trace.risk_domain) || '').trim() || '跨模块风险';
          const evidence = this.formatStructuredRiskEvidence(item);
          const recommendation = this.formatStructuredRiskRecommendation(item);
          const confidence = String(item.confidence || '').trim() || (evidence ? 'medium' : 'low');
          const title = this.formatStructuredRiskTitle(item, index);
          return {
            ...item,
            issue_type: issueType,
            issue_type_label: issueLabels[issueType] || issueType || '风险项',
            risk_level: riskLevel,
            risk_level_label: levelLabels[riskLevel] || levelLabels.unknown,
            source_type: sourceType,
            source_type_label: sourceLabels[sourceType] || sourceType,
            risk_domain: riskDomain,
            risk_domain_label: riskDomain,
            evidence,
            recommendation,
            confidence,
            title,
            _sort_key: [levelOrder[riskLevel] ?? 3, riskDomain, issueType, index],
          };
        })
        .filter((item) => item.title || item.evidence || item.recommendation)
        .sort((a, b) => {
          const left = Array.isArray(a._sort_key) ? a._sort_key : [3, '', '', 0];
          const right = Array.isArray(b._sort_key) ? b._sort_key : [3, '', '', 0];
          return left[0] - right[0] || String(left[1]).localeCompare(String(right[1])) || String(left[2]).localeCompare(String(right[2])) || left[3] - right[3];
        })
        .map((item) => {
          const next = { ...item };
          delete next._sort_key;
          return next;
        });
    },
    riskSummary() {

      const rows = this.structuredRiskItems;
      return {
        total: rows.length,
        high: rows.filter((item) => item.risk_level === 'high').length,
        medium: rows.filter((item) => item.risk_level === 'medium').length,
        low: rows.filter((item) => item.risk_level === 'low').length,
      };
    },
    primaryErrorType() {
      const result = this.feedbackOptimizeResult && this.feedbackOptimizeResult.analysis_result;
      if (result && typeof result === "object") {
        const classification = result.error_classification && typeof result.error_classification === "object"
          ? result.error_classification
          : {};
        return String(classification.primary_error_type || result.primary_error_type || "").trim() || "-";
      }
      return "-";
    },
    feedbackProjection() {
      return this.feedbackOptimizeResult
        && this.feedbackOptimizeResult.feedback_projection
        && typeof this.feedbackOptimizeResult.feedback_projection === "object"
        ? this.feedbackOptimizeResult.feedback_projection
        : {};
    },
    feedbackIssueFamilyLabel() {
      if (this.isP52Section && this.primaryErrorType !== "-") {
        return this.issueFamilyLabelFromError(this.primaryErrorType);
      }
      return String(this.feedbackProjection.issue_family_label || "").trim() || this.issueFamilyLabelFromError(this.primaryErrorType);
    },
    feedbackTargetLayerText() {
      if (this.isP52Section) {
        const values = this.p52TargetLocalization.map((item) => String(item.patch_type || "").trim()).filter(Boolean);
        return values.length ? [...new Set(values)].join(" / ") : "-";
      }
      return String(this.feedbackProjection.target_layer_label || "").trim() || "-";
    },
    primaryErrorTypeLabel() {
      return String(this.feedbackProjection.primary_error_label || "").trim() || this.errorTypeLabel(this.primaryErrorType);
    },
    patchCount() {
      if (this.isP52Section) {
        return this.p52CandidatePatches.length;
      }
      const projected = Number(this.feedbackProjection.patch_count);
      if (Number.isFinite(projected)) {
        return projected;
      }
      const result = this.feedbackOptimizeResult && this.feedbackOptimizeResult.patch_result;
      return Array.isArray(result && result.patches) ? result.patches.length : 0;
    },
    patchTargetAgentText() {
      if (this.isP52Section) {
        const values = [...new Set(this.p52CandidatePatches.map((item) => String(item.target_agent || "").trim()).filter(Boolean))];
        return values.length ? values.join(" / ") : "-";
      }
      const labels = Array.isArray(this.feedbackProjection.target_agent_labels) ? this.feedbackProjection.target_agent_labels : [];
      if (labels.length) {
        return labels.join(" / ");
      }
      const agents = this.patchTargetAgentsFromResult(this.feedbackOptimizeResult && this.feedbackOptimizeResult.patch_result);
      return agents.length ? agents.map((item) => this.targetAgentLabel(item)).join(" / ") : "-";
    },
    patchTypeText() {
      if (this.isP52Section) {
        const values = [...new Set(this.p52CandidatePatches.map((item) => String(item.patch_type || "").trim()).filter(Boolean))];
        return values.length ? values.join(" / ") : "-";
      }
      const patchTypes = Array.isArray(this.feedbackProjection.patch_types) ? this.feedbackProjection.patch_types : [];
      if (patchTypes.length) {
        return patchTypes.join(" / ");
      }
      const result = this.feedbackOptimizeResult && this.feedbackOptimizeResult.patch_result;
      const patches = Array.isArray(result && result.patches) ? result.patches : [];
      const values = [...new Set(patches.map((item) => String((item && item.patch_type) || "").trim()).filter(Boolean))];
      return values.length ? values.join(" / ") : "-";
    },
    patchScopeText() {
      if (this.isP52Section) {
        const values = [...new Set(this.p52CandidatePatches.map((item) => String(item.target_scope || "").trim()).filter(Boolean))];
        return values.length ? values.join(" / ") : "-";
      }
      const scopes = Array.isArray(this.feedbackProjection.patch_scope_labels) ? this.feedbackProjection.patch_scope_labels : [];
      return scopes.length ? scopes.join(" / ") : "-";
    },
    knowledgeCategoryText() {
      const categories = Array.isArray(this.feedbackProjection.knowledge_categories) ? this.feedbackProjection.knowledge_categories : [];
      return categories.length ? categories.join(" / ") : "-";
    },
    metaReflectionSummary() {
      const result = this.feedbackOptimizeResult && this.feedbackOptimizeResult.meta_reflection;
      return String((result && (result.reflection_summary || result.summary)) || "").trim() || "-";
    },
    p52ErrorClassification() {
      const result = this.feedbackOptimizeResult && this.feedbackOptimizeResult.analysis_result;
      return result && result.error_classification && typeof result.error_classification === "object"
        ? result.error_classification
        : {};
    },
    p52ErrorReasonList() {
      const values = Array.isArray(this.p52ErrorClassification.reasons) ? this.p52ErrorClassification.reasons : [];
      return values.map((item) => String(item || "").trim()).filter(Boolean);
    },
    p52ErrorConfidenceText() {
      return String(this.p52ErrorClassification.confidence || "").trim() || "-";
    },
    p52ErrorMethodProfilesText() {
      const values = Array.isArray(this.p52ErrorClassification.method_profiles) ? this.p52ErrorClassification.method_profiles : [];
      return values.length ? values.join(" / ") : "-";
    },
    p52ErrorRuleIdsText() {
      const values = Array.isArray(this.p52ErrorClassification.candidate_rule_ids) ? this.p52ErrorClassification.candidate_rule_ids : [];
      return values.length ? values.join(" / ") : "-";
    },
    p52TargetLocalization() {
      const result = this.feedbackOptimizeResult && this.feedbackOptimizeResult.analysis_result;
      return Array.isArray(result && result.target_localization) ? result.target_localization : [];
    },
    p52CandidatePatches() {
      if (Array.isArray(this.p52FeedbackPatches) && this.p52FeedbackPatches.length) {
        return this.p52FeedbackPatches;
      }
      return Array.isArray(this.feedbackOptimizeResult && this.feedbackOptimizeResult.candidate_patches)
        ? this.feedbackOptimizeResult.candidate_patches
        : [];
    },
    p52PatchGroups() {
      const groups = {};
      this.p52CandidatePatches.forEach((item, index) => {
        const groupKey = this.p52PatchGroupKey(item, index);
        if (!groups[groupKey]) {
          groups[groupKey] = {
            group_key: groupKey,
            group_label: this.p52PatchGroupLabel(item, index),
            versions: [],
          };
        }
        groups[groupKey].versions.push(item);
      });
      return Object.values(groups)
        .map((group) => ({
          ...group,
          versions: group.versions.slice().sort((a, b) => this.p52PatchVersionNo(b) - this.p52PatchVersionNo(a)),
        }))
        .sort((a, b) => a.group_label.localeCompare(b.group_label));
    },
    selectedP52PatchGroupVersions() {
      const selected = this.p52PatchGroups.find((item) => item.group_key === this.selectedP52PatchGroupKey);
      const versions = selected ? selected.versions : [];
      return versions.map((item) => ({
        version_key: this.p52PatchVersionKey(item),
        version_label: this.p52PatchVersionLabel(item),
        patch: item,
      }));
    },
    p52VisibleCandidatePatches() {
      const selectedVersion = this.selectedP52PatchVersionKey;
      if (!selectedVersion) {
        return this.p52CandidatePatches;
      }
      return this.p52CandidatePatches.filter((item) => this.p52PatchVersionKey(item) === selectedVersion);
    },
    selectedP52PatchVersionDiffText() {
      const selectedGroup = this.p52PatchGroups.find((item) => item.group_key === this.selectedP52PatchGroupKey);
      if (!selectedGroup || selectedGroup.versions.length < 2) {
        return "-";
      }
      const versions = selectedGroup.versions.slice().sort((a, b) => this.p52PatchVersionNo(a) - this.p52PatchVersionNo(b));
      const latest = versions[versions.length - 1];
      const previous = versions[versions.length - 2];
      const fields = [];
      if (String(latest.patch_content || "") !== String(previous.patch_content || "")) {
        fields.push("Patch 内容");
      }
      if (String(latest.status || "") !== String(previous.status || "")) {
        fields.push("状态");
      }
      if (this.p52PatchRuleIdsText(latest) !== this.p52PatchRuleIdsText(previous)) {
        fields.push("候选规则");
      }
      if (this.p52PatchMethodProfilesText(latest) !== this.p52PatchMethodProfilesText(previous)) {
        fields.push("方法域");
      }
      if (!fields.length) {
        return "与上一版无明显字段差异";
      }
      return `相对上一版主要变化：${fields.join("、")}`;
    },
    p52WorkflowReplayItems() {
      const rows = Array.isArray(this.signalEntries) ? this.signalEntries : [];
      return rows
        .filter((item) => item && typeof item === "object")
        .filter((item) => ["p52_feedback_optimize", "p52_feedback_verify", "p52_meta_reflection", "p52_ablation"].includes(String(item.analysis_kind || "").trim()))
        .map((item) => ({
          analysis_kind: String(item.analysis_kind || "").trim(),
          stage_label: this.p52WorkflowStageLabel(item.analysis_kind),
          event_time: String(item.created_at || item.created_time || item.event_time || "").trim(),
          summary: this.p52WorkflowSummaryText(item),
        }))
        .reverse();
    },
    p52ProcessParameterSummary() {
      return "Y0、F、τ0、A、P、Y_replay、P*、E";
    },
    p52ProcessChainCards() {
      const cards = [];
      cards.push({
        symbol: "Y0",
        label: "原始章节输出",
        stage: "初始执行",
        summary: String((this.review && (this.review.section_summary || this.review.summary)) || "").trim() || "暂无原始结论摘要",
      });
      cards.push({
        symbol: "F",
        label: "标准化反馈记录",
        stage: "问题输入",
        summary: String((this.feedbackForm && this.feedbackForm.feedback_text) || "").trim() || "暂无反馈文本",
      });
      cards.push({
        symbol: "τ0",
        label: "原始运行轨迹",
        stage: "执行轨迹",
        summary: this.traceArtifact && Object.keys(this.traceArtifact).length ? "已记录原始运行轨迹（可在执行审计中查看）" : "当前无原始轨迹快照",
      });
      cards.push({
        symbol: "A",
        label: "错误归因结果",
        stage: "归因分析",
        summary: this.p52AttributionSummaryText,
      });
      cards.push({
        symbol: "P",
        label: "候选补丁集合",
        stage: "补丁生成",
        summary: this.p52CandidatePatches.length ? `候选补丁 ${this.p52CandidatePatches.length} 个` : "暂无候选补丁",
      });
      cards.push({
        symbol: "Y_replay",
        label: "候选补丁重放结果",
        stage: "回放验证",
        summary: this.p52VerificationPlanAvailable ? `验证结论：${this.p52VerificationVerdictLabel}` : "暂无重放验证结果",
      });
      const approvedCount = this.p52CandidatePatches.filter((item) => String((item && item.status) || "").trim().toLowerCase() === "approved").length;
      cards.push({
        symbol: "P*",
        label: "人工最终确认补丁",
        stage: "人工确认",
        summary: approvedCount ? `已确认补丁 ${approvedCount} 个` : "尚无人工确认补丁",
      });
      cards.push({
        symbol: "E",
        label: "经验沉淀集合",
        stage: "经验固化",
        summary: this.p52MetaDistilledExperiences.length ? `沉淀经验 ${this.p52MetaDistilledExperiences.length} 条` : "尚无经验沉淀",
      });
      return cards;
    },
    p52AttributionSummaryText() {
      const classification = this.p52ErrorClassification && typeof this.p52ErrorClassification === "object"
        ? this.p52ErrorClassification
        : {};
      const primary = String(
        classification.primary_error_type
        || this.primaryErrorType
        || ""
      ).trim();
      const reasons = Array.isArray(classification.reasons)
        ? classification.reasons.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      if (primary) {
        const label = this.errorTypeLabel(primary);
        return reasons.length ? `主错误类型：${label}；归因说明：${reasons.join("；")}` : `主错误类型：${label}`;
      }
      const entries = Array.isArray(this.signalEntries) ? this.signalEntries : [];
      for (let i = entries.length - 1; i >= 0; i -= 1) {
        const row = entries[i];
        if (!row || typeof row !== "object") {
          continue;
        }
        const result = row.analysis_result && typeof row.analysis_result === "object" ? row.analysis_result : {};
        const cls = result.error_classification && typeof result.error_classification === "object" ? result.error_classification : {};
        const rowPrimary = String(cls.primary_error_type || result.primary_error_type || "").trim();
        if (!rowPrimary) {
          continue;
        }
        const rowReasons = Array.isArray(cls.reasons) ? cls.reasons.map((item) => String(item || "").trim()).filter(Boolean) : [];
        const rowLabel = this.errorTypeLabel(rowPrimary);
        return rowReasons.length ? `主错误类型：${rowLabel}；归因说明：${rowReasons.join("；")}` : `主错误类型：${rowLabel}`;
      }
      return "暂无归因结果";
    },
    p52VersionNodes() {
      const replayIndex = this.p52PerPatchReplayIndex;
      const nodes = [];
      nodes.push({
        version_key: "Y0",
        label: "Y0 原始版本",
        type: "origin",
        patch_id: "",
        summary: String((this.review && (this.review.section_summary || this.review.summary)) || "").trim(),
        unsupported_count: Number((this.review && this.review.unsupported_count) || NaN),
        insufficient_count: Number((this.review && this.review.insufficient_count) || NaN),
        task_count: Number((this.review && this.review.task_count) || NaN),
        conclusion: String((this.review && (this.review.pre_review_conclusion || this.review.conclusion)) || "").trim(),
      });
      this.p52CandidatePatches.forEach((item) => {
        const patchId = String((item && item.patch_id) || "").trim();
        const versionNo = this.p52PatchVersionNo(item);
        const statusText = this.p52PatchStatusLabel(item && item.status);
        const replayItem = patchId ? (replayIndex[patchId] || null) : null;
        const replayCurrentCase = replayItem && replayItem.rerun_cases && typeof replayItem.rerun_cases.current_case === "object"
          ? replayItem.rerun_cases.current_case
          : null;
        const replayPatched = replayCurrentCase && replayCurrentCase.patched && typeof replayCurrentCase.patched === "object"
          ? replayCurrentCase.patched
          : {};
        nodes.push({
          version_key: patchId || `P-${versionNo}`,
          label: `候选 ${patchId || `v${versionNo || "-"}`}（${statusText}）`,
          type: "candidate",
          patch_id: patchId,
          summary: String(replayPatched.section_summary || "").trim() || this.p52PatchTraceSummaryText(item),
          unsupported_count: Number(replayPatched.unsupported_count),
          insufficient_count: Number(replayPatched.insufficient_count),
          task_count: Number(replayPatched.task_count),
          conclusion: String(replayPatched.conclusion || "").trim(),
          patch: item,
          replay_result: replayItem,
        });
      });
      return nodes;
    },
    p52PerPatchReplayIndex() {
      const plan = this.p52VerificationPlan && typeof this.p52VerificationPlan === "object" ? this.p52VerificationPlan : {};
      const rows = Array.isArray(plan.per_patch_replay_results) ? plan.per_patch_replay_results : [];
      const out = {};
      rows.forEach((item) => {
        if (!item || typeof item !== "object") {
          return;
        }
        const patchId = String(item.patch_id || "").trim();
        if (!patchId) {
          return;
        }
        out[patchId] = item;
      });
      return out;
    },
    p52VersionDiffItems() {
      const base = this.p52VersionNodes.find((item) => item.version_key === this.selectedP52BaselineVersionKey);
      const target = this.p52VersionNodes.find((item) => item.version_key === this.selectedP52TargetVersionKey);
      if (!base || !target || base.version_key === target.version_key) {
        return [];
      }
      const rows = [];
      rows.push({ title: "版本关系", detail: `${base.label} → ${target.label}` });
      const baseConclusion = base.conclusion ? this.reviewConclusionLabel(base.conclusion) : "-";
      const targetConclusion = target.conclusion ? this.reviewConclusionLabel(target.conclusion) : "-";
      if (baseConclusion !== targetConclusion) {
        rows.push({ title: "结论变化", detail: `${baseConclusion} → ${targetConclusion}` });
      }
      const baseUnsupported = Number(base.unsupported_count);
      const targetUnsupported = Number(target.unsupported_count);
      if (Number.isFinite(baseUnsupported) || Number.isFinite(targetUnsupported)) {
        rows.push({
          title: "未通过项变化",
          detail: `${Number.isFinite(baseUnsupported) ? baseUnsupported : "-"} → ${Number.isFinite(targetUnsupported) ? targetUnsupported : "-"}`,
        });
      }
      const baseInsufficient = Number(base.insufficient_count);
      const targetInsufficient = Number(target.insufficient_count);
      if (Number.isFinite(baseInsufficient) || Number.isFinite(targetInsufficient)) {
        rows.push({
          title: "待补证项变化",
          detail: `${Number.isFinite(baseInsufficient) ? baseInsufficient : "-"} → ${Number.isFinite(targetInsufficient) ? targetInsufficient : "-"}`,
        });
      }
      const baseTaskCount = Number(base.task_count);
      const targetTaskCount = Number(target.task_count);
      if (Number.isFinite(baseTaskCount) || Number.isFinite(targetTaskCount)) {
        rows.push({
          title: "判断项数量变化",
          detail: `${Number.isFinite(baseTaskCount) ? baseTaskCount : "-"} → ${Number.isFinite(targetTaskCount) ? targetTaskCount : "-"}`,
        });
      }
      if (target.type === "candidate" && target.patch) {
        rows.push({
          title: "来源补丁",
          detail: `${this.p52PatchVersionLabel(target.patch)}；规则：${this.p52PatchRuleIdsText(target.patch)}；方法域：${this.p52PatchMethodProfilesText(target.patch)}`,
        });
        const replayVerdict = target.replay_result ? String(target.replay_result.overall_verdict || "").trim() : "";
        if (replayVerdict) {
          rows.push({
            title: "该补丁独立回放结论",
            detail: this.verificationVerdictLabel(replayVerdict),
          });
        }
      }
      const baseSummary = String(base.summary || "").trim();
      const targetSummary = String(target.summary || "").trim();
      if (baseSummary || targetSummary) {
        rows.push({ title: "摘要变化", detail: `${baseSummary || "-"} => ${targetSummary || "-"}` });
      }
      return rows;
    },
    p52VerificationPlan() {
      if (this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult.verification_result === "object" && this.feedbackOptimizeResult.verification_result) {
        return this.feedbackOptimizeResult.verification_result;
      }
      if (this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult.verification_plan === "object" && this.feedbackOptimizeResult.verification_plan) {
        return this.feedbackOptimizeResult.verification_plan;
      }
      const historyEntry = this.latestP52VerificationHistoryEntry;
      if (historyEntry) {
        if (historyEntry.verification_result && typeof historyEntry.verification_result === "object" && Object.keys(historyEntry.verification_result).length) {
          return historyEntry.verification_result;
        }
        if (historyEntry.verification_plan && typeof historyEntry.verification_plan === "object" && Object.keys(historyEntry.verification_plan).length) {
          return historyEntry.verification_plan;
        }
      }
      return {};
    },
    latestP52VerificationHistoryEntry() {
      const entries = Array.isArray(this.signalEntries) ? this.signalEntries : [];
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        const item = entries[index];
        if (!item || typeof item !== "object") {
          continue;
        }
        if (String(item.analysis_kind || "").trim() !== "p52_feedback_verify") {
          continue;
        }
        const hasVerificationResult = item.verification_result && typeof item.verification_result === "object" && Object.keys(item.verification_result).length;
        const hasVerificationPlan = item.verification_plan && typeof item.verification_plan === "object" && Object.keys(item.verification_plan).length;
        if (hasVerificationResult || hasVerificationPlan) {
          return item;
        }
      }
      return null;
    },
    p52VerificationFromHistory() {
      const currentResult = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult.verification_result === "object"
        ? this.feedbackOptimizeResult.verification_result
        : {};
      if (currentResult && Object.keys(currentResult).length) {
        return false;
      }
      const currentPlan = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult.verification_plan === "object"
        ? this.feedbackOptimizeResult.verification_plan
        : {};
      if (currentPlan && Object.keys(currentPlan).length) {
        return false;
      }
      return !!this.latestP52VerificationHistoryEntry;
    },
    latestP52MetaReflectionHistoryEntry() {
      const entries = Array.isArray(this.signalEntries) ? this.signalEntries : [];
      for (let index = entries.length - 1; index >= 0; index -= 1) {
        const item = entries[index];
        if (!item || typeof item !== "object") {
          continue;
        }
        if (String(item.analysis_kind || "").trim() !== "p52_meta_reflection") {
          continue;
        }
        if (item.meta_reflection && typeof item.meta_reflection === "object" && Object.keys(item.meta_reflection).length) {
          return item;
        }
      }
      return null;
    },
    p52MetaReflectionFromHistory() {
      const current = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult.meta_reflection === "object"
        ? this.feedbackOptimizeResult.meta_reflection
        : {};
      if (current && Object.keys(current).length) {
        return false;
      }
      return !!this.latestP52MetaReflectionHistoryEntry;
    },
    p52MetaReflection() {
      const current = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult.meta_reflection === "object"
        ? this.feedbackOptimizeResult.meta_reflection
        : {};
      if (current && Object.keys(current).length) {
        return current;
      }
      const historyEntry = this.latestP52MetaReflectionHistoryEntry;
      return historyEntry && typeof historyEntry.meta_reflection === "object" ? historyEntry.meta_reflection : {};
    },
    p52MetaReflectionAvailable() {
      return !!(this.p52MetaReflection && Object.keys(this.p52MetaReflection).length);
    },
    p52MetaErrorFamilyText() {
      return String(this.p52MetaReflection.error_family || "").trim() || "-";
    },
    p52MetaMethodProfilesText() {
      const values = Array.isArray(this.p52MetaReflection.method_profiles) ? this.p52MetaReflection.method_profiles : [];
      return values.length ? values.join(" / ") : "-";
    },
    p52MetaRuleIdsText() {
      const values = Array.isArray(this.p52MetaReflection.candidate_rule_ids) ? this.p52MetaReflection.candidate_rule_ids : [];
      return values.length ? values.join(" / ") : "-";
    },
    p52MetaApprovedMaterialCountText() {
      const count = Number(this.p52MetaReflection.approved_material_count);
      return Number.isFinite(count) ? String(count) : "-";
    },
    p52MetaReflectionSummaryText() {
      return String(this.p52MetaReflection.reflection_summary || "").trim() || "-";
    },
    p52MetaRecommendedFocus() {
      const values = Array.isArray(this.p52MetaReflection.recommended_focus) ? this.p52MetaReflection.recommended_focus : [];
      return values.map((item) => String(item || "").trim()).filter(Boolean);
    },
    p52MetaDistilledExperiences() {
      return Array.isArray(this.p52MetaReflection.distilled_experiences)
        ? this.p52MetaReflection.distilled_experiences.filter((item) => item && typeof item === "object")
        : [];
    },
    p52VerificationPlanAvailable() {
      return !!(this.p52VerificationPlan && Object.keys(this.p52VerificationPlan).length);
    },
    p52VerificationVerdictLabel() {
      return this.verificationVerdictLabel(this.p52VerificationVerdictCode);
    },
    p52VerificationVerdictCode() {
      return String(this.p52VerificationPlan.overall_verdict || "").trim().toLowerCase();
    },
    p52VerificationAutoReplayText() {
      if (!this.p52VerificationPlanAvailable) {
        return "-";
      }
      return this.p52VerificationPlan.supports_auto_replay ? "支持" : "暂不支持";
    },
    p52VerificationGoalsText() {
      const values = Array.isArray(this.p52VerificationPlan.verification_goals) ? this.p52VerificationPlan.verification_goals : [];
      return values.length ? values.join("；") : "-";
    },
    p52SameProfileCasesText() {
      const cases = this.p52VerificationSameProfileCards;
      return cases.length ? cases.map((item) => item.section_name || item.section_id).join("；") : "-";
    },
    p52AdversarialCasesText() {
      const cases = this.p52VerificationAdversarialCards;
      return cases.length ? cases.map((item) => item.section_name || item.section_id).join("；") : "-";
    },
    p52VerificationNotesText() {
      const values = Array.isArray(this.p52VerificationPlan.notes) ? this.p52VerificationPlan.notes : [];
      return values.length ? values.join("；") : "-";
    },
    p52VerificationCurrentCaseCard() {
      return this.p52VerificationPlan && this.p52VerificationPlan.rerun_cases && typeof this.p52VerificationPlan.rerun_cases.current_case === "object"
        ? this.p52VerificationPlan.rerun_cases.current_case
        : null;
    },
    p52VerificationSameProfileCards() {
      if (this.p52VerificationPlan && this.p52VerificationPlan.rerun_cases && Array.isArray(this.p52VerificationPlan.rerun_cases.same_profile_cases)) {
        return this.p52VerificationPlan.rerun_cases.same_profile_cases;
      }
      return this.p52VerificationPlan.planned_cases && Array.isArray(this.p52VerificationPlan.planned_cases.same_profile_cases)
        ? this.p52VerificationPlan.planned_cases.same_profile_cases
        : [];
    },
    p52VerificationAdversarialCards() {
      if (this.p52VerificationPlan && this.p52VerificationPlan.rerun_cases && Array.isArray(this.p52VerificationPlan.rerun_cases.adversarial_cases)) {
        return this.p52VerificationPlan.rerun_cases.adversarial_cases;
      }
      return this.p52VerificationPlan.planned_cases && Array.isArray(this.p52VerificationPlan.planned_cases.adversarial_cases)
        ? this.p52VerificationPlan.planned_cases.adversarial_cases
        : [];
    },
    p52CurrentCaseChangedText() {
      if (!this.p52VerificationCurrentCaseCard) {
        return "-";
      }
      return this.p52VerificationCurrentCaseCard.changed ? "已改善/有变化" : "未见明显变化";
    },
    p52VerificationOverlaySummaryText() {
      const overlay = this.p52VerificationPlan && typeof this.p52VerificationPlan.overlay_summary === "object"
        ? this.p52VerificationPlan.overlay_summary
        : {};
      const patchCount = Number(overlay.patch_count);
      const patchIds = Array.isArray(overlay.source_patch_ids) ? overlay.source_patch_ids : [];
      const promptTargets = Array.isArray(overlay.prompt_suffix_targets) ? overlay.prompt_suffix_targets : [];
      const parts = [];
      if (Number.isFinite(patchCount)) {
        parts.push(`patch ${patchCount} 个`);
      }
      if (patchIds.length) {
        parts.push(`patch_id：${patchIds.join(" / ")}`);
      }
      if (promptTargets.length) {
        parts.push(`影响环节：${promptTargets.join(" / ")}`);
      }
      return parts.length ? parts.join("；") : "-";
    },
    p52RuleMergeSummaryText() {
      const mergeResult = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult.rule_merge_result === "object"
        ? this.feedbackOptimizeResult.rule_merge_result
        : {};
      if (!mergeResult || !Object.keys(mergeResult).length) {
        return "-";
      }
      if (mergeResult.merged) {
        const ids = Array.isArray(mergeResult.merged_patch_ids) ? mergeResult.merged_patch_ids : [];
        return ids.length ? `已合并 ${ids.length} 个` : "已合并";
      }
      return String(mergeResult.reason || "").trim() || "未合并";
    },
    hasFeedbackOptimizeSummary() {
      return (
        this.feedbackIssueFamilyLabel !== "-"
        || this.feedbackTargetLayerText !== "-"
        || this.primaryErrorTypeLabel !== "-"
        || this.patchTargetAgentText !== "-"
        || this.patchTypeText !== "-"
        || this.patchScopeText !== "-"
        || this.knowledgeCategoryText !== "-"
        || this.patchCount > 0
        || this.p52MetaReflectionAvailable
        || this.optimizeEvaluationVerdictText !== "未判断"
        || this.optimizeEvaluationScoreText !== "-"
        || this.metaReflectionSummary !== "-"
      );
    },
    currentOptimizeEvaluation() {
      return this.feedbackOptimizeResult
        && this.feedbackOptimizeResult.optimize_evaluation
        && typeof this.feedbackOptimizeResult.optimize_evaluation === "object"
        ? this.feedbackOptimizeResult.optimize_evaluation
        : {};
    },
    ruleFindingItems() {
      const rows = Array.isArray(this.review && this.review.rule_findings) ? this.review.rule_findings : [];
      return rows
        .filter((item) => item && typeof item === "object")
        .map((item) => ({
          location: String(item.location || "").trim(),
          rule_code: String(item.rule_code || "").trim(),
          rule_text: String(item.rule_text || "").trim(),
          requirement_point: String(item.requirement_point || item.rule_clause || "").trim(),
          issue_type: String(item.issue_type || "").trim(),
          issue: String(item.issue || "").trim(),
          evidence: String(item.evidence || "").trim(),
          status: String(item.status || item.issue_type || "").trim().toLowerCase(),
          task_question: String(item.task_question || "").trim(),
          reason: String(item.reason || "").trim(),
          advice: String(item.advice || item.suggested_fix || item.requested_action || "").trim(),
          suggested_fix: String(item.suggested_fix || item.requested_action || "").trim(),
        }))
        .filter((item) => item.issue || item.requirement_point || item.rule_text || item.rule_code);
    },
    optimizeEvaluationVerdictText() {
      return this.feedbackSignalLabel(this.currentOptimizeEvaluation.overall_verdict || "unknown");
    },
    optimizeEvaluationScoreText() {
      return this.numberText(this.currentOptimizeEvaluation.score);
    },
    optimizeEvaluationCommentText() {
      return String((this.currentOptimizeEvaluation && this.currentOptimizeEvaluation.comment) || "").trim() || "-";
    },
    sectionRuleItems() {
      return Array.isArray(this.sectionRulesDetail && this.sectionRulesDetail.items) ? this.sectionRulesDetail.items : [];
    },
    sectionRulesSummary() {
      return this.sectionRulesDetail && typeof this.sectionRulesDetail.summary === "object" ? this.sectionRulesDetail.summary : {};
    },
    exampleItems() {
      return Array.isArray(this.sectionExampleDetail && this.sectionExampleDetail.items) ? this.sectionExampleDetail.items : [];
    },
    feedbackHistorySummary() {
      return this.feedbackHistory && typeof this.feedbackHistory.summary === "object" ? this.feedbackHistory.summary : {};
    },
    signalEntries() {
      return Array.isArray(this.feedbackHistory && this.feedbackHistory.signal_entries) ? this.feedbackHistory.signal_entries : [];
    },
  },
  methods: {
    formatStructuredRiskRuleLabel(item) {
      const metadata = item && typeof item.metadata === 'object' ? item.metadata : {};
      const trace = item && typeof item.trace === 'object' ? item.trace : {};
      const ruleCode = String((item && (item.rule_code || item.rule_id || metadata.rule_code || trace.rule_code)) || '').trim();
      const ruleText = String((item && (item.rule_text || metadata.rule_text || trace.rule_text)) || '').trim();
      if (ruleCode && ruleText) {
        return ruleText.startsWith(ruleCode) ? ruleText : `${ruleCode} ${ruleText}`;
      }
      return ruleCode || ruleText || '未标注具体规则';
    },
    formatStructuredRiskApplicability(item) {
      const metadata = item && typeof item.metadata === 'object' ? item.metadata : {};
      const trace = item && typeof item.trace === 'object' ? item.trace : {};
      return String((item && (item.requirement_point || item.basis || item.rule_requirement || metadata.requirement_point || trace.requirement_point)) || '').trim() || '未提供规则适用条件';
    },
    formatStructuredRiskNonCompliance(item) {
      const issue = String((item && (item.issue || item.problem || item.task_question)) || '').trim();
      const reason = String((item && (item.reason || item.judgment_reason || item.comparison)) || '').trim();
      const metadata = item && typeof item.metadata === 'object' ? item.metadata : {};
      const trace = item && typeof item.trace === 'object' ? item.trace : {};
      const location = String((item && (item.location || metadata.location || trace.location)) || '').trim();
      const evidence = String((item && item.evidence) || '').trim();
      const parts = [];
      if (issue) {
        parts.push(`原文不符合点：${issue}`);
      }
      if (reason && reason !== issue) {
        parts.push(`不符合原因：${reason}`);
      }
      if (location) {
        parts.push(`定位：${location}`);
      }
      if (evidence && evidence !== issue && evidence !== reason) {
        parts.push(`原文/比对依据：${evidence}`);
      }
      return parts.join('；');
    },
    formatStructuredRiskEvidence(item) {
      const rawEvidence = String((item && item.evidence) || '').trim();
      if (rawEvidence && (rawEvidence.includes('规则依据') || rawEvidence.includes('规则适用条件'))) {
        return rawEvidence;
      }
      const ruleLabel = this.formatStructuredRiskRuleLabel(item);
      const applicability = this.formatStructuredRiskApplicability(item);
      const nonCompliance = this.formatStructuredRiskNonCompliance(item);
      const parts = [];
      if (ruleLabel) {
        parts.push(`规则依据：${ruleLabel}`);
      }
      if (applicability) {
        parts.push(`规则适用条件：${applicability}`);
      }
      if (nonCompliance) {
        parts.push(nonCompliance);
      }
      return parts.join('；');
    },
    formatStructuredRiskRecommendation(item) {
      return String((item && (item.recommendation || item.advice || item.suggested_fix || item.requested_action)) || '').trim() || '请结合对应规则补充直接事实、判定依据和整改动作。';
    },
    formatStructuredRiskTitle(item, index) {
      const ruleLabel = this.formatStructuredRiskRuleLabel(item);
      const issue = String((item && (item.problem || item.issue || item.task_question || item.requirement_point)) || '').trim() || `风险项 ${index + 1}`;
      if (!ruleLabel || ruleLabel === '未标注具体规则') {
        return issue;
      }
      const normalizedRuleLabel = ruleLabel.toLowerCase();
      const normalizedIssue = issue.toLowerCase();
      if (normalizedIssue && normalizedRuleLabel.includes(normalizedIssue)) {
        return ruleLabel;
      }
      return `${ruleLabel}：${issue}`;
    },
    resetTransientViewState() {
      this.auditDialogVisible = false;
      this.advancedSectionVisibility = {
        metrics: false,
        retrieval: false,
        chapterProfile: false,
        extractedFacts: false,
        sectionRules: false,
        promptRules: false,
        optimize: false,
        experience: false,
        examples: false,
        history: false,
      };
      this.expandedEvidenceSources = {};
      this.expandedJudgmentItems = {};
      this.expandedJudgmentFeedback = {};
      this.expandedP52Patches = {};
      this.collapsedEvidenceSections = {
        approved: false,
        rejected: false,
      };
      this.collapsedPanels = {
        feedbackInput: true,
        optimizeReplay: true,
        versionDiff: true,
      };
      this.selectedP52PatchGroupKey = "";
      this.selectedP52PatchVersionKey = "";
      this.selectedP52BaselineVersionKey = "";
      this.selectedP52TargetVersionKey = "";
      this.expandedProcessChainCards = {};
      this.expandedWorkflowReplayCards = {};
      this.expandedVersionDiffCards = {};
    },
    riskLevelTagType(level) {
      const normalized = String(level || '').trim().toLowerCase();
      if (normalized === 'high') {
        return 'danger';
      }
      if (normalized === 'medium') {
        return 'warning';
      }
      if (normalized === 'low') {
        return 'success';
      }
      return 'info';
    },
    issueFamilyLabelFromError(errorType) {
      const normalized = String(errorType || "").trim();
      if (["method_profile_error"].includes(normalized)) {
        return "方法识别问题";
      }
      if (["entity_extraction_error", "quality_standard_mapping_error"].includes(normalized)) {
        return "结构化抽取问题";
      }
      if (["rule_false_positive", "rule_false_negative"].includes(normalized)) {
        return "规则问题";
      }
      if (["retrieval_scope_error", "retrieval_ranking_error"].includes(normalized)) {
        return "检索问题";
      }
      if (["reviewer_reasoning_error", "result_merge_error"].includes(normalized)) {
        return "审评推理问题";
      }
      if (["frontend_projection_error"].includes(normalized)) {
        return "结果投影问题";
      }
      if (["query_miss", "retrieval_scope_error", "retrieval_ranking_error", "historical_experience_missing"].includes(normalized)) {
        return "检索问题";
      }
      if (["rule_mapping_error", "missing_regulatory_basis"].includes(normalized)) {
        return "规则问题";
      }
      if (["knowledge_understanding_error", "section_fact_extraction_error"].includes(normalized)) {
        return "事实问题";
      }
      if (["task_question_error", "focus_point_miss"].includes(normalized)) {
        return "任务理解问题";
      }
      if (["reasoning_chain_error", "evidence_interpretation_error", "over_inference", "under_identification", "wrong_severity"].includes(normalized)) {
        return "推理链问题";
      }
      if (["wording_not_actionable", "unhelpful_question_to_applicant"].includes(normalized)) {
        return "表达问题";
      }
      return normalized || "-";
    },
    errorTypeLabel(errorType) {
      const mapping = {
        method_profile_error: "方法类型识别错误",
        entity_extraction_error: "实体抽取错误",
        quality_standard_mapping_error: "质量标准映射错误",
        rule_false_positive: "规则误报",
        rule_false_negative: "规则漏报",
        reviewer_reasoning_error: "reviewer 判断错误",
        result_merge_error: "结果合并错误",
        frontend_projection_error: "前端展示错误",
        query_miss: "检索查询缺失",
        retrieval_scope_error: "检索范围错误",
        retrieval_ranking_error: "检索排序错误",
        historical_experience_missing: "历史经验缺失",
        knowledge_understanding_error: "知识理解错误",
        rule_mapping_error: "规则映射错误",
        section_fact_extraction_error: "章节事实抽取错误",
        focus_point_miss: "关注点遗漏",
        task_question_error: "判断项构造错误",
        reasoning_chain_error: "推理链错误",
        evidence_interpretation_error: "证据解释错误",
        over_inference: "过度推断",
        under_identification: "问题识别不足",
        wrong_severity: "风险等级判断错误",
        missing_regulatory_basis: "法规依据缺失",
        wording_not_actionable: "表述不可执行",
        unhelpful_question_to_applicant: "问题表述无助于整改",
      };
      const normalized = String(errorType || "").trim();
      return mapping[normalized] || normalized || "-";
    },
    targetAgentLabel(agent) {
      const mapping = {
        p52_method_profile: "P52 方法识别",
        p52_entity_extractor: "P52 实体抽取",
        p52_quality_standard_mapper: "P52 质量标准映射",
        p52_rule_engine: "P52 规则引擎",
        p52_retriever: "P52 检索补证",
        p52_reviewer: "P52 reviewer",
        p52_result_merger: "P52 结果合并",
        p52_frontend_projection: "P52 前端投影",
        planner: "planner（任务理解与检索规划）",
        retrieval_evaluator: "retrieval_evaluator（证据筛选与准入）",
        task_question: "task_question（判断项构造）",
        reviewer: "reviewer（规则比对与结论生成）",
        feedback_analyzer: "feedback_analyzer（反馈归因）",
        feedback_optimizer: "feedback_optimizer（补丁生成）",
      };
      const normalized = String(agent || "").trim();
      return mapping[normalized] || normalized || "-";
    },
    p52PatchStatusType(status) {
      const normalized = String(status || "").trim().toLowerCase();
      if (normalized === "approved" || normalized === "applied") {
        return "success";
      }
      if (normalized === "rejected") {
        return "danger";
      }
      if (normalized === "candidate") {
        return "warning";
      }
      return "info";
    },
    p52PatchStatusLabel(status) {
      const normalized = String(status || "").trim().toLowerCase();
      if (normalized === "approved") {
        return "已批准";
      }
      if (normalized === "applied") {
        return "已生效";
      }
      if (normalized === "rejected") {
        return "已拒绝";
      }
      if (normalized === "candidate") {
        return "候选";
      }
      return normalized || "-";
    },
    isP52PatchExpanded(patchId) {
      const key = String(patchId || "").trim();
      return !!this.expandedP52Patches[key];
    },
    toggleP52PatchExpanded(patchId) {
      const key = String(patchId || "").trim();
      if (!key) {
        return;
      }
      this.$set(this.expandedP52Patches, key, !this.expandedP52Patches[key]);
    },
    p52PatchPayload(item) {
      const outer = item && typeof item.payload === "object" ? item.payload : {};
      if (outer && typeof outer.payload === "object") {
        return outer.payload;
      }
      return outer;
    },
    p52PatchTargetFileText(item) {
      const payload = this.p52PatchPayload(item);
      return String(payload.target_file || "").trim() || "-";
    },
    p52PatchTargetKeyText(item) {
      const payload = this.p52PatchPayload(item);
      return String(payload.target_key || "").trim() || "-";
    },
    p52PatchMethodProfilesText(item) {
      const payload = this.p52PatchPayload(item);
      const values = Array.isArray(payload.method_profiles)
        ? payload.method_profiles.map((entry) => String(entry || "").trim()).filter(Boolean)
        : [];
      return values.length ? values.join(" / ") : "待确认";
    },
    p52PatchRuleIdsText(item) {
      const payload = this.p52PatchPayload(item);
      const values = Array.isArray(payload.candidate_rule_ids)
        ? payload.candidate_rule_ids.map((entry) => String(entry || "").trim()).filter(Boolean)
        : [];
      return values.length ? values.join(" / ") : "无";
    },
    p52PatchFeedbackText(item) {
      const payload = this.p52PatchPayload(item);
      return String(payload.source_feedback_text || "").trim() || "-";
    },
    p52PatchTraceSummaryText(item) {
      const payload = this.p52PatchPayload(item);
      return String(payload.trace_summary || "").trim() || "-";
    },
    p52PatchStatusUpdateText(item) {
      const payload = this.p52PatchPayload(item);
      const update = payload && typeof payload.status_update === "object" ? payload.status_update : {};
      const status = String(update.status || "").trim();
      const operator = String(update.operator || "").trim();
      const comment = String(update.comment || "").trim();
      const updateTime = String(update.update_time || "").trim();
      const segments = [];
      if (status) {
        segments.push(`状态：${this.p52PatchStatusLabel(status)}`);
      }
      if (operator) {
        segments.push(`操作人：${operator}`);
      }
      if (updateTime) {
        segments.push(`时间：${updateTime}`);
      }
      if (comment) {
        segments.push(`备注：${comment}`);
      }
      return segments.length ? segments.join("\n") : "-";
    },
    mustAnswerCoverageLabel(status) {
      const normalized = String(status || "").trim().toLowerCase();
      if (normalized === "covered") {
        return "已覆盖";
      }
      if (normalized === "partial") {
        return "部分覆盖";
      }
      return "未覆盖";
    },
    mustAnswerCoverageTagType(status) {
      const normalized = String(status || "").trim().toLowerCase();
      if (normalized === "covered") {
        return "success";
      }
      if (normalized === "partial") {
        return "warning";
      }
      return "info";
    },
    patchTargetAgentsFromResult(patchResult) {
      const patches = Array.isArray(patchResult && patchResult.patches) ? patchResult.patches : [];
      return [...new Set(patches.map((item) => String((item && item.target_agent) || "").trim()).filter(Boolean))];
    },
    metricPercent(value) {
      const num = Number(value);
      return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : "-";
    },
    metricNumber(value) {
      const num = Number(value);
      return Number.isFinite(num) ? `${num}` : "-";
    },
    isAdvancedSectionVisible(section) {
      return !!this.advancedSectionVisibility[String(section || "")];
    },
    toggleAdvancedSection(section) {
      const key = String(section || "");
      this.$set(this.advancedSectionVisibility, key, !this.advancedSectionVisibility[key]);
    },
    numberText(value) {
      const num = Number(value);
      return Number.isFinite(num) ? num.toFixed(4) : "-";
    },
    evidenceKey(item) {
      const evidenceId = String((item && item.evidence_id) || "").trim();
      if (evidenceId) {
        return evidenceId;
      }
      const docId = String((item && item.doc_id) || "").trim();
      const chunkId = String((item && item.chunk_id) || "").trim();
      const title = String((item && item.title) || "").trim();
      return [docId, chunkId, title].filter(Boolean).join(":") || Math.random().toString(16).slice(2);
    },
    resolveEvidenceSupportText(value) {
      const mapping = this.approvedEvidenceDisplayMap || {};
      const seen = new Set();
      const labels = String(value || "")
        .split(/[；;，,\n\r]+/)
        .map((item) => String(item || "").trim())
        .filter(Boolean)
        .map((token) => {
          if (mapping[token]) {
            return mapping[token];
          }
          if (token.includes(":")) {
            const suffix = token.split(":").slice(1).join(":").trim();
            if (mapping[suffix]) {
              return mapping[suffix];
            }
          }
          if (/^(?:pharm|rag|guidance|rule|ich|cp|doc|ref|meta|experience):/i.test(token) || /^[0-9a-f]{8,}:[A-Za-z0-9._-]+$/i.test(token)) {
            return "";
          }
          return this.humanizeReferenceName(token);
        })
        .filter((label) => {
          if (!label || seen.has(label)) {
            return false;
          }
          seen.add(label);
          return true;
        });
      return labels.join("；");
    },
    normalizeReferenceList(value) {
      const mapping = this.approvedEvidenceDisplayMap || {};
      const seen = new Set();
      return String(value || "")
        .split(/[；;，,\n\r]+/)
        .map((item) => String(item || "").trim())
        .filter(Boolean)
        .map((token) => {
          if (mapping[token]) {
            return mapping[token];
          }
          if (token.includes(":")) {
            const suffix = token.split(":").slice(1).join(":").trim();
            if (mapping[suffix]) {
              return mapping[suffix];
            }
          }
          if (/^(?:pharm|rag|guidance|rule|ich|cp|doc|ref|meta|experience):/i.test(token) || /^[0-9a-f]{8,}:[A-Za-z0-9._-]+$/i.test(token)) {
            return "";
          }
          return token;
        })
        .filter((token) => {
          if (!token || seen.has(token)) {
            return false;
          }
          seen.add(token);
          return true;
        })
        .join("；");
    },
    normalizeEvidenceMentionsInText(value) {
      let text = String(value || "").trim();
      if (!text) {
        return "";
      }
      text = text.replace(/“([^”]+)”/g, (match, inner) => {
        const normalized = this.normalizeReferenceList(inner);
        return normalized ? `“${normalized}”` : "";
      });
      text = text.replace(/\b(?:pharm|rag|guidance|rule|ich|cp|doc|ref|meta|experience):[A-Za-z0-9._-]+\b/gi, "");
      text = text.replace(/\b[0-9a-f]{8,}:[A-Za-z0-9._-]+\b/gi, "");
      text = text.replace(/“\s*”/g, "");
      text = text.replace(/[；;,，]\s*[；;,，]+/g, "；");
      text = text.replace(/\s{2,}/g, " ").trim();
      return text;
    },
    evidenceName(item) {
      const raw = String((item && (item.display_name || item.file_name_zh || item.file_name || item.doc_title || item.title || item.doc_id)) || "-").trim() || "-";
      return this.humanizeReferenceName(raw, item);
    },
    evidenceTitle(item) {
      const raw = String((item && (item.title || item.doc_title || item.file_name || item.doc_id)) || "-").trim() || "-";
      return this.humanizeReferenceName(raw, item);
    },
    evidenceSnippet(item) {
      return String((item && (item.context_snippet || item.content_preview || item.content)) || "").trim() || "当前没有证据片段。";
    },
    fullEvidenceText(item) {
      return String((item && item.content) || this.evidenceSnippet(item) || "").trim();
    },
    evidenceReasonText(item, mode) {
      if (mode === "rejected") {
        return String(
          (item && (item.evaluation_reject_reason || item.reject_reason || item.evaluation_keep_reason || item.keep_reason)) || ""
        ).trim();
      }
      return String(
        (item && (item.evaluation_keep_reason || item.keep_reason || item.evaluation_reject_reason || item.reject_reason)) || ""
      ).trim();
    },
    evidenceRejectTypeText(item) {
      return String((item && (item.evaluation_reject_type || item.reject_type)) || "").trim() || "-";
    },
    buildVisibleEvidenceGroups(groups, mode) {
      return (Array.isArray(groups) ? groups : []).map((group) => {
        const seen = new Set();
        const items = (Array.isArray(group.items) ? group.items : []).filter((item) => {
          const score = Number(item && item.score);
          if (!Number.isFinite(score) || score < 0.5) {
            return false;
          }
          const evidenceId = this.evidenceKey(item);
          if (seen.has(evidenceId)) {
            return false;
          }
          seen.add(evidenceId);
          return true;
        });
        const source = String(group.source || "-").trim() || "-";
        const expandKey = `${mode}:${source}`;
        const expanded = this.isEvidenceGroupExpanded(expandKey);
        return { source, total: items.length, expandKey, visibleItems: expanded ? items : items.slice(0, this.defaultEvidenceLimit) };
      }).filter((group) => group.total > 0);
    },
    isEvidenceGroupExpanded(source) {
      return !!this.expandedEvidenceSources[String(source || "")];
    },
    isEvidenceSectionCollapsed(section) {
      return !!this.collapsedEvidenceSections[String(section || "")];
    },
    toggleEvidenceSection(section) {
      const key = String(section || "");
      this.$set(this.collapsedEvidenceSections, key, !this.collapsedEvidenceSections[key]);
    },
    toggleEvidenceGroup(source) {
      const key = String(source || "");
      this.$set(this.expandedEvidenceSources, key, !this.expandedEvidenceSources[key]);
    },
    exampleTypeLabel(value) {
      const mapping = { reference: "参考示例", few_shot: "Few-shot 示例", evaluation_case: "评估样本" };
      return mapping[String(value || "").trim()] || value || "-";
    },
    feedbackSignalLabel(value) {
      const mapping = { correct: "正确", partial: "部分正确", incorrect: "错误", unknown: "未判断" };
      return mapping[String(value || "").trim()] || value || "-";
    },
    summarizeEvidenceFeedback(items) {
      const rows = Array.isArray(items) ? items : [];
      if (!rows.length) {
        return "-";
      }
      return rows.map((item) => `${item.evidence_id || "-"}:${this.feedbackSignalLabel(item.verdict)}`).slice(0, 3).join(" / ");
    },
    verificationVerdictLabel(verdictCode) {
      const verdict = String(verdictCode || "").trim().toLowerCase();
      if (verdict === "improved") {
        return "已改善";
      }
      if (verdict === "regressed") {
        return "有回归";
      }
      if (verdict === "needs_review") {
        return "待复核";
      }
      if (verdict === "pending_replay") {
        return "待执行回放";
      }
      if (verdict === "failed") {
        return "执行失败";
      }
      if (verdict === "passed") {
        return "通过";
      }
      if (verdict === "rejected") {
        return "拒绝";
      }
      return "-";
    },
    verificationVerdictClass(verdictCode) {
      const verdict = String(verdictCode || "").trim().toLowerCase();
      if (verdict === "improved" || verdict === "passed") {
        return "success";
      }
      if (verdict === "regressed" || verdict === "failed" || verdict === "rejected") {
        return "warning";
      }
      return "";
    },
    verificationChangedTagType(changed) {
      return changed ? "success" : "info";
    },
    verificationCaseSummary(value) {
      const row = value && typeof value === "object" ? value : {};
      const parts = [];
      const conclusion = String(row.conclusion || "").trim();
      if (conclusion) {
        parts.push(`结论：${this.verificationVerdictLabel(conclusion)}`);
      }
      const unsupportedCount = Number(row.unsupported_count);
      if (Number.isFinite(unsupportedCount)) {
        parts.push(`未通过 ${unsupportedCount} 项`);
      }
      const insufficientCount = Number(row.insufficient_count);
      if (Number.isFinite(insufficientCount)) {
        parts.push(`待补证 ${insufficientCount} 项`);
      }
      const taskCount = Number(row.task_count);
      if (Number.isFinite(taskCount)) {
        parts.push(`判断项 ${taskCount} 个`);
      }
      const summary = String(row.section_summary || "").trim();
      if (summary) {
        parts.push(`摘要：${summary}`);
      }
      return parts.length ? parts.join("；") : "-";
    },
    verificationCaseConclusionText(value) {
      const row = value && typeof value === "object" ? value : {};
      const conclusion = String(row.conclusion || "").trim();
      return conclusion ? this.verificationVerdictLabel(conclusion) : "-";
    },
    buildVerificationBaselineRow(value, caseItem) {
      const baseline = value && typeof value === "object" ? { ...value } : {};
      const caseRow = caseItem && typeof caseItem === "object" ? caseItem : {};
      if (!String(baseline.conclusion || "").trim()) {
        baseline.conclusion = String((this.review && (this.review.pre_review_conclusion || this.review.conclusion)) || "").trim();
      }
      if (!String(baseline.section_summary || "").trim()) {
        baseline.section_summary = String(caseRow.section_summary || "").trim()
          || String((this.review && (this.review.section_summary || this.review.summary)) || "").trim();
      }
      const reviewUnsupported = Number((this.review && this.review.unsupported_count) || NaN);
      const reviewInsufficient = Number((this.review && this.review.insufficient_count) || NaN);
      const reviewTaskCount = Number((this.review && this.review.task_count) || NaN);
      if (!Number.isFinite(Number(baseline.unsupported_count)) && Number.isFinite(reviewUnsupported)) {
        baseline.unsupported_count = reviewUnsupported;
      }
      if (!Number.isFinite(Number(baseline.insufficient_count)) && Number.isFinite(reviewInsufficient)) {
        baseline.insufficient_count = reviewInsufficient;
      }
      if (!Number.isFinite(Number(baseline.task_count)) && Number.isFinite(reviewTaskCount)) {
        baseline.task_count = reviewTaskCount;
      }
      return baseline;
    },
    verificationBaselineConclusionText(value, caseItem) {
      const merged = this.buildVerificationBaselineRow(value, caseItem);
      return this.verificationCaseConclusionText(merged);
    },
    verificationBaselineSummary(value, caseItem) {
      const merged = this.buildVerificationBaselineRow(value, caseItem);
      const baseSummary = this.verificationCaseSummary(merged);
      if (baseSummary && baseSummary !== "-") {
        return baseSummary;
      }
      const caseSummary = String((caseItem && caseItem.section_summary) || "").trim();
      if (caseSummary) {
        return `摘要：${caseSummary}`;
      }
      const reviewSummary = String((this.review && (this.review.section_summary || this.review.summary)) || "").trim();
      if (reviewSummary) {
        return `摘要：${reviewSummary}`;
      }
      return "摘要：暂无原始摘要";
    },
    p52PatchVersionNo(item) {
      const value = Number(item && item.version);
      return Number.isFinite(value) ? value : 0;
    },
    p52PatchVersionKey(item) {
      return String((item && item.patch_id) || "").trim() || `version-${this.p52PatchVersionNo(item)}`;
    },
    p52PatchVersionLabel(item) {
      const versionNo = this.p52PatchVersionNo(item);
      const status = this.p52PatchStatusLabel(item && item.status);
      return versionNo > 0 ? `v${versionNo}（${status}）` : `未标注版本（${status}）`;
    },
    p52PatchGroupKey(item, index) {
      const key = this.p52PatchTargetKeyText(item);
      if (key && key !== "-") {
        return key;
      }
      const patchType = String((item && item.patch_type) || "").trim();
      return patchType || `group-${index}`;
    },
    p52PatchGroupLabel(item, index) {
      const key = this.p52PatchGroupKey(item, index);
      const patchType = String((item && item.patch_type) || "").trim();
      return patchType ? `${key} / ${patchType}` : key;
    },
    syncP52PatchVersionSelection() {
      const groups = this.p52PatchGroups;
      if (!groups.length) {
        this.selectedP52PatchGroupKey = "";
        this.selectedP52PatchVersionKey = "";
        return;
      }
      const hasGroup = groups.some((item) => item.group_key === this.selectedP52PatchGroupKey);
      if (!hasGroup) {
        this.selectedP52PatchGroupKey = groups[0].group_key;
      }
      const versions = this.selectedP52PatchGroupVersions;
      const hasVersion = versions.some((item) => item.version_key === this.selectedP52PatchVersionKey);
      if (!hasVersion) {
        this.selectedP52PatchVersionKey = versions.length ? versions[0].version_key : "";
      }
    },
    syncP52VersionCompareSelection() {
      const nodes = this.p52VersionNodes;
      if (!nodes.length) {
        this.selectedP52BaselineVersionKey = "";
        this.selectedP52TargetVersionKey = "";
        return;
      }
      if (!nodes.some((item) => item.version_key === this.selectedP52BaselineVersionKey)) {
        this.selectedP52BaselineVersionKey = "Y0";
      }
      if (!nodes.some((item) => item.version_key === this.selectedP52TargetVersionKey)) {
        const preferred = nodes.find((item) => item.type === "candidate") || nodes[0];
        this.selectedP52TargetVersionKey = preferred.version_key;
      }
      if (this.selectedP52BaselineVersionKey === this.selectedP52TargetVersionKey) {
        const alternative = nodes.find((item) => item.version_key !== this.selectedP52BaselineVersionKey);
        if (alternative) {
          this.selectedP52TargetVersionKey = alternative.version_key;
        }
      }
    },
    isProcessChainCardExpanded(index) {
      const key = String(index);
      return this.expandedProcessChainCards[key] !== false;
    },
    toggleProcessChainCard(index) {
      const key = String(index);
      this.$set(this.expandedProcessChainCards, key, !this.isProcessChainCardExpanded(index));
    },
    isWorkflowReplayCardExpanded(index) {
      const key = String(index);
      return this.expandedWorkflowReplayCards[key] !== false;
    },
    toggleWorkflowReplayCard(index) {
      const key = String(index);
      this.$set(this.expandedWorkflowReplayCards, key, !this.isWorkflowReplayCardExpanded(index));
    },
    isVersionDiffCardExpanded(index) {
      const key = String(index);
      return this.expandedVersionDiffCards[key] !== false;
    },
    toggleVersionDiffCard(index) {
      const key = String(index);
      this.$set(this.expandedVersionDiffCards, key, !this.isVersionDiffCardExpanded(index));
    },
    p52WorkflowStageLabel(kind) {
      const mapping = {
        p52_feedback_optimize: "候选补丁生成",
        p52_feedback_verify: "回放验证",
        p52_meta_reflection: "元反思",
        p52_ablation: "消融对比",
      };
      return mapping[String(kind || "").trim()] || String(kind || "").trim() || "流程事件";
    },
    p52WorkflowSummaryText(item) {
      const patchResult = item && typeof item.patch_result === "object" ? item.patch_result : {};
      const patches = Array.isArray(patchResult.patches) ? patchResult.patches : [];
      const verify = item && typeof item.verification_result === "object" ? item.verification_result : {};
      const verdict = String(verify.overall_verdict || "").trim();
      const parts = [];
      if (patches.length) {
        parts.push(`生成 patch ${patches.length} 个`);
      }
      if (verdict) {
        parts.push(`验证结论：${this.verificationVerdictLabel(verdict)}`);
      }
      const reflection = item && typeof item.meta_reflection === "object" ? item.meta_reflection : {};
      const reflectionSummary = String(reflection.reflection_summary || "").trim();
      if (reflectionSummary) {
        parts.push(`反思：${reflectionSummary}`);
      }
      return parts.length ? parts.join("；") : "已记录流程事件";
    },
    adversarialRiskText(item) {
      const baseline = item && typeof item.baseline === "object" ? item.baseline : {};
      const patched = item && typeof item.patched === "object" ? item.patched : {};
      const baselineUnsupported = Number(baseline.unsupported_count);
      const patchedUnsupported = Number(patched.unsupported_count);
      if (Number.isFinite(baselineUnsupported) && Number.isFinite(patchedUnsupported) && patchedUnsupported > baselineUnsupported) {
        return "疑似误报增加";
      }
      return "关注副作用";
    },
    judgmentStatusText(status) {
      const mapping = {
        supported: "满足",
        unsupported: "不满足",
        insufficient_information: "暂无法判断",
        issue: "存在问题",
        question: "待补充",
        missing: "证据不足",
      };
      return mapping[String(status || "").trim().toLowerCase()] || status || "-";
    },
    judgmentStatusType(status) {
      const normalized = String(status || "").trim().toLowerCase();
      if (normalized === "supported") {
        return "success";
      }
      if (normalized === "unsupported" || normalized === "issue") {
        return "danger";
      }
      if (normalized === "insufficient_information" || normalized === "question" || normalized === "missing") {
        return "warning";
      }
      return "info";
    },
    judgmentItemKey(item, index) {
      const taskCode = String((item && item.task_code) || "").trim();
      const taskQuestion = String((item && item.task_question) || "").trim();
      return taskCode ? `judgment:${taskCode}` : `judgment:${index}:${taskQuestion.slice(0, 64)}`;
    },
    isJudgmentItemExpanded(item, index) {
      return !!this.expandedJudgmentItems[this.judgmentItemKey(item, index)];
    },
    toggleJudgmentItem(item, index) {
      const key = this.judgmentItemKey(item, index);
      this.$set(this.expandedJudgmentItems, key, !this.expandedJudgmentItems[key]);
    },
    isPanelCollapsed(key) {
      return !!this.collapsedPanels[String(key || "")];
    },
    togglePanel(key) {
      const normalized = String(key || "");
      this.$set(this.collapsedPanels, normalized, !this.collapsedPanels[normalized]);
    },
    judgmentFeedbackKey(item, index) {
      const explicitKey = String((item && item.feedback_key) || "").trim();
      if (explicitKey) {
        return explicitKey;
      }
      const feedbackKind = String((item && item.feedback_kind) || "task_verdict").trim() || "task_verdict";
      const taskCode = String((item && item.task_code) || "").trim();
      const taskQuestion = String((item && item.task_question) || "").trim();
      return taskCode ? `${feedbackKind}:${taskCode}` : `${feedbackKind}:${index}:${taskQuestion.slice(0, 64)}`;
    },
    isJudgmentFeedbackExpanded(key) {
      return !!this.expandedJudgmentFeedback[String(key || "")];
    },
    toggleJudgmentFeedback(key) {
      const normalized = String(key || "");
      this.$set(this.expandedJudgmentFeedback, normalized, !this.expandedJudgmentFeedback[normalized]);
    },
    buildEmptyFieldFeedback() {
      return {
        rule: "unknown",
        fact: "unknown",
        evidence: "unknown",
        reasoning: "unknown",
        conclusion: "unknown",
      };
    },
    judgmentFeedbackSeed(item, index) {
      return {
        issue_key: this.judgmentFeedbackKey(item, index),
        feedback_kind: String((item && item.feedback_kind) || "task_verdict").trim() || "task_verdict",
        verdict: "",
        feedback_text: "",
        error_reason: "",
        task_code: String((item && item.task_code) || "").trim(),
        task_question: String((item && item.task_question) || "").trim(),
        task_status: String((item && item.status) || "").trim().toLowerCase(),
        rule_code: String((item && (item.rule_code || item.task_code)) || "").trim(),
        rule_text: String((item && (item.rule_text || item.rule_requirement)) || "").trim(),
        requirement_point: String((item && (item.rule_requirement || item.basis)) || "").trim(),
        basis: String((item && (item.basis || item.rule_requirement)) || "").trim(),
        material_fact: String((item && item.material_fact) || "").trim(),
        evidence_support: String((item && item.evidence_support) || "").trim(),
        comparison: String((item && item.comparison) || "").trim(),
        judgment_reason: String((item && (item.reason || item.judgment_reason)) || "").trim(),
        issue: String((item && (item.problem || item.task_question)) || "").trim(),
        problem: String((item && item.problem) || "").trim(),
        advice: String((item && item.advice) || "").trim(),
        location: this.currentBrowseSectionLabel || "",
        violating_text: String((item && (item.material_fact || item.evidence_support || item.problem)) || "").trim(),
        evidence_files: [],
        field_feedback: this.buildEmptyFieldFeedback(),
      };
    },
    syncJudgmentFeedbackStates() {
      if (!this.feedbackForm.issue_feedback_map || typeof this.feedbackForm.issue_feedback_map !== "object") {
        this.$set(this.feedbackForm, "issue_feedback_map", {});
      }
      const verdictFeedbackItems = this.isP52Section
        ? this.structuredRiskItems.map((item, index) => this.riskFeedbackPseudoItem(item, index))
        : this.taskVerdictItems;
      const feedbackItems = [...verdictFeedbackItems, ...this.displayedReviewRuleItems];
      feedbackItems.forEach((item, index) => {
        const key = this.judgmentFeedbackKey(item, index);
        const seed = this.judgmentFeedbackSeed(item, index);
        const current = this.feedbackForm.issue_feedback_map[key];
        if (!current) {
          this.$set(this.feedbackForm.issue_feedback_map, key, seed);
          return;
        }
        const nextFieldFeedback = {
          ...this.buildEmptyFieldFeedback(),
          ...(current.field_feedback && typeof current.field_feedback === "object" ? current.field_feedback : {}),
        };
        Object.keys(seed).forEach((fieldKey) => {
          if (fieldKey === "field_feedback") {
            return;
          }
          if (current[fieldKey] !== seed[fieldKey] && (fieldKey !== "verdict" && fieldKey !== "feedback_text" && fieldKey !== "error_reason")) {
            this.$set(current, fieldKey, seed[fieldKey]);
          }
        });
        if (JSON.stringify(current.field_feedback || {}) !== JSON.stringify(nextFieldFeedback)) {
          this.$set(current, "field_feedback", nextFieldFeedback);
        }
        if (!Array.isArray(current.evidence_files)) {
          this.$set(current, "evidence_files", []);
        }
      });
    },
    judgmentFeedbackEntry(item, index) {
      if (!this.feedbackForm.issue_feedback_map || typeof this.feedbackForm.issue_feedback_map !== "object") {
        return this.judgmentFeedbackSeed(item, index);
      }
      const key = this.judgmentFeedbackKey(item, index);
      return this.feedbackForm.issue_feedback_map[key] || this.judgmentFeedbackSeed(item, index);
    },
    judgmentNeedsErrorReason(state) {
      if (!state || typeof state !== "object") {
        return false;
      }
      if (String(state.verdict || "").trim().toLowerCase() === "incorrect") {
        return true;
      }
      const fieldFeedback = state.field_feedback && typeof state.field_feedback === "object" ? state.field_feedback : {};
      return Object.values(fieldFeedback).some((value) => String(value || "").trim().toLowerCase() === "incorrect");
    },
    historyPrimaryErrorType(item) {
      const result = item && typeof item.analysis_result === "object" ? item.analysis_result : {};
      return String((result && result.primary_error_type) || "").trim() || "-";
    },
    historyIssueFamilyLabel(item) {
      const projection = item && typeof item.feedback_projection === "object" ? item.feedback_projection : {};
      const label = String((projection && projection.issue_family_label) || "").trim();
      return label || this.issueFamilyLabelFromError(this.historyPrimaryErrorType(item));
    },
    historyPrimaryErrorTypeLabel(item) {
      const projection = item && typeof item.feedback_projection === "object" ? item.feedback_projection : {};
      const label = String((projection && projection.primary_error_label) || "").trim();
      return label || this.errorTypeLabel(this.historyPrimaryErrorType(item));
    },
    historyPatchCount(item) {
      const projection = item && typeof item.feedback_projection === "object" ? item.feedback_projection : {};
      const projected = Number(projection && projection.patch_count);
      if (Number.isFinite(projected)) {
        return projected;
      }
      const result = item && typeof item.patch_result === "object" ? item.patch_result : {};
      const patches = Array.isArray(result && result.patches) ? result.patches : [];
      return patches.length;
    },
    historyPatchTargetAgentText(item) {
      const projection = item && typeof item.feedback_projection === "object" ? item.feedback_projection : {};
      const labels = Array.isArray(projection && projection.target_agent_labels) ? projection.target_agent_labels : [];
      if (labels.length) {
        return labels.join(" / ");
      }
      const result = item && typeof item.patch_result === "object" ? item.patch_result : {};
      const agents = this.patchTargetAgentsFromResult(result);
      return agents.length ? agents.map((value) => this.targetAgentLabel(value)).join(" / ") : "-";
    },
    historyPatchTypeText(item) {
      const projection = item && typeof item.feedback_projection === "object" ? item.feedback_projection : {};
      const patchTypes = Array.isArray(projection && projection.patch_types) ? projection.patch_types : [];
      if (patchTypes.length) {
        return patchTypes.join(" / ");
      }
      const result = item && typeof item.patch_result === "object" ? item.patch_result : {};
      const patches = Array.isArray(result && result.patches) ? result.patches : [];
      const values = [...new Set(patches.map((patch) => String((patch && patch.patch_type) || "").trim()).filter(Boolean))];
      return values.length ? values.join(" / ") : "-";
    },
    humanizeReferenceName(value, item = {}) {
      const text = String(value || "").trim();
      if (!text) {
        return "-";
      }
      const sourceType = String((item && item.source_type) || "").trim();
      if (text === "meta_reflection") {
        return "历史经验沉淀";
      }
      const normalized = text.replace(/\.[^.]+$/, "").replace(/^[0-9a-f]{12,64}[_-]/i, "").replace(/[_\s]+/g, "").toUpperCase();
      const aliases = {
        Q8R2: "ICH Q8(R2) 药物研发",
        Q6A: "ICH Q6A 质量标准",
        Q12: "ICH Q12 生命周期管理",
        ICHQ7: "ICH Q7 原料药 GMP",
        Q4B5R1: "ICH Q4B 附件 5(R1)",
        Q4B8R1: "ICH Q4B 附件 8(R1)",
      };
      if (aliases[normalized]) {
        return aliases[normalized];
      }
      if (sourceType === "药典数据" && text.endsWith(".json")) {
        return "药典数据";
      }
      return text.replace(/^[0-9a-f]{12,64}[_-]/i, "");
    },
    formatDisplayedReviewRule(item) {
      const raw = String(item || "").trim();
      if (!raw) {
        return "-";
      }
      if (/\s/.test(raw)) {
        return raw;
      }
      const ruleCode = raw.split(":", 1)[0].trim();
      const findings = Array.isArray(this.review && this.review.rule_findings) ? this.review.rule_findings : [];
      for (const row of findings) {
        if (!row || typeof row !== "object") {
          continue;
        }
        const code = String((row.rule_code || row.rule_id || "")).trim();
        const text = String((row.rule_text || "")).trim();
        if (code === ruleCode && text) {
          return text.startsWith(code) ? text : `${code} ${text}`;
        }
      }
      return raw;
    },
    riskFeedbackPseudoItem(item, index) {
      return {
        feedback_kind: "risk_item",
        feedback_key: `risk_item:${String(item && (item.rule_code || item.rule_id || item.issue_type || index) || index).trim()}:${String(item && (item.title || item.issue || item.problem || "") || "").trim().slice(0, 48)}`,
        rule_code: String(item && (item.rule_code || item.rule_id || "") || "").trim(),
        rule_text: String(item && (item.requirement_point || item.rule_text || "") || "").trim(),
        task_code: String(item && (item.rule_code || item.rule_id || item.issue_type || `risk-${index}`) || "").trim() || `risk-${index}`,
        task_question: String(item && (item.title || item.issue || item.problem || `风险项 ${index + 1}`) || "").trim(),
        status: String(item && (item.issue_type || item.status || "unsupported") || "").trim().toLowerCase(),
        basis: String(item && (item.requirement_point || item.rule_text || "") || "").trim(),
        problem: String(item && (item.title || item.issue || item.problem || "") || "").trim(),
        advice: String(item && (item.recommendation || item.suggested_fix || item.advice || "") || "").trim(),
        material_fact: String(item && (item.material_fact || "") || "").trim(),
        evidence_support: String(item && (item.evidence || "") || "").trim(),
        comparison: String(item && (item.comparison || "") || "").trim(),
        reason: String(item && (item.reason || "") || "").trim(),
        judgment_reason: String(item && (item.reason || "") || "").trim(),
        rule_requirement: String(item && (item.requirement_point || item.rule_text || "") || "").trim(),
      };
    },
  },
};
</script>

<style scoped>
.dialog-body { display: flex; flex-direction: column; gap: 16px; }
.summary-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.dialog-actions { display: flex; justify-content: flex-end; margin-top: -4px; }
.summary-card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; background: #ffffff; }
.summary-card.primary { background: linear-gradient(135deg, #eff6ff, #ffffff); border-color: #bfdbfe; }
.summary-card .label { font-size: 12px; color: #64748b; }
.summary-card .value { margin-top: 6px; font-size: 18px; font-weight: 700; color: #0f172a; }
.muted { color: #64748b; font-size: 12px; line-height: 1.7; }
.content-layout { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(320px, 0.9fr); gap: 12px; align-items: start; }
.content-main, .content-side { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.risk-panel { background: linear-gradient(180deg, #fffdfa 0%, #fff 100%); }
.risk-summary-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; min-width: 0; }
.mini-stat.compact { padding: 8px 10px; }
.risk-list { display: flex; flex-direction: column; gap: 10px; margin-top: 6px; }
.risk-card { border: 1px solid #f1e5c8; border-radius: 10px; background: #fffdf7; padding: 12px; }
.risk-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.risk-card-title { font-size: 13px; font-weight: 700; color: #0f172a; }
.risk-card-tags { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
.risk-card-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.risk-card-cell { border: 1px dashed #eadfbe; border-radius: 8px; background: #fff; padding: 10px; min-width: 0; }
.risk-card-cell.full { grid-column: 1 / -1; }
.panel-grid, .feedback-grid, .advanced-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.panel { border: 1px solid #e2e8f0; border-radius: 10px; background: #fff; padding: 14px; }
.panel-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 10px; }
.panel-title, .sub-title { font-size: 14px; font-weight: 600; color: #0f172a; }
.collapse-toggle { padding: 0; }
.section-line, .issue-item, .list-item { font-size: 13px; color: #334155; line-height: 1.8; }
.issue-list, .evidence-section { display: flex; flex-direction: column; gap: 8px; }
.review-rule-card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px; background: #f8fafc; }
.review-rule-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.review-rule-text { font-size: 13px; color: #334155; line-height: 1.75; word-break: break-word; }
.review-rule-finding { margin-top: 8px; font-size: 12px; color: #64748b; line-height: 1.7; }
.review-rule-feedback { margin-top: 10px; padding-top: 10px; }
.overview-panel { background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%); }
.conclusion-block { padding: 10px 12px; border: 1px solid #e2e8f0; border-radius: 10px; background: rgba(255, 255, 255, 0.85); margin-bottom: 10px; }
.conclusion-label { font-size: 12px; color: #64748b; margin-bottom: 6px; }
.conclusion-text { font-size: 13px; color: #334155; line-height: 1.85; white-space: pre-wrap; word-break: break-word; }
.overview-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
.verification-overview { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.verification-case-groups { display: flex; flex-direction: column; gap: 14px; margin-top: 12px; }
.verification-case-group { display: flex; flex-direction: column; gap: 10px; }
.verification-case-list { display: flex; flex-direction: column; gap: 10px; }
.verification-case-card { border: 1px solid #e2e8f0; border-radius: 10px; background: #f8fbff; padding: 12px; }
.verification-case-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.verification-case-tags { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.verification-compare-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.mini-stat { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px; background: #fff; }
.mini-label { font-size: 12px; color: #64748b; }
.mini-value { margin-top: 6px; font-size: 18px; font-weight: 700; color: #0f172a; }
.mini-value.success { color: #15803d; }
.mini-value.warning { color: #b45309; }
.tag-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.extracted-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.compact-list .issue-card { margin-bottom: 8px; }
.evidence-section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.evidence-section.rejected { margin-top: 8px; padding-top: 12px; border-top: 1px solid #eef2f7; }
.evidence-group { border-top: 1px solid #eef2f7; padding-top: 12px; margin-top: 12px; }
.evidence-group:first-child { margin-top: 0; padding-top: 0; border-top: none; }
.evidence-group-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.evidence-card, .issue-card { border: 1px solid #edf2f7; border-radius: 8px; background: #f8fafc; padding: 10px; margin-bottom: 10px; }
.issue-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
.issue-head-actions { display: inline-flex; align-items: center; gap: 8px; flex-shrink: 0; }
.verdict-groups, .reasoning-list { display: flex; flex-direction: column; gap: 14px; }
.verdict-group { display: flex; flex-direction: column; gap: 10px; }
.group-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.verdict-card.supported { background: #f8fffb; border-color: #bbf7d0; }
.verdict-summary { font-size: 14px; font-weight: 600; color: #0f172a; margin-bottom: 10px; }
.judgment-feedback { margin-top: 12px; padding-top: 12px; border-top: 1px solid #e2e8f0; display: flex; flex-direction: column; gap: 10px; }
.judgment-feedback-actions { display: flex; justify-content: flex-end; }
.judgment-feedback-detail { display: flex; flex-direction: column; gap: 10px; padding: 10px; border: 1px solid #e2e8f0; border-radius: 8px; background: #fff; }
.judgment-feedback-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.detail-grid.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.detail-block { border: 1px solid #edf2f7; border-radius: 8px; background: #fff; padding: 10px; min-width: 0; }
.detail-block.full { grid-column: 1 / -1; }
.detail-label { font-size: 12px; color: #64748b; margin-bottom: 4px; }
.detail-text { font-size: 13px; color: #334155; line-height: 1.75; white-space: pre-wrap; word-break: break-word; }
.side-panel { position: sticky; top: 0; }
.rejected-card { background: #fff7f7; border-color: #fecaca; }
.evidence-head { display: flex; justify-content: space-between; gap: 12px; }
.evidence-name, .issue-title { font-size: 13px; font-weight: 600; color: #0f172a; }
.score-box { text-align: right; font-size: 12px; color: #334155; }
.snippet { margin-top: 8px; padding: 10px; border-radius: 6px; background: #fff; color: #334155; font-size: 12px; line-height: 1.7; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
.evidence-feedback { margin-top: 10px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field.full { margin-top: 12px; }
.field label { font-size: 12px; color: #475569; }
.actions { display: flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.result-summary { display: flex; flex-direction: column; gap: 6px; }
.evaluation-box { margin-top: 16px; padding-top: 12px; border-top: 1px solid #eef2f7; }
.rule-summary { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 10px; font-size: 12px; color: #64748b; }
.advanced-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.empty { color: #94a3b8; font-size: 13px; line-height: 1.8; }
.error-box { margin-top: 10px; padding: 10px 12px; border-radius: 8px; background: #fff1f2; border: 1px solid #fecdd3; color: #be123c; font-size: 12px; line-height: 1.7; }
@media (max-width: 1400px) { .content-layout { grid-template-columns: 1fr; } .side-panel { position: static; } }
@media (max-width: 1200px) { .summary-strip, .panel-grid, .feedback-grid, .advanced-grid, .form-grid, .overview-stats, .verification-overview, .verification-compare-grid, .detail-grid, .detail-grid.compact { grid-template-columns: 1fr; } }
</style>
