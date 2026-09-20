<template>
  <div class="session-page">
    <div class="page-header">
      <div class="page-header-main">
        <h2>辅助审评任务会话</h2>
        <div class="session-meta-inline">
          <div class="session-meta-line">
            <span><strong>项目名称：</strong>{{ project.project_name || "-" }}</span>
            <span><strong>注册大类：</strong>{{ project.registration_scope || "-" }}</span>
            <span><strong>注册分类：</strong>{{ project.registration_leaf || "-" }}</span>
          </div>
          <div class="session-meta-line">
            <span><strong>项目 ID：</strong>{{ project.project_id || "-" }}</span>
            <span class="feedback-loop-inline">
              <strong>反馈优化回路：</strong>
              <el-switch
                :value="experimentConfig.enableFeedbackOptimize"
                active-text="开启"
                inactive-text="关闭"
                @input="experimentConfig.enableFeedbackOptimize = $event"
              />
            </span>
            <span class="session-meta-hint">用于消融实验，决定本次运行后的反馈是否进入优化闭环。</span>
          </div>
          <div v-if="runs.length" class="session-meta-line">
            <span class="reviewed-section-picker">
              <strong>历史运行：</strong>
              <el-select
                :value="selectedRunId"
                size="mini"
                filterable
                placeholder="选择运行记录"
                style="min-width: 420px"
                @change="onRunHistoryChange"
              >
                <el-option
                  v-for="item in runs"
                  :key="item.run_id"
                  :label="runHistoryOptionLabel(item)"
                  :value="item.run_id"
                />
              </el-select>
            </span>
          </div>
          <div v-if="reviewedSectionOptions.length" class="session-meta-line reviewed-section-line">
            <span class="reviewed-section-picker">
              <strong>已审评章节：</strong>
              <el-select
                :value="currentBrowseSectionId"
                size="mini"
                filterable
                placeholder="选择已审评章节"
                @change="onReviewedSectionChange"
              >
                <el-option-group
                  v-for="group in reviewedSectionOptions"
                  :key="group.label"
                  :label="group.label"
                >
                  <el-option
                    v-for="item in group.options"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-option-group>
              </el-select>
            </span>
          </div>
        </div>
      </div>
    </div>

    <el-card>
      <pre-review-action-toolbar
        slot="header"
        :running="running"
        :running-section="runningSection"
        :running-module="runningModule"
        :current-browse-section-id="currentBrowseSectionId"
        :current-browse-scope-section-id="currentBrowseScopeSectionId"
        :active-doc-id="activeDocId"
        :has-review-result="canOpenReviewResult"
        :can-export-review-conclusions="canExportReviewConclusions"
        :exporting-review-conclusions="exportingReviewConclusions"
        :section-action-hint="sectionActionHint"
        @run-all="run"
        @run-section="runCurrentSectionReview"
        @run-module="runCurrentModuleReview"
        @open-review-result="openReviewResultDialog"
        @export-review-conclusions="exportCurrentRunReviewConclusions"
        @open-preview="openPreviewDialog"
      />

      <el-alert
        v-if="runResultLoadError"
        class="run-result-load-error"
        type="warning"
        :closable="false"
        show-icon
      >
        <span slot="title">{{ runResultLoadError }}</span>
        <el-button type="text" size="mini" @click="retrySelectedRunResult">重新加载</el-button>
      </el-alert>

      <el-alert v-if="selectedRunIncomplete" type="warning" :closable="false" show-icon>
        <span slot="title">本次审评未完成，仅展示已保存的部分结果，不能导出完整审评报告。</span>
        <div>已完成阶段：{{ (selectedRunSummary.completed_stages || []).join('、') || '未记录' }}</div>
        <div>失败阶段：{{ (selectedRunSummary.error || {}).stage || '未记录' }}</div>
        <div>未完成范围：{{ (selectedRunSummary.incomplete_stages || []).join('、') || '未记录，请核对任务日志' }}</div>
        <div>已完成章节：{{ (selectedRunSummary.completed_section_ids || []).join('、') || '未记录' }}</div>
        <div>未完成章节：{{ (selectedRunSummary.incomplete_section_ids || []).join('、') || '未记录' }}</div>
        <details v-if="selectedRunPartialText">
          <summary>查看已保存的部分结果（只读，不是完整审评结论）</summary>
          <pre style="max-height: 320px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;">{{ selectedRunPartialText }}</pre>
        </details>
        <div>历史成功结果可从运行历史中单独选择，不代表本次任务成功。</div>
      </el-alert>

      <div class="workspace" :class="{ 'left-pane-collapsed': leftPaneCollapsed }">
        <div class="pane left" :class="{ collapsed: leftPaneCollapsed }">
          <div class="pane-head">
            <div v-if="!leftPaneCollapsed">
              <div class="title">章节目录</div>
              <div class="muted">{{ activeDocLabel }}</div>
              <el-select
                v-if="currentBrowseFiles.length > 1"
                :value="activeDocId"
                size="mini"
                filterable
                style="margin-top: 8px; width: 100%"
                placeholder="选择当前章节对应文件"
                @change="onDocChange"
              >
                <el-option
                  v-for="item in currentBrowseFiles"
                  :key="item.doc_id"
                  :label="item.file_name"
                  :value="item.doc_id"
                />
              </el-select>
            </div>
            <div class="actions">
              <el-button
                v-if="!leftPaneCollapsed"
                size="mini"
                icon="el-icon-plus"
                circle
                @click="setBrowseTreeExpanded(true)"
              />
              <el-button
                v-if="!leftPaneCollapsed"
                size="mini"
                icon="el-icon-minus"
                circle
                @click="setBrowseTreeExpanded(false)"
              />
              <el-button
                size="mini"
                :icon="leftPaneCollapsed ? 'el-icon-d-arrow-right' : 'el-icon-d-arrow-left'"
                circle
                @click="toggleLeftPaneCollapsed"
              />
            </div>
          </div>
          <el-tree
            v-if="leftTreeData.length && !leftPaneCollapsed"
            ref="browseTree"
            class="chapter-tree"
            :data="leftTreeData"
            node-key="section_id"
            :props="treeProps"
            :highlight-current="true"
            :default-expand-all="false"
            :expand-on-click-node="false"
            @node-click="onBrowseSectionSelect"
          />
          <div v-else-if="!leftPaneCollapsed" class="empty">当前没有可展示的章节目录。</div>
        </div>

        <div class="pane middle">
          <pre-review-content-pane
            :current-content-section-label="currentContentSectionLabel"
            :rendered-markdown-content="renderedMarkdownContent"
            :editor-text="editorText"
            :content-char-count="currentContentSectionCharCount"
            :content-view-mode="contentViewMode"
            :editing="contentEditorMode"
            :saving="savingContent"
            :dirty="isContentDirty"
            :loading="loadingSubmissionContent"
            :error-message="submissionContentError"
            @change-view-mode="onContentViewModeChange"
            @editor-input="onEditorTextChange"
            @start-edit="startEditingContent"
            @cancel-edit="cancelEditingContent"
            @save="saveEditedContent"
            @retry="retrySubmissionContent"
          />
        </div>

        <div class="pane right">
          <pre-review-execution-panel
            title="审评进度"
            :status-label="runStreamStatusLabel"
            :running="running"
            :running-section="runningSection"
            :run-stream-logs="runStreamLogs"
            :run-stream-log-groups="runStreamLogGroups"
            :expanded-sections="expandedLogSections"
            @clear-logs="clearRunStreamLogs"
            @update:expanded-sections="expandedLogSections = $event"
          />

          <pre-review-session-review-panel
            :review="selectedDisplayReview"
            :current-browse-section-label="currentBrowseSectionLabel"
            :review-conclusion-label="reviewConclusionLabel"
            :feedback-form="feedbackForm"
            :approved-evidence-groups="selectedApprovedEvidenceGroups"
            :submitting-feedback="submittingFeedback"
            :feedback-loop-mode="selectedRunFeedbackLoopMode"
            :can-open-detail="canOpenReviewResult"
            @open-detail="openReviewResultDialog"
            @submit-feedback="submitSectionFeedback"
          />
        </div>
      </div>
    </el-card>

    <el-dialog title="原文件预览" :visible.sync="previewDialog.visible" width="88%" top="4vh" @close="closePreviewDialog">
      <div class="preview-meta">
        <span>文件名：{{ activeDocLabel }}</span>
        <span>文件类型：{{ activeFileType || "-" }}</span>
      </div>
      <div v-if="previewUrl" class="preview-frame-wrap">
        <iframe :src="previewUrl" class="preview-frame" frameborder="0" />
      </div>
      <div v-else class="empty">当前文件没有可预览地址。</div>
      <span slot="footer">
        <el-button @click="closePreviewDialog">关闭</el-button>
        <el-button v-if="previewUrl" type="primary" @click="openPreviewInNewTab">新窗口打开</el-button>
      </span>
    </el-dialog>

    <pre-review-result-dialog
      :key="reviewResultDialogRenderKey"
      :visible.sync="reviewResultDialog.visible"
      :review="selectedDisplayReview"
      :current-browse-section-id="currentBrowseSectionId"
      :current-browse-section-label="currentBrowseSectionLabel"
      :review-conclusion-label="reviewConclusionLabel"
      :normalized-supported-points="normalizedSupportedPoints"
      :normalized-unsupported-points="normalizedUnsupportedPoints"
      :normalized-missing-points="normalizedMissingPoints"
      :normalized-risk-points="normalizedRiskPoints"
      :normalized-questions="normalizedQuestions"
      :metrics="selectedRunMetrics"
      :retrieval-detail="selectedTraceRetrievalDetail"
      :trace-artifact="selectedSectionTrace && selectedSectionTrace.trace_artifact"
      :loading-trace-artifact="loadingTraceArtifact"
      :approved-evidence-groups="selectedApprovedEvidenceGroups"
      :rejected-evidence-groups="selectedRejectedEvidenceGroups"
      :feedback-form="feedbackForm"
      :selected-run="selectedRun"
      :submitting-feedback="submittingFeedback"
      :feedback-optimize-result="feedbackOptimizeResult"
      :p52-feedback-patches="p52FeedbackPatches"
      :feedback-history="sectionFeedbackHistory"
      :section-rules-detail="sectionRulesDetail"
      :selected-prompt-rules="selectedPromptRules"
      :prompt-rules-detail="sectionPromptRuleDetail"
      :section-example-detail="sectionExampleDetail"
      :experience-memory-detail="sectionExperienceMemoryDetail"
      :execution-audit-detail="sectionExecutionAuditDetail"
      :ablation-detail="sectionAblationDetail"
      :loading-ablation="runningAblation"
      :optimize-evaluation-form="optimizeEvaluationForm"
      :feedback-loop-mode="selectedRunFeedbackLoopMode"
      :feedback-loop-locked="true"
      :replaying-feedback-optimize="replayingFeedbackOptimize"
      :replaying-meta-reflection="replayingMetaReflection"
      :replaying-p52-feedback-verify="replayingP52FeedbackVerify"
      :submitting-p52-feedback-optimize="submittingP52FeedbackOptimize"
      :loading-p52-feedback-patches="loadingP52FeedbackPatches"
      :acting-p52-patch-id="actingP52PatchId"
      @view-trace-artifact="openTraceArtifactDialog"
      @submit-feedback="submitSectionFeedback"
      @submit-p52-feedback-optimize="handleSubmitP52FeedbackOptimize"
      @submit-optimize-evaluation="submitOptimizeEvaluation"
      @replay-feedback-optimize="handleReplayFeedbackOptimize"
      @replay-meta-reflection="handleReplayMetaReflection"
      @refresh-p52-feedback-patches="loadSelectedP52FeedbackPatches(true)"
      @replay-verify-p52-feedback="handleReplayVerifyP52Feedback"
      @approve-p52-feedback-patch="handleApproveP52FeedbackPatch"
      @reject-p52-feedback-patch="handleRejectP52FeedbackPatch"
      @run-ablation="runSectionAblationStudy"
    />

    <el-dialog
      :title="traceArtifactDialog.title || '原始 Trace 文件'"
      :visible.sync="traceArtifactDialog.visible"
      width="90%"
    >
      <div class="preview-meta">
        <span>artifact 路径：{{ traceArtifactDialog.file_path || "-" }}</span>
      </div>
      <el-input
        :value="traceArtifactDialog.content"
        type="textarea"
        :rows="24"
        resize="none"
        readonly
      />
      <span slot="footer">
        <el-button @click="traceArtifactDialog.visible = false">关闭</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import {
  addFeedback,
  ctdCatalog,
  evaluateOptimizeFeedback,
  executionAudits,
  feedbackStats,
  generateRegressionCases,
  getLatestPreReviewMainTask,
  getPreReviewTaskProgress,
  listSubmissions,
  optimizeFeedbackAsync,
  optimizeP52FeedbackAsync,
  projectDetail,
  approveP52FeedbackPatch,
  listP52FeedbackPatches,
  rejectP52FeedbackPatch,
  replayP52MetaReflectionAsync,
  replayFeedbackOptimizeAsync,
  replayMetaReflectionAsync,
  replayVerifyP52FeedbackAsync,
  runP52AblationStudy,
  runAblationStudyAsync as runAblationStudyAsyncApi,
  sectionExamples,
  sectionExperienceMemory,
  sectionPromptRules,
  sectionRules,
  runHistory,
  exportReviewConclusions,
  startModuleReplayTask,
  startSectionReplayTask,
  saveSubmissionContent,
  sectionOverview,
  sectionTraceArtifact,
  startPreReviewTask,
  sectionPatchCandidates,
  sectionTraces,
  submissionContent,
  submissionSectionDiagnostics,
  submissionPreviewUrl,
} from "@/api/prereview";
import MarkdownIt from "markdown-it";
import { normalizeReviewDomain } from "@/constants/preReviewEnums";
import { formatReviewConfidence } from "@/utils/preReviewDisplay";
import { buildSectionDisplayLabel, stripSectionDisplayName } from "@/utils/ctdDisplay";
import PreReviewActionToolbar from "@/components/pre-review/PreReviewActionToolbar.vue";
import PreReviewContentPane from "@/components/pre-review/PreReviewContentPane.vue";
import PreReviewExecutionPanel from "@/components/pre-review/PreReviewExecutionPanel.vue";
import PreReviewResultDialog from "@/components/pre-review/PreReviewResultDialog.vue";
import PreReviewSessionReviewPanel from "@/components/pre-review/PreReviewSessionReviewPanel.vue";

function cloneTree(nodes) {
  return Array.isArray(nodes) ? JSON.parse(JSON.stringify(nodes)) : [];
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeSectionId(value) {
  const raw = String(value || "").trim().replace(/\.+$/, "");
  if (!raw) {
    return "";
  }
  return raw
    .split(".")
    .filter(Boolean)
    .map((part) => (/[A-Za-z]+$/.test(part) ? part.toLowerCase() : part))
    .join(".");
}

function normalizeStringList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

const RUN_PROGRESS_POLL_INTERVAL_MS = 10000;
const LONG_WORKFLOW_POLL_INTERVAL_MS = 2000;
const P52_VERIFY_RESULT_POLL_INTERVAL_MS = 30000;
const P52_VERIFY_RESULT_POLL_MAX_TIMES = 6;

function normalizeQuestionList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => ({
    issue: String((item && item.issue) || "").trim(),
    basis: String((item && item.basis) || "").trim(),
    requested_action: String((item && item.requested_action) || "").trim(),
  })).filter((item) => item.issue || item.basis || item.requested_action);
}

const markdownRenderer = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

markdownRenderer.renderer.rules.table_open = () => '<table class="md-table">';

function renderMarkdownContent(text) {
  const source = String(text || "");
  if (!source.trim()) {
    return '<div class="empty-markdown">暂无可预览内容。</div>';
  }
  return markdownRenderer.render(source).replace(
    /<p>\s*【([\s\S]*?)】\s*<\/p>/g,
    (_match, labelContent) => `<div class="md-label">【${labelContent}】</div>`,
  );
}

function renderSimpleMarkdown(text) {
  return renderMarkdownContent(text);
  const source = String(text || "").trim();
  if (!source) {
    return '<div class="empty-markdown">暂无可预览内容。</div>';
  }
  const lines = source.split(/\r?\n/);
  const html = [];
  let inUl = false;
  let inOl = false;
  let tableBuffer = [];
  const flushLists = () => {
    if (inUl) {
      html.push("</ul>");
      inUl = false;
    }
    if (inOl) {
      html.push("</ol>");
      inOl = false;
    }
  };
  const flushTable = () => {
    if (!tableBuffer.length) {
      return;
    }
    const rows = tableBuffer.map((line) => line.split("|").slice(1, -1).map((cell) => escapeHtml(cell.trim())));
    tableBuffer = [];
    html.push('<table class="md-table"><tbody>');
    rows.forEach((row, rowIndex) => {
      if (rowIndex === 1 && row.every((cell) => /^-+$/.test(cell))) {
        return;
      }
      html.push("<tr>");
      row.forEach((cell) => {
        html.push(rowIndex === 0 ? `<th>${cell}</th>` : `<td>${cell}</td>`);
      });
      html.push("</tr>");
    });
    html.push("</tbody></table>");
  };
  lines.forEach((rawLine) => {
    const trimmed = String(rawLine || "").trim();
    if (!trimmed) {
      flushLists();
      flushTable();
      html.push('<div class="md-space"></div>');
      return;
    }
    if (trimmed.startsWith("|")) {
      flushLists();
      tableBuffer.push(trimmed);
      return;
    }
    flushTable();
    const imageMatch = trimmed.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    if (imageMatch) {
      flushLists();
      return;
    }
    const displayText = trimmed.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, "").trim();
    if (!displayText) {
      flushLists();
      return;
    }
    if (/^【.+】$/.test(displayText)) {
      flushLists();
      html.push(`<div class="md-label">${escapeHtml(displayText)}</div>`);
      return;
    }
    if (/^####\s+/.test(displayText)) {
      flushLists();
      html.push(`<h4>${escapeHtml(displayText.replace(/^####\s+/, ""))}</h4>`);
      return;
    }
    if (/^###\s+/.test(displayText)) {
      flushLists();
      html.push(`<h4>${escapeHtml(displayText.replace(/^###\s+/, ""))}</h4>`);
      return;
    }
    if (/^##\s+/.test(displayText)) {
      flushLists();
      html.push(`<h3>${escapeHtml(displayText.replace(/^##\s+/, ""))}</h3>`);
      return;
    }
    if (/^#\s+/.test(displayText)) {
      flushLists();
      html.push(`<h2>${escapeHtml(displayText.replace(/^#\s+/, ""))}</h2>`);
      return;
    }
    if (/^-\s+/.test(displayText)) {
      if (inOl) {
        html.push("</ol>");
        inOl = false;
      }
      if (!inUl) {
        html.push("<ul>");
        inUl = true;
      }
      html.push(`<li>${escapeHtml(displayText.replace(/^-\s+/, ""))}</li>`);
      return;
    }
    if (/^\d+\.\s+/.test(displayText)) {
      if (inUl) {
        html.push("</ul>");
        inUl = false;
      }
      if (!inOl) {
        html.push("<ol>");
        inOl = true;
      }
      html.push(`<li>${escapeHtml(displayText.replace(/^\d+\.\s+/, ""))}</li>`);
      return;
    }
    flushLists();
    html.push(`<p>${escapeHtml(displayText)}</p>`);
  });
  flushLists();
  flushTable();
  return html.join("");
}

export default {
  name: "PreReviewSession",
  components: {
    PreReviewActionToolbar,
    PreReviewContentPane,
    PreReviewExecutionPanel,
    PreReviewResultDialog,
    PreReviewSessionReviewPanel,
  },
  data() {
    return {
      projectId: this.$route.params.projectId,
      project: {},
      ctdCatalogData: [],
      browseTreeData: [],
      flatCatalogNodes: [],
      catalogNodeMap: {},
      catalogLeafIdMap: {},
      submissions: [],
      runs: [],
      resultOverview: null,
      displayedRunId: "",
      runResultLoadError: "",
      runResultRequestToken: 0,
      sectionTraceMap: {},
      sectionPatchMap: {},
      sectionFeedbackHistory: null,
      sectionRulesDetail: null,
      sectionPromptRuleDetail: null,
      sectionExampleDetail: null,
      sectionExperienceMemoryDetail: null,
      sectionExecutionAuditDetail: null,
      sectionAblationDetail: null,
      sectionAblationCache: {},
      sectionRunDetailLoadState: { key: "", promise: null },
      sectionStaticDetailLoadState: { key: "", promise: null },
      sectionDiagnosticsMap: {},
      submissionContentMap: {},
      submissionContentRequestMap: {},
      submissionContentRequestToken: 0,
      sectionDiagnosticsRequestTokens: {},
      traceArtifactRequestToken: 0,
      saveContentRequestToken: 0,
      currentBrowseSectionId: "",
      currentBrowseNodeId: "",
      activeDocId: "",
      contentDisplayDocId: "",
      activeFileType: "",
      previewUrl: "",
      contentSections: [],
      contentViewMode: "cleaned",
      contentEditorMode: false,
      editorText: "",
      treeProps: { children: "children_sections", label: "label" },
      running: false,
      savingContent: false,
      loadingSubmissionContent: false,
      submissionContentError: "",
      runningSection: false,
      runningModule: false,
      exportingReviewConclusions: false,
      submittingFeedback: false,
      loadingSectionDiagnostics: false,
      selectedRun: null,
      feedbackForm: {
        decision: "valid",
        feedback_text: "",
        enableOptimize: true,
        conclusion_feedback: "correct",
        retrieval_feedback: "unknown",
        issue_feedback_map: {},
        evidence_feedback_map: {},
        missing_item_feedback_text: "",
        missing_item_feedback_reason: "",
        reference_example_title: "",
        reference_example_content: "",
      },
      optimizeEvaluationForm: {
        overall_verdict: "unknown",
        comment: "",
        patch_feedback_map: {},
      },
      feedbackOptimizeResult: {
        feedback_key: "",
        analysis_result: null,
        patch_result: null,
        candidate_patches: [],
        verification_result: null,
        verification_plan: null,
        meta_reflection: null,
        feedback_projection: null,
        reference_example: null,
        optimize_evaluation: null,
        feedback_optimize_status: "",
        candidate_register_status: "",
        replay_status: "",
        error_message: "",
      },
      experimentConfig: { enableFeedbackOptimize: true },
      runPollingTimer: null,
      runPollingTaskId: "",
      runPollingRunId: "",
      runPollingCancel: null,
      runPollingContext: null,
      mainRunOperationEpoch: 0,
      runStreamCursor: 0,
      runStreamStatus: "idle",
      runStreamLogs: [],
      expandedLogSections: ["__run__"],
      previewDialog: { visible: false },
      reviewResultDialog: { visible: false },
      traceArtifactDialog: { visible: false, title: "", content: "", file_path: "" },
      loadingTraceArtifact: false,
      replayingFeedbackOptimize: false,
      replayingMetaReflection: false,
      replayingP52FeedbackVerify: false,
      submittingP52FeedbackOptimize: false,
      loadingP52FeedbackPatches: false,
      p52FeedbackPatchesRequestToken: 0,
      actingP52PatchId: "",
      p52FeedbackPatches: [],
      runningAblation: false,
      runStreamScopeSectionId: "",
      leftPaneCollapsed: false,
      p52VerifyPollTimer: null,
      p52VerifyPollCount: 0,
      sessionProjectToken: 0,
      longWorkflowTaskStates: {},
      longWorkflowTaskEpochs: {},
      longWorkflowTaskPollers: {},
    };
  },
  computed: {
    isChemicalPharmacyProject() {
      return ["化药", "化学药"].includes(String(this.project.registration_scope || "").trim());
    },
    leftTreeData() {
      return this.browseTreeData;
    },
    currentBrowseFiles() {
      const sid = String(this.currentBrowseSectionId || "").trim();
      if (!sid) {
        return this.submissions;
      }
      const matched = (this.submissions || [])
        .filter((item) => this.submissionCoversSection(item, sid))
        .sort((a, b) => this.compareSubmissionCoverage(a, b, sid));
      return matched.length ? matched : this.submissions;
    },
    currentBrowseSection() {
      const sid = normalizeSectionId(this.currentBrowseSectionId);
      return (sid && this.catalogNodeMap[sid]) || null;
    },
    currentBrowseLeafSectionIds() {
      const sid = normalizeSectionId(this.currentBrowseSectionId);
      return (sid && this.catalogLeafIdMap[sid]) ? this.catalogLeafIdMap[sid] : [];
    },
    currentBrowseScopeSectionId() {
      const scopeId = String(this.currentBrowseNodeId || "").trim();
      return scopeId || String(this.currentBrowseSectionId || "").trim();
    },
    sectionActionHint() {
      const current = this.currentBrowseSection;
      if (!current) {
        return "";
      }
      const leafIds = this.currentBrowseLeafSectionIds;
      if (leafIds.length > 1) {
        return `当前节点将自动展开并审评 ${leafIds.length} 个叶子章节`;
      }
      return "叶子章节将按当前节点直接审评";
    },
    currentContentSection() {
      const sid = normalizeSectionId(this.currentBrowseSectionId);
      const list = Array.isArray(this.contentSections) ? this.contentSections : [];
      if (String(this.contentDisplayDocId || "").trim() !== String(this.activeDocId || "").trim()) {
        return null;
      }
      const exact = list.find((item) => normalizeSectionId(item && item.section_id) === sid) || null;
      return exact || (!sid ? list[0] || null : null);
    },
    activeDocLabel() {
      const row = this.submissions.find((item) => item.doc_id === this.activeDocId);
      return row ? row.file_name : "未选择申报资料";
    },
    currentBrowseSectionLabel() {
      const current = this.currentBrowseSection;
      return current
        ? stripSectionDisplayName(current.section_name || current.label || current.section_id || "当前章节")
        : "当前章节";
    },
    currentContentSectionLabel() {
      const current = this.currentContentSection;
      return current
        ? stripSectionDisplayName(current.section_name || current.section_title || current.section_id || "当前章节")
        : "当前章节";
    },
    currentContentSectionCharCount() {
      const current = this.currentContentSection;
      if (!current) {
        return 0;
      }
      const explicit = Number(current.char_count || 0);
      if (explicit > 0) {
        return explicit;
      }
      return String(current.cleaned_markdown || current.display_content || current.raw_content || current.content || "").length;
    },
    renderedMarkdownContent() {
      return renderMarkdownContent(this.editorText);
    },
    isContentDirty() {
      return String(this.editorText || "").trim() !== String(this.currentEditorBaselineText() || "").trim();
    },
    selectedRunMetrics() {
      const metrics = (this.selectedRun && this.selectedRun.metrics) || {};
      const review = metrics.review || {};
      const problemDetection = metrics.problem_detection || review.problem_detection || {};
      return {
        review,
        retrieval: metrics.retrieval || {},
        feedback: metrics.feedback || {},
        trajectory: metrics.trajectory || {},
        problem_detection: problemDetection,
      };
    },
    selectedRunId() {
      return String((this.selectedRun && this.selectedRun.run_id) || "").trim();
    },
    selectedRunFeedbackLoopMode() {
      return (this.selectedRun && this.selectedRun.feedback_loop_mode) || "feedback_optimize";
    },
    canOpenReviewResult() {
      return !!this.selectedDisplayReview;
    },
    selectedRunSummary() {
      return (this.selectedRun && this.selectedRun.summary_payload) || {};
    },
    selectedRunIncomplete() {
      const summary = this.selectedRunSummary;
      return summary.review_complete === false || ["failed", "running", "pending", "cancelled", "interrupted"].includes(summary.execution_status);
    },
    selectedRunPartialText() {
      const partial = this.selectedRunSummary.partial_result;
      return partial && typeof partial === "object" && Object.keys(partial).length
        ? JSON.stringify(partial, null, 2) : "";
    },
    canExportReviewConclusions() {
      return !!(this.selectedRun && this.selectedRun.run_id) && !this.selectedRunIncomplete;
    },
    firstReviewedSectionOption() {
      const groups = Array.isArray(this.reviewedSectionOptions) ? this.reviewedSectionOptions : [];
      for (const group of groups) {
        const options = Array.isArray(group && group.options) ? group.options : [];
        if (options.length) {
          return options[0];
        }
      }
      return null;
    },
    selectedFullDocumentSummary() {
      return this.resultOverview && this.resultOverview.full_document_summary
        ? this.resultOverview.full_document_summary
        : null;
    },
    reviewedSectionOptions() {
      const summary = Array.isArray(this.resultOverview && this.resultOverview.chapter_conclusion_summary)
        ? this.resultOverview.chapter_conclusion_summary
        : [];
      const groups = {};
      const order = ["模块一", "模块二", "模块三", "模块四", "模块五", "其他"];
      summary
        .filter((item) => item && item.status === "reviewed" && item.section_id)
        .forEach((item) => {
          const sectionId = String(item.section_id || "").trim();
          if (!sectionId) {
            return;
          }
          const moduleId = sectionId.split(".")[0] || "其他";
          const moduleLabel = this.reviewedSectionModuleLabel(moduleId);
          if (!groups[moduleLabel]) {
            groups[moduleLabel] = [];
          }
          groups[moduleLabel].push({
            value: sectionId,
            label: buildSectionDisplayLabel(sectionId, item.section_name),
          });
        });
      return order.filter((label) => Array.isArray(groups[label]) && groups[label].length).map((label) => ({
        label,
        options: groups[label],
      }));
    },
    runStreamStatusLabel() {
      const mapping = {
        idle: "未开始",
        connecting: "连接中",
        running: "执行中",
        completed: "已完成",
        failed: "失败",
      };
      return mapping[this.runStreamStatus] || this.runStreamStatus || "-";
    },
    activeDocSectionDiagnostics() {
      return this.sectionDiagnosticsMap[this.activeDocId] || null;
    },
    runStreamLogGroups() {
      const groups = [];
      const buckets = {};
      const scopedSectionId = String(this.runStreamScopeSectionId || "").trim();
      (this.runStreamLogs || []).forEach((item) => {
        const key = item.sectionKey || "__run__";
        if (scopedSectionId && key !== "__run__" && key !== scopedSectionId) {
          return;
        }
        if (!buckets[key]) {
          buckets[key] = {
            key,
            label: item.sectionLabel || (key === "__run__" ? "运行级事件" : key),
            items: [],
          };
          groups.push(buckets[key]);
        }
        buckets[key].items.push(item);
      });
      return groups;
    },
    selectedSectionTrace() {
      return this.sectionTraceMap[this.currentBrowseSectionId] || null;
    },
    selectedTraceRetrievalDetail() {
      const detail = (this.selectedSectionTrace && this.selectedSectionTrace.retrieval_detail) || {};
      const retrievalEvaluation = this.selectedSectionTrace && this.selectedSectionTrace.retrieval_evaluation
        && typeof this.selectedSectionTrace.retrieval_evaluation === "object"
        ? this.selectedSectionTrace.retrieval_evaluation
        : {};
      return {
        metrics: detail.metrics || {},
        source_breakdown: detail.source_breakdown || {},
        error_breakdown: detail.error_breakdown || {},
        rejected_count: Number(retrievalEvaluation.rejected_count || 0) || 0,
      };
    },
    selectedDisplayReview() {
      if (!this.resultOverview) {
        return null;
      }
      const sid = this.currentBrowseSectionId;
      const standardized = (this.resultOverview.standardized_output_by_section_id || {})[sid];
      if (standardized) {
        return standardized;
      }
      const sectionOutput = (this.resultOverview.section_output_by_section_id || {})[sid];
      if (sectionOutput) {
        return sectionOutput;
      }
      const conclusion = (this.resultOverview.conclusion_by_section_id || {})[sid];
      return conclusion && conclusion.standard_output ? conclusion.standard_output : null;
    },
    selectedApprovedEvidenceGroups() {
      const materials = Array.isArray(this.selectedSectionTrace && this.selectedSectionTrace.retrieved_materials)
        ? this.selectedSectionTrace.retrieved_materials
        : [];
      return this.groupEvidenceBySource(materials);
    },
    selectedRejectedEvidenceGroups() {
      return [];
    },
    normalizedSupportedPoints() {
      return normalizeStringList(this.selectedDisplayReview && this.selectedDisplayReview.supported_points);
    },
    normalizedUnsupportedPoints() {
      return normalizeStringList(this.selectedDisplayReview && this.selectedDisplayReview.unsupported_points);
    },
    normalizedMissingPoints() {
      return normalizeStringList(this.selectedDisplayReview && this.selectedDisplayReview.missing_points);
    },
    normalizedRiskPoints() {
      return normalizeStringList(this.selectedDisplayReview && this.selectedDisplayReview.risk_points);
    },
    normalizedQuestions() {
      return normalizeQuestionList(this.selectedDisplayReview && this.selectedDisplayReview.questions);
    },
    currentReviewSummaryText() {
      return String((this.selectedDisplayReview && (this.selectedDisplayReview.section_summary || this.selectedDisplayReview.summary)) || "").trim();
    },
    currentReviewConfidenceText() {
      return formatReviewConfidence(this.selectedDisplayReview && this.selectedDisplayReview.confidence);
    },
    reviewResultDialogRenderKey() {
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      return `${runId || "no-run"}|${sectionId || "no-section"}`;
    },
    normalizedEvidenceRefs() {
      return normalizeStringList(this.selectedDisplayReview && this.selectedDisplayReview.evidence_refs);
    },
    formattedFeedbackAnalysis() {
      return JSON.stringify(this.feedbackOptimizeResult.analysis_result || {}, null, 2);
    },
    formattedFeedbackPatch() {
      return JSON.stringify(this.feedbackOptimizeResult.patch_result || {}, null, 2);
    },
    displayedPatchCandidates() {
      const patchResult = this.feedbackOptimizeResult.patch_result;
      return patchResult && typeof patchResult === "object" && Array.isArray(patchResult.patches) ? patchResult.patches : [];
    },
    historicalPatchCandidates() {
      return Array.isArray(this.sectionPatchMap[this.currentBrowseSectionId]) ? this.sectionPatchMap[this.currentBrowseSectionId] : [];
    },
    selectedPromptRules() {
      const promptRules = this.selectedSectionTrace && this.selectedSectionTrace.prompt_rules && typeof this.selectedSectionTrace.prompt_rules === "object"
        ? this.selectedSectionTrace.prompt_rules
        : {};
      return {
        planner: Array.isArray(promptRules.planner) ? promptRules.planner : [],
        retrieval_evaluator: Array.isArray(promptRules.retrieval_evaluator) ? promptRules.retrieval_evaluator : [],
        reviewer: Array.isArray(promptRules.reviewer) ? promptRules.reviewer : [],
      };
    },
  },
  watch: {
    "$route.params.projectId"(nextProjectId) {
      const normalizedProjectId = String(nextProjectId || "").trim();
      if (normalizedProjectId === String(this.projectId || "").trim()) {
        return;
      }
      this.reloadProjectSession(normalizedProjectId);
    },
    "$route.query.run_id"(nextRunId) {
      const runId = String(nextRunId || "").trim();
      if (!runId || runId === this.selectedRunId) {
        return;
      }
      const matched = this.runs.find((item) => String((item && item.run_id) || "").trim() === runId);
      if (matched) {
        this.viewRun(matched);
      }
    },
    currentBrowseSectionId() {
      this.cancelAllLongWorkflowTaskPolling("已切换章节，原后台操作不再回写当前页面");
      this.p52FeedbackPatchesRequestToken += 1;
      this.traceArtifactRequestToken += 1;
      this.loadingTraceArtifact = false;
      this.syncBrowseTreeCurrent();
      this.syncSectionAblationFromCache();
      this.clearSelectedSectionDetailState();
      this.resetSectionFeedbackDraft();
      this.loadingP52FeedbackPatches = false;
      this.contentEditorMode = false;
      this.syncEditorToCurrentSection();
      if (this.reviewResultDialog && this.reviewResultDialog.visible) {
        this.loadSelectedSectionDetailBundle();
      }
    },
    contentViewMode() {
      if (!this.contentEditorMode) {
        this.syncEditorToCurrentSection();
      }
    },
  },
  async mounted() {
    await this.reloadProjectSession(this.projectId);
  },
  beforeDestroy() {
    this.submissionContentRequestToken += 1;
    this.traceArtifactRequestToken += 1;
    this.saveContentRequestToken += 1;
    this.p52FeedbackPatchesRequestToken += 1;
    this.runResultRequestToken += 1;
    this.cancelAllLongWorkflowTaskPolling("页面已关闭");
    this.stopRunPolling();
    this.stopP52VerifyResultPolling();
  },
  methods: {
    createProjectSessionToken(projectId) {
      this.projectId = String(projectId || "").trim();
      this.sessionProjectToken += 1;
      return this.sessionProjectToken;
    },
    isProjectSessionTokenActive(token) {
      return Number(token || 0) === Number(this.sessionProjectToken || 0);
    },
    resetProjectScopedState() {
      this.stopRunPolling();
      this.mainRunOperationEpoch += 1;
      this.runPollingContext = null;
      this.running = false;
      this.runningSection = false;
      this.runningModule = false;
      this.stopP52VerifyResultPolling();
      this.cancelAllLongWorkflowTaskPolling("已切换项目，原后台操作不再回写当前页面");
      this.project = {};
      this.ctdCatalogData = [];
      this.browseTreeData = [];
      this.flatCatalogNodes = [];
      this.catalogNodeMap = {};
      this.catalogLeafIdMap = {};
      this.submissions = [];
      this.runs = [];
      this.resultOverview = null;
      this.displayedRunId = "";
      this.runResultLoadError = "";
      this.runResultRequestToken += 1;
      this.sectionTraceMap = {};
      this.sectionPatchMap = {};
      this.sectionFeedbackHistory = null;
      this.sectionRulesDetail = null;
      this.sectionPromptRuleDetail = null;
      this.sectionExampleDetail = null;
      this.sectionExperienceMemoryDetail = null;
      this.sectionExecutionAuditDetail = null;
      this.sectionAblationDetail = null;
      this.sectionAblationCache = {};
      this.sectionRunDetailLoadState = { key: "", promise: null };
      this.sectionStaticDetailLoadState = { key: "", promise: null };
      this.sectionDiagnosticsMap = {};
      this.sectionDiagnosticsRequestTokens = {};
      this.submissionContentMap = {};
      this.submissionContentRequestMap = {};
      this.submissionContentRequestToken += 1;
      this.traceArtifactRequestToken += 1;
      this.saveContentRequestToken += 1;
      this.p52FeedbackPatchesRequestToken += 1;
      this.currentBrowseSectionId = "";
      this.currentBrowseNodeId = "";
      this.activeDocId = "";
      this.contentDisplayDocId = "";
      this.activeFileType = "";
      this.previewUrl = "";
      this.contentSections = [];
      this.contentEditorMode = false;
      this.editorText = "";
      this.savingContent = false;
      this.loadingSubmissionContent = false;
      this.submissionContentError = "";
      this.selectedRun = null;
      this.runStreamCursor = 0;
      this.runStreamStatus = "idle";
      this.runStreamLogs = [];
      this.runStreamScopeSectionId = "";
      this.previewDialog = { visible: false };
      this.reviewResultDialog = { visible: false };
      this.traceArtifactDialog = { visible: false, title: "", content: "", file_path: "" };
      this.loadingTraceArtifact = false;
      this.actingP52PatchId = "";
      this.p52FeedbackPatches = [];
      this.loadingP52FeedbackPatches = false;
      this.p52FeedbackPatchesRequestToken += 1;
      this.resetSectionFeedbackDraft();
      this.syncEditorToCurrentSection();
    },
    async reloadProjectSession(projectId) {
      const token = this.createProjectSessionToken(projectId);
      this.resetProjectScopedState();
      await Promise.all([
        this.loadCatalog(token),
        this.loadProject(token),
        this.loadRuns(token),
      ]);
      if (!this.isProjectSessionTokenActive(token)) {
        return;
      }
      await this.loadSubmissions(token);
      if (!this.isProjectSessionTokenActive(token)) {
        return;
      }
      await this.restoreLatestMainRunTask(token);
    },
    resetSectionFeedbackDraft() {
      this.feedbackForm.decision = "valid";
      this.feedbackForm.feedback_text = "";
      this.feedbackForm.conclusion_feedback = "correct";
      this.feedbackForm.retrieval_feedback = "unknown";
      this.feedbackForm.issue_feedback_map = {};
      this.feedbackForm.evidence_feedback_map = {};
      this.feedbackForm.missing_item_feedback_text = "";
      this.feedbackForm.missing_item_feedback_reason = "";
      this.feedbackForm.reference_example_title = "";
      this.feedbackForm.reference_example_content = "";
      this.optimizeEvaluationForm = {
        overall_verdict: "unknown",
        comment: "",
        patch_feedback_map: {},
      };
      this.feedbackOptimizeResult = {
        feedback_key: "",
        analysis_result: null,
        patch_result: null,
        candidate_patches: [],
        verification_result: null,
        verification_plan: null,
        meta_reflection: null,
        feedback_projection: null,
        reference_example: null,
        optimize_evaluation: null,
        feedback_optimize_status: "",
        candidate_register_status: "",
        replay_status: "",
        error_message: "",
      };
      this.p52FeedbackPatches = [];
    },
    clearSelectedSectionDetailState() {
      this.sectionFeedbackHistory = null;
      this.sectionRulesDetail = null;
      this.sectionPromptRuleDetail = null;
      this.sectionExampleDetail = null;
      this.sectionExperienceMemoryDetail = null;
      this.sectionExecutionAuditDetail = null;
      this.sectionRunDetailLoadState = { key: "", promise: null };
      this.sectionStaticDetailLoadState = { key: "", promise: null };
    },
    toggleLeftPaneCollapsed() {
      this.leftPaneCollapsed = !this.leftPaneCollapsed;
    },
    async openReviewResultDialog() {
      if (this.selectedDisplayReview) {
        this.$set(this.reviewResultDialog, "visible", true);
        this.loadSelectedSectionDetailBundle();
        return;
      }
      this.$message.warning("当前章节没有可查看的详细审评结果");
    },
    setBrowseTreeExpanded(expanded) {
      this.$nextTick(() => {
        const tree = this.$refs.browseTree;
        if (!tree || !tree.store || !tree.store.nodesMap) {
          return;
        }
        Object.keys(tree.store.nodesMap).forEach((key) => {
          const node = tree.store.nodesMap[key];
          if (node && node.level > 0) {
            node.expanded = !!expanded;
          }
        });
      });
    },
    syncBrowseTreeCurrent(sectionId = "") {
      this.$nextTick(() => {
        const tree = this.$refs.browseTree;
        if (!tree || typeof tree.setCurrentKey !== "function") {
          return;
        }
        tree.setCurrentKey(String(sectionId || this.currentBrowseSectionId || "").trim());
      });
    },
    emptySectionFeedbackHistory() {
      return {
        feedback_entries: [],
        optimize_events: [],
        patch_entries: [],
        signal_entries: [],
        summary: {},
      };
    },
    hydrateFeedbackOptimizeResultFromHistory(history) {
      const events = Array.isArray(history && history.optimize_events) ? history.optimize_events : [];
      if (!events.length) {
        this.feedbackOptimizeResult = {
          ...(this.feedbackOptimizeResult || {}),
          feedback_key: "",
          analysis_result: null,
          patch_result: null,
          candidate_patches: [],
          verification_result: null,
          verification_plan: null,
          meta_reflection: null,
          feedback_projection: null,
          reference_example: null,
          optimize_evaluation: null,
          feedback_optimize_status: "",
          candidate_register_status: "",
          replay_status: "",
          error_message: "",
        };
        return;
      }
      const latest = events[events.length - 1] || {};
      const patchResult = latest && typeof latest.patch_result === "object" && latest.patch_result
        ? latest.patch_result
        : null;
      const candidatePatches = Array.isArray(latest && latest.candidate_patches)
        ? latest.candidate_patches
        : (patchResult && Array.isArray(patchResult.patches) ? patchResult.patches : []);
      const verificationResult = latest && typeof latest.verification_result === "object" && latest.verification_result
        ? latest.verification_result
        : null;
      this.feedbackOptimizeResult = {
        ...(this.feedbackOptimizeResult || {}),
        feedback_key: String((latest && latest.feedback_key) || "").trim(),
        analysis_result: latest && typeof latest.analysis_result === "object" ? latest.analysis_result : null,
        patch_result: patchResult,
        candidate_patches: candidatePatches,
        verification_result: verificationResult,
        verification_plan: verificationResult,
        meta_reflection: latest && typeof latest.meta_reflection === "object" ? latest.meta_reflection : null,
        feedback_projection: latest && typeof latest.feedback_projection === "object" ? latest.feedback_projection : null,
        reference_example: latest && typeof latest.reference_example === "object" ? latest.reference_example : null,
        optimize_evaluation: latest && typeof latest.optimize_evaluation === "object" ? latest.optimize_evaluation : null,
        feedback_optimize_status: String((latest && latest.feedback_optimize_status) || "").trim(),
        candidate_register_status: String((latest && latest.candidate_register_status) || "").trim(),
        replay_status: String((latest && latest.replay_status) || "").trim(),
        error_message: String((latest && latest.error_message) || "").trim(),
      };
    },
    emptySectionRulesDetail() {
      return {
        items: [],
        source_breakdown: {},
        summary: {},
      };
    },
    emptySectionPromptRuleDetail() {
      return {
        items: [],
        task_breakdown: {},
        source_breakdown: {},
        summary: {},
      };
    },
    emptySectionExampleDetail() {
      return {
        items: [],
        type_breakdown: {},
        summary: {},
      };
    },
    emptySectionExperienceMemoryDetail() {
      return {
        items: [],
        scope_breakdown: {},
        type_breakdown: {},
        summary: {},
      };
    },
    emptySectionExecutionAuditDetail() {
      return {
        items: [],
        stage_breakdown: {},
        status_breakdown: {},
        envelope_breakdown: {},
        protocol_breakdown: {},
        summary: {},
      };
    },
    emptySectionAblationDetail() {
      return {
        case_ids: [],
        variants: [],
        ranking: [],
        summary: {},
        generated_case_count: 0,
        source_run_id: "",
        source_section_id: "",
      };
    },
    currentSectionAblationCacheKey() {
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!runId || !sectionId) {
        return "";
      }
      return `${runId}|${sectionId}`;
    },
    syncSectionAblationFromCache() {
      const key = this.currentSectionAblationCacheKey();
      this.sectionAblationDetail = key && this.sectionAblationCache[key]
        ? this.sectionAblationCache[key]
        : null;
    },
    isP52SectionId(sectionId) {
      const value = normalizeSectionId(sectionId);
      return value === "3.2.p.5.2" || value.startsWith("3.2.p.5.2.");
    },
    currentSectionRunDetailRequestKey() {
      return [
        String((this.selectedRun && this.selectedRun.run_id) || "").trim(),
        String(this.currentBrowseSectionId || "").trim(),
      ].join("|");
    },
    currentSectionStaticDetailRequestKey() {
      return [
        String(this.projectId || "").trim(),
        String(this.currentBrowseSectionId || "").trim(),
      ].join("|");
    },
    createSectionDetailRequestContext(includeRun = false) {
      return {
        projectToken: Number(this.sessionProjectToken || 0),
        projectId: String(this.projectId || "").trim(),
        sectionId: String(this.currentBrowseSectionId || "").trim(),
        runId: includeRun ? String((this.selectedRun && this.selectedRun.run_id) || "").trim() : "",
      };
    },
    isSectionDetailRequestContextActive(context, includeRun = false) {
      if (!context || !this.isProjectSessionTokenActive(context.projectToken)) {
        return false;
      }
      if (String(this.projectId || "").trim() !== context.projectId
        || String(this.currentBrowseSectionId || "").trim() !== context.sectionId) {
        return false;
      }
      return !includeRun
        || String((this.selectedRun && this.selectedRun.run_id) || "").trim() === context.runId;
    },
    async loadSelectedSectionDetailBundle(force = false) {
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!sectionId) {
        this.sectionFeedbackHistory = null;
        this.sectionRulesDetail = null;
        this.sectionPromptRuleDetail = null;
        this.sectionExampleDetail = null;
        this.sectionExperienceMemoryDetail = null;
        this.sectionExecutionAuditDetail = null;
        this.sectionAblationDetail = null;
        this.sectionRunDetailLoadState = { key: "", promise: null };
        this.sectionStaticDetailLoadState = { key: "", promise: null };
        return;
      }
      const runPromise = this.loadSelectedSectionRunDetailBundle(force);
      const staticPromise = this.loadSelectedSectionStaticDetailBundle(false);
      return Promise.all([runPromise, staticPromise]);
    },
    async loadSelectedSectionRunDetailBundle(force = false) {
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      const requestKey = this.currentSectionRunDetailRequestKey();
      if (!sectionId || !this.selectedRun || !this.selectedRun.run_id) {
        this.sectionFeedbackHistory = null;
        this.sectionExecutionAuditDetail = null;
        this.sectionRunDetailLoadState = { key: "", promise: null };
        return;
      }
      if (!force && this.sectionRunDetailLoadState.key === requestKey) {
        if (this.sectionRunDetailLoadState.promise) {
          return this.sectionRunDetailLoadState.promise;
        }
        return;
      }
      const promise = Promise.all([
        this.loadSelectedSectionTrace(force),
        this.loadSelectedSectionFeedbackHistory(),
        this.loadSelectedSectionExecutionAudits(),
        this.loadSelectedP52FeedbackPatches(),
      ]).finally(() => {
        if (this.sectionRunDetailLoadState.key === requestKey) {
          this.sectionRunDetailLoadState.promise = null;
        }
      });
      this.sectionRunDetailLoadState = {
        key: requestKey,
        promise,
      };
      return promise;
    },
    async loadSelectedSectionStaticDetailBundle(force = false) {
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      const requestKey = this.currentSectionStaticDetailRequestKey();
      if (!sectionId) {
        this.sectionRulesDetail = null;
        this.sectionPromptRuleDetail = null;
        this.sectionExampleDetail = null;
        this.sectionExperienceMemoryDetail = null;
        this.sectionStaticDetailLoadState = { key: "", promise: null };
        return;
      }
      if (!force && this.sectionStaticDetailLoadState.key === requestKey) {
        if (this.sectionStaticDetailLoadState.promise) {
          return this.sectionStaticDetailLoadState.promise;
        }
        return;
      }
      const loaders = [
        this.loadSelectedSectionRules(),
        this.loadSelectedSectionPromptRules(),
        this.loadSelectedSectionExperienceMemory(),
      ];
      if (!this.selectedRun || !this.selectedRun.run_id) {
        loaders.push(this.loadSelectedSectionExamples());
      }
      const promise = Promise.all(loaders).finally(() => {
        if (this.sectionStaticDetailLoadState.key === requestKey) {
          this.sectionStaticDetailLoadState.promise = null;
        }
      });
      this.sectionStaticDetailLoadState = {
        key: requestKey,
        promise,
      };
      return promise;
    },
    reviewConclusionLabel(value) {
      const mapping = {
        supported: "满足",
        partially_supported: "暂不满足（需补充）",
        unsupported: "不满足",
        insufficient_information: "信息不足",
        need_more_information: "需补充信息",
        risk: "存在风险",
      };
      return mapping[String(value || "").trim()] || value || "-";
    },
    metricPercent(value) {
      const num = Number(value);
      return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : "-";
    },
    metricNumber(value) {
      const num = Number(value);
      return Number.isFinite(num) ? `${num}` : "-";
    },
    groupEvidenceBySource(materials) {
      const buckets = {};
      const seen = new Set();
      (Array.isArray(materials) ? materials : []).forEach((item) => {
        const score = Number(item && item.score);
        if (!Number.isFinite(score) || score < 0.5) {
          return;
        }
        const evidenceId = String((item && (item.evidence_id || `${item.doc_id || ""}:${item.chunk_id || ""}`)) || "").trim();
        if (evidenceId && seen.has(evidenceId)) {
          return;
        }
        if (evidenceId) {
          seen.add(evidenceId);
        }
        const source = String(item.source_type || item.source_domain || item.category || item.classification || "其他").trim() || "其他";
        if (!buckets[source]) {
          buckets[source] = [];
        }
        buckets[source].push(item);
      });
      return Object.keys(buckets).map((key) => ({ source: key, items: buckets[key] }));
    },
    clearRunStreamLogs() {
      this.runStreamLogs = [];
      this.runStreamCursor = 0;
      this.runStreamScopeSectionId = "";
      this.expandedLogSections = ["__run__"];
      if (!this.running && !this.runningSection && !this.runningModule) {
        this.runStreamStatus = "idle";
      }
    },
    longWorkflowCancellation(message = "后台操作已取消轮询") {
      const error = new Error(message);
      error.isLongWorkflowCancelled = true;
      return error;
    },
    isLongWorkflowCancellation(error) {
      return !!(error && error.isLongWorkflowCancelled);
    },
    longWorkflowErrorMessage(error, fallback = "后台操作失败") {
      const raw = String(
        (error && error.userMessage)
        || (error && error.response && error.response.data && error.response.data.message)
        || (error && error.message)
        || "",
      ).trim();
      if (/task not found/i.test(raw)) return "后台任务不存在或已过期，请重新发起";
      if (/run not found/i.test(raw)) return "审评运行记录不存在或已删除";
      return raw || fallback;
    },
    createLongWorkflowContext(operation) {
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      const projectId = String(this.projectId || "").trim();
      const normalizedOperation = String(operation || "long_operation").trim() || "long_operation";
      return {
        operation: normalizedOperation,
        projectId,
        runId,
        sectionId,
        projectToken: Number(this.sessionProjectToken || 0),
        key: `${projectId}|${runId}|${sectionId}|${normalizedOperation}`,
      };
    },
    setLongWorkflowOperationIdle(operation) {
      const name = String(operation || "");
      if (name === "feedback_optimize") this.submittingFeedback = false;
      if (name === "p52_feedback_optimize") {
        this.submittingFeedback = false;
        this.submittingP52FeedbackOptimize = false;
      }
      if (name === "feedback_replay_optimize") this.replayingFeedbackOptimize = false;
      if (name === "p52_feedback_verify") this.replayingP52FeedbackVerify = false;
      if (["feedback_meta_reflection", "p52_meta_reflection"].includes(name)) this.replayingMetaReflection = false;
      if (name === "feedback_evaluation_ablation") this.runningAblation = false;
    },
    cancelLongWorkflowTaskPolling(key, message = "后台操作已被新操作取代") {
      const taskKey = String(key || "");
      const poller = (this.longWorkflowTaskPollers || {})[taskKey];
      if (!poller) return;
      if (poller.timer) clearTimeout(poller.timer);
      this.$delete(this.longWorkflowTaskPollers, taskKey);
      if (typeof poller.reject === "function") {
        const reject = poller.reject;
        poller.reject = null;
        reject(this.longWorkflowCancellation(message));
      }
    },
    cancelAllLongWorkflowTaskPolling(message = "后台操作已取消轮询") {
      const keys = new Set([
        ...Object.keys(this.longWorkflowTaskPollers || {}),
        ...Object.keys(this.longWorkflowTaskEpochs || {}),
      ]);
      keys.forEach((key) => {
        const state = (this.longWorkflowTaskStates || {})[key] || {};
        this.cancelLongWorkflowTaskPolling(key, message);
        this.$set(this.longWorkflowTaskEpochs, key, Number(this.longWorkflowTaskEpochs[key] || 0) + 1);
        this.setLongWorkflowOperationIdle(state.operation);
      });
    },
    beginLongWorkflowOperation(operation) {
      const context = this.createLongWorkflowContext(operation);
      this.cancelLongWorkflowTaskPolling(context.key);
      const epoch = Number(this.longWorkflowTaskEpochs[context.key] || 0) + 1;
      this.$set(this.longWorkflowTaskEpochs, context.key, epoch);
      const activeContext = { ...context, epoch };
      this.$set(this.longWorkflowTaskStates, context.key, {
        ...activeContext,
        task_id: "",
        status: "starting",
        message: "正在创建后台任务",
      });
      return activeContext;
    },
    isLongWorkflowContextCurrent(context) {
      if (!context || typeof context !== "object") return false;
      return (
        Number(this.longWorkflowTaskEpochs[context.key] || 0) === Number(context.epoch || 0)
        && Number(this.sessionProjectToken || 0) === Number(context.projectToken || 0)
        && String(this.projectId || "").trim() === String(context.projectId || "")
        && String((this.selectedRun && this.selectedRun.run_id) || "").trim() === String(context.runId || "")
        && String(this.currentBrowseSectionId || "").trim() === String(context.sectionId || "")
      );
    },
    markLongWorkflowFailed(context, error, fallback) {
      if (!this.isLongWorkflowContextCurrent(context)) return;
      this.$set(this.longWorkflowTaskStates, context.key, {
        ...(this.longWorkflowTaskStates[context.key] || {}),
        status: "failed",
        message: this.longWorkflowErrorMessage(error, fallback),
      });
    },
    waitForLongWorkflowTask(taskId, context) {
      const normalizedTaskId = String(taskId || "").trim();
      if (!normalizedTaskId) return Promise.reject(new Error("后台操作未返回 task_id"));
      if (!this.isLongWorkflowContextCurrent(context)) {
        return Promise.reject(this.longWorkflowCancellation());
      }
      this.$set(this.longWorkflowTaskStates, context.key, {
        ...(this.longWorkflowTaskStates[context.key] || {}),
        task_id: normalizedTaskId,
        status: "pending",
        message: "后台任务已创建，等待执行",
      });
      return new Promise((resolve, reject) => {
        const control = {
          taskId: normalizedTaskId,
          context,
          timer: null,
          reject,
          cursor: 0,
          consecutiveErrors: 0,
        };
        this.$set(this.longWorkflowTaskPollers, context.key, control);
        const isCurrent = () => (
          this.longWorkflowTaskPollers[context.key] === control
          && this.isLongWorkflowContextCurrent(context)
        );
        const release = () => {
          if (control.timer) clearTimeout(control.timer);
          control.timer = null;
          control.reject = null;
          if (this.longWorkflowTaskPollers[context.key] === control) {
            this.$delete(this.longWorkflowTaskPollers, context.key);
          }
        };
        const fail = (error) => {
          release();
          reject(error);
        };
        const scheduleNext = () => {
          if (!isCurrent()) return;
          control.timer = setTimeout(() => {
            control.timer = null;
            pollOnce();
          }, LONG_WORKFLOW_POLL_INTERVAL_MS);
        };
        const pollOnce = async () => {
          if (!isCurrent()) {
            fail(this.longWorkflowCancellation());
            return;
          }
          try {
            const response = await getPreReviewTaskProgress(
              normalizedTaskId,
              { cursor: control.cursor },
              { silentError: true },
            );
            if (!isCurrent()) {
              fail(this.longWorkflowCancellation());
              return;
            }
            const snapshot = (response && response.data) || {};
            if (snapshot.task_id && String(snapshot.task_id) !== normalizedTaskId) {
              throw new Error("后台任务返回了不匹配的 task_id");
            }
            if (snapshot.project_id && String(snapshot.project_id) !== context.projectId) {
              throw new Error("后台任务所属项目与当前页面不一致");
            }
            if (snapshot.run_id && String(snapshot.run_id) !== context.runId) {
              throw new Error("后台任务所属运行与当前结果不一致");
            }
            if (snapshot.section_id && String(snapshot.section_id) !== context.sectionId) {
              throw new Error("后台任务所属章节与当前章节不一致");
            }
            control.consecutiveErrors = 0;
            control.cursor = Number(snapshot.next_cursor || snapshot.cursor || control.cursor || 0);
            const status = String(snapshot.status || "pending").toLowerCase();
            this.$set(this.longWorkflowTaskStates, context.key, {
              ...(this.longWorkflowTaskStates[context.key] || {}),
              ...snapshot,
              operation: context.operation,
              status,
            });
            if (status === "completed") {
              release();
              const result = snapshot.result && typeof snapshot.result === "object" ? snapshot.result : {};
              resolve({ message: result.message || "", data: result.data, task: snapshot, raw_result: result });
              return;
            }
            if (["failed", "cancelled", "interrupted"].includes(status)) {
              const result = snapshot.result && typeof snapshot.result === "object" ? snapshot.result : {};
              const error = new Error(snapshot.error_message || result.message || "后台操作执行失败");
              error.userMessage = error.message;
              fail(error);
              return;
            }
            scheduleNext();
          } catch (error) {
            if (!isCurrent()) {
              fail(this.longWorkflowCancellation());
              return;
            }
            control.consecutiveErrors += 1;
            if (control.consecutiveErrors < 5) {
              this.$set(this.longWorkflowTaskStates, context.key, {
                ...(this.longWorkflowTaskStates[context.key] || {}),
                message: `任务进度查询暂时失败，正在重试（${control.consecutiveErrors}/5）`,
              });
              scheduleNext();
              return;
            }
            const finalError = new Error("后台任务进度查询失败，任务可能仍在执行，请稍后刷新");
            finalError.userMessage = this.longWorkflowErrorMessage(error, finalError.message);
            fail(finalError);
          }
        };
        pollOnce();
      });
    },
    async executeLongWorkflowTask(context, startRequest) {
      try {
        const startResponse = await startRequest();
        if (!this.isLongWorkflowContextCurrent(context)) {
          throw this.longWorkflowCancellation();
        }
        const taskId = String((((startResponse || {}).data || {}).task_id) || "").trim();
        return await this.waitForLongWorkflowTask(taskId, context);
      } catch (error) {
        if (!this.isLongWorkflowCancellation(error)) {
          this.markLongWorkflowFailed(context, error, "后台操作失败");
        }
        throw error;
      }
    },
    stopRunPolling(message = "当前任务轮询已取消") {
      if (this.runPollingTimer) {
        clearTimeout(this.runPollingTimer);
        this.runPollingTimer = null;
      }
      const cancel = this.runPollingCancel;
      this.runPollingCancel = null;
      this.runPollingTaskId = "";
      this.runPollingContext = null;
      if (typeof cancel === "function") {
        cancel(message);
      }
    },
    isRunPollingCancellation(error) {
      return !!(error && error.isRunPollingCancelled);
    },
    mainRunCancellation(message = "当前审评任务所属页面已切换") {
      const error = new Error(message);
      error.isRunPollingCancelled = true;
      return error;
    },
    beginMainRunOperation(taskType, { sectionId = "", docId = "" } = {}) {
      this.stopRunPolling("已启动新的审评任务");
      this.mainRunOperationEpoch += 1;
      const context = {
        epoch: Number(this.mainRunOperationEpoch || 0),
        projectToken: Number(this.sessionProjectToken || 0),
        projectId: String(this.projectId || "").trim(),
        taskType: String(taskType || "run").trim() || "run",
        sectionId: String(sectionId || "").trim(),
        docId: String(docId || this.activeDocId || "").trim(),
      };
      this.runPollingContext = context;
      return context;
    },
    isMainRunContextCurrent(context) {
      if (!context || typeof context !== "object") return false;
      return (
        Number(this.mainRunOperationEpoch || 0) === Number(context.epoch || 0)
        && Number(this.sessionProjectToken || 0) === Number(context.projectToken || 0)
        && String(this.projectId || "").trim() === String(context.projectId || "")
      );
    },
    assertMainRunTaskSnapshot(snapshot, taskId, context) {
      const data = snapshot && typeof snapshot === "object" ? snapshot : {};
      if (data.task_id && String(data.task_id) !== String(taskId || "")) {
        throw new Error("审评任务返回了不匹配的 task_id");
      }
      if (data.project_id && String(data.project_id) !== String(context.projectId || "")) {
        throw new Error("审评任务所属项目与当前页面不一致");
      }
      if (data.task_type && String(data.task_type) !== String(context.taskType || "")) {
        throw new Error("审评任务类型与当前操作不一致");
      }
      if (data.source_doc_id && context.docId && String(data.source_doc_id) !== String(context.docId)) {
        throw new Error("审评任务所属文件与当前操作不一致");
      }
      if (data.section_id && context.sectionId && String(data.section_id) !== String(context.sectionId)) {
        throw new Error("审评任务所属章节与当前操作不一致");
      }
    },
    setMainRunOperationBusy(taskType, busy) {
      const value = !!busy;
      const type = String(taskType || "run");
      if (type === "run") this.running = value;
      if (type === "section_replay") this.runningSection = value;
      if (type === "module_replay") this.runningModule = value;
    },
    createRestoredMainRunContext(task) {
      const metadata = task && typeof task === "object" ? task : {};
      return {
        epoch: Number(this.mainRunOperationEpoch || 0),
        projectToken: Number(this.sessionProjectToken || 0),
        projectId: String(this.projectId || "").trim(),
        taskType: String(metadata.task_type || "run").trim(),
        sectionId: String(metadata.section_id || "").trim(),
        docId: String(metadata.source_doc_id || "").trim(),
      };
    },
    async restoreLatestMainRunTask(projectToken = this.sessionProjectToken) {
      const projectId = String(this.projectId || "").trim();
      if (!projectId || !this.isProjectSessionTokenActive(projectToken)) return;
      const requestEpoch = Number(this.mainRunOperationEpoch || 0);
      let response = null;
      try {
        response = await getLatestPreReviewMainTask(
          projectId,
          { includeTerminal: true },
          { silentError: true },
        );
      } catch (_) {
        return;
      }
      if (
        !this.isProjectSessionTokenActive(projectToken)
        || String(this.projectId || "").trim() !== projectId
        || Number(this.mainRunOperationEpoch || 0) !== requestEpoch
      ) {
        return;
      }
      const task = (response && response.data) || null;
      if (!task || typeof task !== "object") return;
      const allowedTypes = new Set(["run", "section_replay", "module_replay"]);
      const taskType = String(task.task_type || "").trim();
      const taskId = String(task.task_id || "").trim();
      const taskProjectId = String(task.project_id || "").trim();
      const status = String(task.status || "").trim().toLowerCase();
      if (!taskId || !allowedTypes.has(taskType) || taskProjectId !== projectId) return;

      if (["pending", "running"].includes(status)) {
        const sectionId = String(task.section_id || "").trim();
        this.resetRunStream(sectionId);
        const context = this.beginMainRunOperation(taskType, {
          sectionId,
          docId: String(task.source_doc_id || "").trim(),
        });
        this.runPollingTaskId = taskId;
        this.runPollingContext = context;
        this.runStreamStatus = status === "running" ? "running" : "connecting";
        this.setMainRunOperationBusy(taskType, true);
        this.continueRestoredMainRunTask(taskId, context);
        return;
      }

      if (["completed", "failed"].includes(status)) {
        const context = this.createRestoredMainRunContext(task);
        this.restoreTerminalMainRunTask(taskId, status, context);
      }
    },
    async continueRestoredMainRunTask(taskId, context) {
      try {
        const result = await this.waitForRunTask(taskId, context);
        if (!this.isMainRunContextCurrent(context)) return;
        await this.loadRuns(context.projectToken);
        if (!this.isMainRunContextCurrent(context)) return;
        const runId = String((((result || {}).data || {}).run_id) || "").trim();
        const matched = runId
          ? this.runs.find((item) => String((item && item.run_id) || "").trim() === runId)
          : null;
        if (matched) await this.viewRun(matched, context.projectToken);
        if (this.isMainRunContextCurrent(context)) {
          this.$message.success((result && result.message) || "已恢复的审评任务执行完成");
        }
      } catch (error) {
        if (this.isRunPollingCancellation(error) || !this.isMainRunContextCurrent(context)) return;
        this.$message.error((error && error.userMessage) || (error && error.message) || "已恢复的审评任务执行失败");
      } finally {
        if (this.isMainRunContextCurrent(context)) {
          this.setMainRunOperationBusy(context.taskType, false);
        }
      }
    },
    async restoreTerminalMainRunTask(taskId, terminalStatus, context) {
      try {
        const response = await getPreReviewTaskProgress(
          taskId,
          { cursor: 0 },
          { silentError: true },
        );
        if (!this.isMainRunContextCurrent(context)) return;
        const snapshot = (response && response.data) || {};
        this.assertMainRunTaskSnapshot(snapshot, taskId, context);
        if (!this.isMainRunContextCurrent(context)) return;
        this.runStreamLogs = [];
        this.runStreamCursor = Number(snapshot.next_cursor || 0);
        this.runStreamScopeSectionId = String(context.sectionId || "");
        this.expandedLogSections = [this.runStreamScopeSectionId || "__run__"];
        (Array.isArray(snapshot.logs) ? snapshot.logs : []).forEach((item) => {
          this.appendRunStreamLog(item.stage === "run_failed" ? "error" : "progress", item);
        });
        const status = String(snapshot.status || terminalStatus || "").trim().toLowerCase();
        this.runStreamStatus = status === "completed" ? "completed" : "failed";
      } catch (_) {
        if (!this.isMainRunContextCurrent(context)) return;
        this.runStreamStatus = terminalStatus === "completed" ? "completed" : "failed";
      }
    },
    async showFailedTaskRun(snapshot, context) {
      const payload = ((snapshot || {}).result || {}).data || {};
      const runId = String(payload.run_id || "").trim();
      if (!runId || !this.isMainRunContextCurrent(context)) return;
      try {
        const response = await runHistory({ project_id: context.projectId }, { silentError: true });
        if (!this.isMainRunContextCurrent(context)) return;
        const data = response && response.data;
        const rows = Array.isArray(data) ? data : (data && Array.isArray(data.list) ? data.list : []);
        const row = rows.find((item) => String(item.run_id || "") === runId);
        if (!row) {
          this.runResultLoadError = "本次任务失败，未取得对应轮次；当前显示内容不代表本次结果，请重新加载。";
          return;
        }
        this.runs = rows;
        await this.viewRun(row, context.projectToken);
      } catch (_) {
        if (this.isMainRunContextCurrent(context)) {
          this.runResultLoadError = "本次任务失败，部分结果加载失败；已保留原显示内容，请重新加载。";
        }
      }
    },
    resetRunStream(sectionId = "") {
      this.stopRunPolling();
      this.runStreamLogs = [];
      this.runStreamCursor = 0;
      this.runStreamScopeSectionId = String(sectionId || "").trim();
      this.expandedLogSections = [this.runStreamScopeSectionId || "__run__"];
      this.runStreamStatus = "connecting";
    },
    async loadSubmissionSectionDiagnostics(docId, force = false) {
      const normalizedDocId = String(docId || "").trim();
      if (!normalizedDocId) {
        return null;
      }
      if (!force && this.sectionDiagnosticsMap[normalizedDocId]) {
        return this.sectionDiagnosticsMap[normalizedDocId];
      }
      const projectToken = this.sessionProjectToken;
      const projectId = String(this.projectId || "").trim();
      const requestToken = Number(this.sectionDiagnosticsRequestTokens[normalizedDocId] || 0) + 1;
      this.$set(this.sectionDiagnosticsRequestTokens, normalizedDocId, requestToken);
      const isCurrentRequest = () => (
        this.isProjectSessionTokenActive(projectToken)
        && String(this.projectId || "").trim() === projectId
        && Number(this.sectionDiagnosticsRequestTokens[normalizedDocId] || 0) === requestToken
      );
      this.loadingSectionDiagnostics = true;
      try {
        const res = await submissionSectionDiagnostics(projectId, normalizedDocId, { silentError: true });
        if (!isCurrentRequest()) {
          return null;
        }
        const data = (res && res.data) || null;
        this.$set(this.sectionDiagnosticsMap, normalizedDocId, data);
        return data;
      } catch (_) {
        if (!isCurrentRequest()) {
          return null;
        }
        return this.sectionDiagnosticsMap[normalizedDocId] || null;
      } finally {
        if (isCurrentRequest() && String(this.activeDocId || "").trim() === normalizedDocId) {
          this.loadingSectionDiagnostics = false;
        }
      }
    },
    async refreshCurrentDocDiagnostics() {
      if (!this.activeDocId) {
        return;
      }
      await this.loadSubmissionSectionDiagnostics(this.activeDocId, true);
    },
    async resolveReplayDocId(sectionId) {
      const sid = String(sectionId || "").trim();
      const ordered = [];
      const seen = new Set();
      [...(this.currentBrowseFiles || []), ...(this.submissions || [])].forEach((item) => {
        const docId = String((item && item.doc_id) || "").trim();
        if (!docId || seen.has(docId)) {
          return;
        }
        seen.add(docId);
        ordered.push(item);
      });
      for (const item of ordered) {
        const docId = String((item && item.doc_id) || "").trim();
        if (!docId) {
          continue;
        }
        try {
          const data = await this.loadSubmissionSectionDiagnostics(docId);
          const diagnostics = Array.isArray(data && data.section_diagnostics) ? data.section_diagnostics : [];
          const matched = diagnostics.some((section) => {
            const sectionKey = String((section && section.section_id) || "").trim();
            return sectionKey === sid && !!section.can_replay;
          });
          if (matched) {
            return docId;
          }
        } catch (_) {}
      }
      return ordered.length ? String((ordered[0] && ordered[0].doc_id) || "").trim() : String(this.activeDocId || "").trim();
    },
    appendRunStreamLog(type, payload) {
      const data = payload && typeof payload === "object" ? payload : { message: String(payload || "") };
      const text = this.formatRunStreamMessage(type, data);
      if (!text) {
        return;
      }
      const now = new Date();
      const time = [now.getHours(), now.getMinutes(), now.getSeconds()].map((item) => String(item).padStart(2, "0")).join(":");
      this.runStreamLogs.push({
        id: `${Date.now()}_${this.runStreamLogs.length + 1}`,
        type,
        stage: data.stage || type,
        sectionKey: data.section_id ? String(data.section_id) : (data.section_code ? String(data.section_code) : "__run__"),
        sectionLabel: data.section_id
          ? buildSectionDisplayLabel(data.section_id, data.section_name)
          : (data.section_code ? buildSectionDisplayLabel(data.section_code, data.section_name) : "运行级事件"),
        time,
        text,
      });
      const sectionKey = data.section_id ? String(data.section_id) : (data.section_code ? String(data.section_code) : "__run__");
      if (!this.expandedLogSections.includes(sectionKey)) {
        this.expandedLogSections.push(sectionKey);
      }
      if (this.runStreamLogs.length > 300) {
        this.runStreamLogs.splice(0, this.runStreamLogs.length - 300);
      }
    },
    formatRunStreamMessage(type, payload) {
      const stage = String((payload && payload.stage) || "").trim();
      const message = String((payload && payload.message) || "").trim();
      const sectionLabel = buildSectionDisplayLabel(payload.section_id, payload.section_name);
      const prefix = sectionLabel ? `[${sectionLabel}] ` : "";
      if (type === "error" && message) {
        return message;
      }
      switch (stage) {
        case "task_created":
          return "轮询任务已创建";
        case "task_start":
          return "辅助审评任务已启动";
        case "run_start":
        case "module_start":
        case "chunks_loaded":
        case "run_created":
        case "section_queue":
        case "planner_start":
        case "planner_done":
        case "retrieval_start":
        case "retrieval_done":
        case "reviewer_start":
        case "reviewer_done":
        case "module_done":
        case "run_done":
        case "run_failed":
          return `${prefix}${message}`;
        case "section_start":
          return `${prefix}${message || "开始处理章节"}`;
        case "section_done":
          return `${prefix}${message}${payload.conclusion ? ` 结论: ${payload.conclusion}` : ""}`;
        case "section_skipped":
          return `${prefix}${message || "章节已跳过"}`;
        case "retrieval_source_start":
          return `${prefix}开始检索 ${payload.source_type || "资料来源"}`;
        case "retrieval_query":
          return `${prefix}正在检索 ${payload.source_type || "资料来源"}: ${payload.query || message}`;
        case "retrieval_source_done":
          return `${prefix}${payload.source_type || "资料来源"} 检索完成，命中 ${payload.hit_count ?? 0} 条${Array.isArray(payload.hit_titles) && payload.hit_titles.length ? `，标题: ${payload.hit_titles.join("；")}` : ""}`;
        default:
          return `${prefix}${message || stage || type || "执行中"}`;
      }
    },
    submissionCoversSection(item, sid) {
      const currentId = normalizeSectionId(item && item.section_id);
      const targetId = normalizeSectionId(sid);
      const sectionPath = Array.isArray(item && item.section_path)
        ? item.section_path.map((x) => normalizeSectionId(x)).filter(Boolean)
        : [];
      if (!targetId) {
        return true;
      }
      if (!currentId && !sectionPath.length) {
        return false;
      }
      if (currentId === targetId || currentId.startsWith(`${targetId}.`) || targetId.startsWith(`${currentId}.`)) {
        return true;
      }
      return sectionPath.some((x) => x === targetId || x.startsWith(`${targetId}.`) || targetId.startsWith(`${x}.`));
    },
    compareSubmissionCoverage(a, b, sid) {
      return this.rankSubmissionCoverage(a, sid) - this.rankSubmissionCoverage(b, sid);
    },
    rankSubmissionCoverage(item, sid) {
      const currentId = normalizeSectionId(item && item.section_id);
      const targetId = normalizeSectionId(sid);
      if (!targetId) {
        return 99;
      }
      if (currentId === targetId) {
        return 0;
      }
      if (currentId && targetId.startsWith(`${currentId}.`)) {
        return 1;
      }
      if (currentId && currentId.startsWith(`${targetId}.`)) {
        return 2;
      }
      return 99;
    },
    buildRunPayload() {
      const sectionFocusOverrides = {};
      const walk = (nodes) => {
        (nodes || []).forEach((node) => {
          if (!node || typeof node !== "object") return;
          const sid = String(node.section_id || "").trim();
          const points = normalizeStringList(node.concern_points);
          const children = Array.isArray(node.children_sections) ? node.children_sections : [];
          if (sid && !children.length && points.length) {
            sectionFocusOverrides[sid] = points;
          }
          walk(children);
        });
      };
      walk(this.leftTreeData);
      return {
        project_id: this.projectId,
        source_doc_id: this.activeDocId,
        run_config: {
          domain: normalizeReviewDomain(this.project.registration_scope || this.project.review_domain || ""),
          strategy: "single_section_pre_review_v2",
          workflow_mode: "single_section_pre_review_v2",
          feedback_loop_mode: this.experimentConfig.enableFeedbackOptimize ? "feedback_optimize" : "feedback_only",
          enable_feedback_optimize: !!this.experimentConfig.enableFeedbackOptimize,
          section_focus_overrides: sectionFocusOverrides,
        },
      };
    },
    async fetchRunTaskProgress(taskId, context = this.runPollingContext) {
      const res = await getPreReviewTaskProgress(
        taskId,
        { cursor: this.runStreamCursor },
        { silentError: true },
      );
      const data = (res && res.data) || {};
      if (this.runPollingTaskId !== taskId || !this.isMainRunContextCurrent(context)) {
        return { ...data, stale: true };
      }
      this.assertMainRunTaskSnapshot(data, taskId, context);
      const logs = Array.isArray(data.logs) ? data.logs : [];
      logs.forEach((item) => {
        this.appendRunStreamLog(item.stage === "run_failed" ? "error" : "progress", item);
      });
      this.runStreamCursor = Number(data.next_cursor || this.runStreamCursor || 0);
      return data;
    },
    waitForRunTask(taskId, context = this.runPollingContext) {
      if (!this.isMainRunContextCurrent(context)) {
        return Promise.reject(this.mainRunCancellation());
      }
      this.runPollingContext = context;
      return new Promise((resolve, reject) => {
        let settled = false;
        this.runPollingCancel = (message) => {
          if (settled) return;
          settled = true;
          const error = new Error(message || "当前任务轮询已取消");
          error.isRunPollingCancelled = true;
          reject(error);
        };
        const releasePolling = () => {
          this.runPollingCancel = null;
          this.stopRunPolling();
        };
        const poll = async () => {
          if (settled || this.runPollingTaskId !== taskId || !this.isMainRunContextCurrent(context)) {
            return;
          }
          try {
            const data = await this.fetchRunTaskProgress(taskId, context);
            if (settled || this.runPollingTaskId !== taskId || data.stale || !this.isMainRunContextCurrent(context)) {
              return;
            }
            const status = String(data.status || "").trim();
            if (status === "pending") {
              this.runStreamStatus = "connecting";
            } else if (status === "running") {
              this.runStreamStatus = "running";
            } else if (status === "completed") {
              settled = true;
              this.runStreamStatus = "completed";
              releasePolling();
              resolve((data.result && typeof data.result === "object") ? data.result : data);
              return;
            } else if (status === "failed") {
              settled = true;
              this.runStreamStatus = "failed";
              releasePolling();
              await this.showFailedTaskRun(data, context);
              if (!this.isMainRunContextCurrent(context)) {
                reject(this.mainRunCancellation());
                return;
              }
              reject(new Error(data.error_message || (data.result && data.result.message) || "预审执行失败"));
              return;
            }
          } catch (e) {
            settled = true;
            this.runStreamStatus = "failed";
            releasePolling();
            reject(e);
            return;
          }
          this.runPollingTimer = setTimeout(poll, RUN_PROGRESS_POLL_INTERVAL_MS);
        };
        poll();
      });
    },
    decorateCatalogTree(nodes) {
      return (nodes || []).map((item) => ({
        ...item,
        label: buildSectionDisplayLabel(item.section_id, item.section_name),
        children_sections: this.decorateCatalogTree(item.children_sections || []),
      }));
    },
    flattenCatalogNodes(nodes) {
      const out = [];
      const walk = (items) => {
        (items || []).forEach((item) => {
          if (!item || typeof item !== "object") {
            return;
          }
          out.push(item);
          walk(item.children_sections || []);
        });
      };
      walk(nodes);
      return out;
    },
    rebuildCatalogIndexes(nodes) {
      const flatNodes = this.flattenCatalogNodes(nodes);
      const nodeMap = {};
      flatNodes.forEach((node) => {
        const sid = normalizeSectionId(node && node.section_id);
        if (!sid || nodeMap[sid]) {
          return;
        }
        nodeMap[sid] = node;
      });
      const leafIdMap = {};
      const collect = (node) => {
        if (!node || typeof node !== "object") {
          return [];
        }
        const sid = normalizeSectionId(node.section_id);
        const children = Array.isArray(node.children_sections) ? node.children_sections : [];
        if (!children.length) {
          const leafList = sid ? [sid] : [];
          if (sid) {
            leafIdMap[sid] = leafList;
          }
          return leafList;
        }
        const out = [];
        children.forEach((child) => {
          collect(child).forEach((leafId) => {
            if (leafId && !out.includes(leafId)) {
              out.push(leafId);
            }
          });
        });
        if (sid) {
          leafIdMap[sid] = out;
        }
        return out;
      };
      (nodes || []).forEach((node) => collect(node));
      this.flatCatalogNodes = flatNodes;
      this.catalogNodeMap = nodeMap;
      this.catalogLeafIdMap = leafIdMap;
    },
    buildProjectAlignedContentSections(sections) {
      const parsedSections = Array.isArray(sections) ? sections : [];
      const parsedMap = {};
      parsedSections.forEach((item) => {
        const sid = normalizeSectionId(item && item.section_id);
        if (!sid || parsedMap[sid]) {
          return;
        }
        parsedMap[sid] = item;
      });
      const catalogNodes = Array.isArray(this.flatCatalogNodes) ? this.flatCatalogNodes : [];
      if (!catalogNodes.length) {
        return parsedSections;
      }
      return catalogNodes.map((node) => {
        const sid = normalizeSectionId(node && node.section_id);
        const matched = parsedMap[sid] || {};
        const rawContent = String((matched && matched.raw_content) || (matched && matched.content) || "");
        const cleanedMarkdown = String((matched && matched.cleaned_markdown) || (matched && matched.display_content) || rawContent);
        return {
          ...matched,
          section_id: sid || String((node && node.section_id) || "").trim(),
          section_name: String((matched && matched.section_name) || (node && node.section_name) || sid || "").trim(),
          section_code: String((matched && matched.section_code) || (node && node.section_code) || sid || "").trim(),
          title_path: Array.isArray((matched && matched.title_path))
            ? matched.title_path
            : (Array.isArray(node && node.title_path) ? node.title_path : []),
          parent_section_id: String((matched && matched.parent_section_id) || (node && node.parent_section_id) || "").trim(),
          raw_content: rawContent,
          content: rawContent,
          cleaned_markdown: cleanedMarkdown,
          display_content: cleanedMarkdown,
          content_preview: String((matched && matched.content_preview) || ""),
          children_sections: Array.isArray(node && node.children_sections) ? node.children_sections : [],
        };
      });
    },
    firstTreeNodeId(nodes) {
      const list = Array.isArray(nodes) ? nodes : [];
      if (!list.length) {
        return "";
      }
      const head = list[0];
      const children = Array.isArray(head.children_sections) ? head.children_sections : [];
      if (children.length) {
        return this.firstTreeNodeId(children);
      }
      return head.section_id || "";
    },
    findTreeNode(nodes, sectionId) {
      const targetId = normalizeSectionId(sectionId);
      if (targetId && this.catalogNodeMap[targetId]) {
        return this.catalogNodeMap[targetId];
      }
      for (const item of nodes || []) {
        if (normalizeSectionId(item.section_id) === targetId) {
          return item;
        }
        const child = this.findTreeNode(item.children_sections || [], targetId);
        if (child) {
          return child;
        }
      }
      return null;
    },
    collectLeafSectionIds(node) {
      const sid = normalizeSectionId(node && node.section_id);
      if (sid && this.catalogLeafIdMap[sid]) {
        return this.catalogLeafIdMap[sid];
      }
      if (!node || typeof node !== "object") {
        return [];
      }
      const children = Array.isArray(node.children_sections) ? node.children_sections : [];
      if (!children.length) {
        return node.section_id ? [String(node.section_id).trim()] : [];
      }
      const out = [];
      children.forEach((child) => {
        this.collectLeafSectionIds(child).forEach((sid) => {
          if (sid && !out.includes(sid)) {
            out.push(sid);
          }
        });
      });
      return out;
    },
    onBrowseSectionSelect(node) {
      const selectedId = normalizeSectionId(node && node.section_id);
      this.currentBrowseNodeId = selectedId;
      this.currentBrowseSectionId = selectedId;
      if (this.currentBrowseFiles.length && !this.currentBrowseFiles.some((item) => item.doc_id === this.activeDocId)) {
        this.activeDocId = this.currentBrowseFiles[0].doc_id;
        this.onDocChange(this.activeDocId);
      }
    },
    onReviewedSectionChange(sectionId) {
      const sid = String(sectionId || "").trim();
      if (!sid) {
        return;
      }
      const node = this.findTreeNode(this.leftTreeData, sid);
      if (node) {
        this.onBrowseSectionSelect(node);
        return;
      }
      this.currentBrowseNodeId = sid;
      this.currentBrowseSectionId = sid;
    },
    reviewedSectionModuleLabel(moduleId) {
      const mapping = {
        "1": "模块一",
        "2": "模块二",
        "3": "模块三",
        "4": "模块四",
        "5": "模块五",
        supplement: "药品补充申请",
      };
      return mapping[String(moduleId || "").trim()] || "其他";
    },
    async loadProject(projectToken = this.sessionProjectToken) {
      try {
        const res = await projectDetail(this.projectId);
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        this.project = (res && res.data) || {};
      } catch (_) {
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        this.project = {};
      }
    },
    async loadCatalog(projectToken = this.sessionProjectToken) {
      try {
        const res = await ctdCatalog(this.projectId);
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        const data = (res && res.data) || {};
        this.ctdCatalogData = cloneTree(data.chapter_structure || []);
        this.browseTreeData = this.decorateCatalogTree(this.ctdCatalogData);
        this.rebuildCatalogIndexes(this.browseTreeData);
      } catch (_) {
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        this.ctdCatalogData = [];
        this.browseTreeData = [];
        this.flatCatalogNodes = [];
        this.catalogNodeMap = {};
        this.catalogLeafIdMap = {};
      }
      if (!this.isProjectSessionTokenActive(projectToken)) {
        return;
      }
      if (!this.currentBrowseSectionId) {
        this.currentBrowseSectionId = this.firstTreeNodeId(this.browseTreeData);
      }
      if (!this.currentBrowseNodeId) {
        this.currentBrowseNodeId = this.currentBrowseSectionId;
      }
    },
    async loadSubmissions(projectToken = this.sessionProjectToken) {
      try {
        const res = await listSubmissions(this.projectId, { page: 1, page_size: 500 });
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        this.submissions = ((res && res.data && res.data.list) || []).map((item) => ({ ...item, section_path: Array.isArray(item.section_path) ? item.section_path : [] }));
      } catch (_) {
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        this.submissions = [];
      }
      if (!this.isProjectSessionTokenActive(projectToken)) {
        return;
      }
      this.ensureActiveDoc();
    },
    async loadRuns(projectToken = this.sessionProjectToken) {
      try {
        const res = await runHistory({ project_id: this.projectId });
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        const data = res && res.data;
        this.runs = Array.isArray(data)
          ? data
          : (data && Array.isArray(data.list) ? data.list : []);
        const currentRunId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
        const requestedRunId = currentRunId || String(this.$route.query.run_id || "").trim();
        const matchedRun = requestedRunId
          ? this.runs.find((item) => String((item && item.run_id) || "").trim() === requestedRunId)
          : null;
        if (matchedRun) {
          this.selectedRun = matchedRun;
          if (!this.resultOverview) {
            await this.viewRun(matchedRun, projectToken);
          }
        } else if (this.runs.length) {
          await this.viewRun(this.runs[0], projectToken);
        } else {
          this.selectedRun = null;
          this.resultOverview = null;
        }
      } catch (_) {
        if (!this.isProjectSessionTokenActive(projectToken)) {
          return;
        }
        this.runs = [];
        this.selectedRun = null;
        this.resultOverview = null;
      }
    },
    onContentViewModeChange(value) {
      this.contentViewMode = value;
      if (!this.contentEditorMode) {
        this.syncEditorToCurrentSection();
      }
    },
    onEditorTextChange(value) {
      this.editorText = value;
    },
    startEditingContent() {
      this.contentEditorMode = true;
      this.syncEditorToCurrentSection();
    },
    cancelEditingContent() {
      this.contentEditorMode = false;
      this.syncEditorToCurrentSection();
    },
    ensureActiveDoc() {
      const allSubmissions = Array.isArray(this.submissions) ? this.submissions : [];
      if (!allSubmissions.length) {
        this.submissionContentRequestToken += 1;
        this.activeDocId = "";
        this.contentDisplayDocId = "";
        this.previewUrl = "";
        this.editorText = "";
        this.contentSections = [];
        this.contentEditorMode = false;
        this.loadingSubmissionContent = false;
        this.submissionContentError = "";
        return;
      }
      const candidates = (Array.isArray(this.currentBrowseFiles) && this.currentBrowseFiles.length)
        ? this.currentBrowseFiles
        : allSubmissions;
      if (!candidates.some((item) => item.doc_id === this.activeDocId)) {
        this.activeDocId = candidates[0].doc_id;
      }
      this.onDocChange(this.activeDocId);
    },
    syncActiveDocMeta(docId) {
      const row = this.submissions.find((item) => item.doc_id === docId);
      this.activeFileType = row ? row.file_type : "";
      this.previewUrl = row ? submissionPreviewUrl(this.projectId, docId) : "";
    },
    async onDocChange(docId) {
      const normalizedDocId = String(docId || "").trim();
      if (!normalizedDocId) {
        this.submissionContentRequestToken += 1;
        this.activeDocId = "";
        this.contentDisplayDocId = "";
        this.previewUrl = "";
        this.editorText = "";
        this.contentSections = [];
        this.contentEditorMode = false;
        this.loadingSubmissionContent = false;
        this.submissionContentError = "";
        this.loadingSectionDiagnostics = false;
        return;
      }
      this.activeDocId = normalizedDocId;
      this.contentEditorMode = false;
      this.syncActiveDocMeta(normalizedDocId);
      if (String(this.contentDisplayDocId || "").trim() !== normalizedDocId) {
        this.editorText = "";
      }
      const loaded = await this.loadSubmissionText(normalizedDocId);
      if (loaded && String(this.activeDocId || "").trim() === normalizedDocId) {
        this.loadSubmissionSectionDiagnostics(normalizedDocId).catch(() => null);
      }
    },
    async loadSubmissionText(docId, force = false) {
      const normalizedDocId = String(docId || "").trim();
      if (!normalizedDocId) {
        return false;
      }
      const projectToken = this.sessionProjectToken;
      const requestToken = this.submissionContentRequestToken + 1;
      this.submissionContentRequestToken = requestToken;
      const isCurrentRequest = () => (
        this.isProjectSessionTokenActive(projectToken)
        && this.submissionContentRequestToken === requestToken
        && String(this.activeDocId || "").trim() === normalizedDocId
      );
      this.loadingSubmissionContent = true;
      this.submissionContentError = "";
      try {
        let data = !force ? this.submissionContentMap[normalizedDocId] : null;
        if (!data) {
          let pendingRequest = !force ? this.submissionContentRequestMap[normalizedDocId] : null;
          if (!pendingRequest) {
            pendingRequest = submissionContent(this.projectId, normalizedDocId, {
              compact: true,
              silentError: true,
            }).then((res) => (res && res.data) || {});
            this.$set(this.submissionContentRequestMap, normalizedDocId, pendingRequest);
          }
          try {
            data = await pendingRequest;
          } finally {
            if (this.submissionContentRequestMap[normalizedDocId] === pendingRequest) {
              this.$delete(this.submissionContentRequestMap, normalizedDocId);
            }
          }
          if (isCurrentRequest()) {
            this.$set(this.submissionContentMap, normalizedDocId, data);
          }
        }
        if (!isCurrentRequest()) {
          return false;
        }
        this.contentSections = this.buildProjectAlignedContentSections(data.sections);
        this.contentDisplayDocId = normalizedDocId;
        const currentSectionId = normalizeSectionId(this.currentBrowseSectionId);
        const hasCurrentSection = this.contentSections.some((item) => normalizeSectionId(item && item.section_id) === currentSectionId);
        if (!hasCurrentSection && this.contentSections.length) {
          const firstNonEmpty = this.contentSections.find((item) => String((item && item.content) || "").trim());
          this.currentBrowseSectionId = normalizeSectionId(
            ((firstNonEmpty || this.contentSections[0]) && (firstNonEmpty || this.contentSections[0]).section_id)
            || this.currentBrowseSectionId
            || "",
          );
        }
        this.syncEditorToCurrentSection(data.display_content || data.content || "");
        this.submissionContentError = "";
        return true;
      } catch (error) {
        if (!isCurrentRequest()) {
          return false;
        }
        const hasPreservedContent = (
          String(this.contentDisplayDocId || "").trim() === normalizedDocId
          && Array.isArray(this.contentSections)
          && this.contentSections.length > 0
        );
        if (!hasPreservedContent) {
          this.contentDisplayDocId = normalizedDocId;
          this.editorText = "";
          this.contentSections = [];
        }
        const message = this.submissionContentFailureMessage(error, hasPreservedContent);
        this.submissionContentError = message;
        this.$message.error(message);
        return false;
      } finally {
        if (isCurrentRequest()) {
          this.loadingSubmissionContent = false;
        }
      }
    },
    submissionContentFailureMessage(error, hasPreservedContent = false) {
      const status = Number(error && error.response && error.response.status);
      const backendMessage = String(
        (error && error.response && error.response.data && error.response.data.message)
        || "",
      ).trim();
      const isTimeout = status === 504
        || status === 408
        || (error && error.code === "ECONNABORTED")
        || /timeout/i.test(String((error && error.message) || ""));
      if (isTimeout) {
        return hasPreservedContent
          ? "章节内容刷新超时，已保留上一次成功加载的内容，请稍后重试。"
          : "章节内容加载超时，请稍后重新加载。";
      }
      const requiresReparse = status === 409
        || /(?:not ready|reparse required)/i.test(backendMessage);
      if (requiresReparse) {
        return hasPreservedContent
          ? "章节解析结果尚未就绪，已保留上一次成功加载的内容；请重新解析该文件后重试。"
          : "章节解析结果尚未就绪，请重新解析该文件后重试。";
      }
      const reason = backendMessage && !/^request failed$/i.test(backendMessage)
        ? `：${backendMessage}`
        : "。";
      return hasPreservedContent
        ? `章节内容刷新失败，已保留上一次成功加载的内容${reason}`
        : `章节内容加载失败${reason}`;
    },
    async retrySubmissionContent() {
      if (!this.activeDocId || this.loadingSubmissionContent) {
        return;
      }
      await this.loadSubmissionText(this.activeDocId, true);
    },
    syncEditorToCurrentSection(fallbackContent = "") {
      const current = this.currentContentSection;
      const rawText = (current && (current.raw_content || current.content)) || fallbackContent || "";
      const cleanedText = (current && (current.cleaned_markdown || current.display_content)) || rawText;
      this.editorText = this.contentViewMode === "raw" ? rawText : cleanedText;
    },
    async saveEditedContent() {
      if (!this.activeDocId) {
        return this.$message.warning("请先选择申报资料文件");
      }
      const projectToken = this.sessionProjectToken;
      const projectId = String(this.projectId || "").trim();
      const docId = String(this.activeDocId || "").trim();
      const requestToken = this.saveContentRequestToken + 1;
      this.saveContentRequestToken = requestToken;
      const isCurrentSave = () => (
        this.isProjectSessionTokenActive(projectToken)
        && this.saveContentRequestToken === requestToken
      );
      this.savingContent = true;
      try {
        const sectionId = String((this.currentContentSection && this.currentContentSection.section_id) || this.currentBrowseSectionId || "").trim();
        const content = this.editorText || "";
        await saveSubmissionContent(projectId, docId, {
          content,
          section_id: sectionId,
        });
        if (!isCurrentSave()) {
          return;
        }
        this.$delete(this.submissionContentMap, docId);
        this.$message.success("章节内容已保存");
        if (String(this.activeDocId || "").trim() === docId) {
          this.contentEditorMode = false;
          await this.loadSubmissionText(docId, true);
        }
      } catch (e) {
        if (!isCurrentSave()) {
          return;
        }
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "Request failed");
      } finally {
        if (isCurrentSave()) {
          this.savingContent = false;
        }
      }
    },
    async run() {
      if (!this.activeDocId) {
        return this.$message.warning("请先选择申报资料文件");
      }
      this.resetRunStream("");
      const context = this.beginMainRunOperation("run", { docId: this.activeDocId });
      this.running = true;
      try {
        const startRes = await startPreReviewTask(this.buildRunPayload(), { silentError: true });
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        const startData = (startRes && startRes.data) || {};
        const taskId = String(startData.task_id || "").trim();
        if (!taskId) {
          throw new Error("辅助审评任务未返回 task_id");
        }
        this.assertMainRunTaskSnapshot(startData, taskId, context);
        this.runPollingTaskId = taskId;
        const res = await this.waitForRunTask(taskId, context);
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        this.$message.success((res && res.message) || "辅助审评任务已完成");
        this.selectedRun = null;
        await this.loadRuns(context.projectToken);
      } catch (e) {
        if (this.isRunPollingCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "Request failed");
      } finally {
        if (this.isMainRunContextCurrent(context)) {
          this.running = false;
          if (this.runStreamStatus === "connecting") {
            this.runStreamStatus = "idle";
          }
        }
      }
    },
    async runCurrentSectionReview() {
      if (!this.currentBrowseSectionId) {
        return this.$message.warning("请先选择章节");
      }
      const currentNode = this.currentBrowseSection;
      const leafSectionIds = this.currentBrowseLeafSectionIds;
      const hasChildSections = !!(currentNode && Array.isArray(currentNode.children_sections) && currentNode.children_sections.length);
      const targetSectionId = String(this.currentBrowseSectionId || "").trim();
      this.resetRunStream(targetSectionId);
      const taskType = hasChildSections && leafSectionIds.length > 1 ? "module_replay" : "section_replay";
      const context = this.beginMainRunOperation(taskType, { sectionId: targetSectionId, docId: "" });
      this.runningSection = true;
      try {
        const replayDocId = await this.resolveReplayDocId(targetSectionId);
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        if (!replayDocId) {
          this.$message.error("当前章节没有可用的挂载文件，请先检查上传和章节挂载。");
          return;
        }
        context.docId = replayDocId;
        if (replayDocId !== this.activeDocId) {
          this.activeDocId = replayDocId;
          await this.onDocChange(this.activeDocId);
          if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        }
        const runConfig = this.buildRunPayload().run_config;
        let startRes = null;
        if (hasChildSections && leafSectionIds.length > 1) {
          startRes = await startModuleReplayTask(context.projectId, context.docId, targetSectionId, {
            run_config: runConfig,
          }, { silentError: true });
        } else {
          startRes = await startSectionReplayTask(context.projectId, context.docId, targetSectionId, {
            run_config: runConfig,
          }, { silentError: true });
        }
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        const startData = (startRes && startRes.data) || {};
        const taskId = String(startData.task_id || "").trim();
        if (!taskId) {
          throw new Error("章节审评任务未返回 task_id");
        }
        this.assertMainRunTaskSnapshot(startData, taskId, context);
        this.runPollingTaskId = taskId;
        const res = await this.waitForRunTask(taskId, context);
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        const data = (res && res.data) || {};
        const runId = String(data.run_id || "").trim();
        this.$message.success((res && res.message) || (hasChildSections ? "当前节点叶子章节审评完成" : "当前章节审评完成"));
        await this.loadRuns(context.projectToken);
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        if (runId) {
          const matched = this.runs.find((item) => item.run_id === runId);
          if (matched) {
            await this.viewRun(matched);
          }
        }
      } catch (e) {
        if (this.isRunPollingCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "Request failed");
      } finally {
        if (this.isMainRunContextCurrent(context)) {
          this.runningSection = false;
        }
      }
    },
    async runCurrentModuleReview() {
      const scopeSectionId = String(this.currentBrowseScopeSectionId || "").trim();
      if (!scopeSectionId) {
        return this.$message.warning("请先选择模块或章节");
      }
      this.resetRunStream(scopeSectionId);
      const context = this.beginMainRunOperation("module_replay", { sectionId: scopeSectionId, docId: "" });
      this.runningModule = true;
      try {
        const replayDocId = await this.resolveReplayDocId(this.currentBrowseSectionId || scopeSectionId);
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        if (!replayDocId) {
          this.$message.error("当前模块没有可用的挂载文件，请先检查上传和章节挂载。");
          return;
        }
        context.docId = replayDocId;
        if (replayDocId !== this.activeDocId) {
          this.activeDocId = replayDocId;
          await this.onDocChange(this.activeDocId);
          if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        }
        const startRes = await startModuleReplayTask(context.projectId, context.docId, scopeSectionId, {
          run_config: this.buildRunPayload().run_config,
        }, { silentError: true });
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        const startData = (startRes && startRes.data) || {};
        const taskId = String(startData.task_id || "").trim();
        if (!taskId) {
          throw new Error("模块审评任务未返回 task_id");
        }
        this.assertMainRunTaskSnapshot(startData, taskId, context);
        this.runPollingTaskId = taskId;
        const res = await this.waitForRunTask(taskId, context);
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        const data = (res && res.data) || {};
        const runId = String(data.run_id || "").trim();
        this.$message.success((res && res.message) || "当前模块审评完成");
        await this.loadRuns(context.projectToken);
        if (!this.isMainRunContextCurrent(context)) throw this.mainRunCancellation();
        if (runId) {
          const matched = this.runs.find((item) => item.run_id === runId);
          if (matched) {
            await this.viewRun(matched);
          }
        }
      } catch (e) {
        if (this.isRunPollingCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "Request failed");
      } finally {
        if (this.isMainRunContextCurrent(context)) {
          this.runningModule = false;
        }
      }
    },
    async exportCurrentRunReviewConclusions() {
      if (this.selectedRunIncomplete) {
        return this.$message.warning("本次审评未完成，不能导出完整审评报告");
      }
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      if (!runId) {
        return this.$message.warning("请先选择一个辅助审评运行结果");
      }
      this.exportingReviewConclusions = true;
      try {
        const res = await exportReviewConclusions(runId);
        const data = (res && res.data) || {};
        const downloadUrl = String(data.download_url || "").trim();
        if (downloadUrl) {
          window.open(downloadUrl, "_blank");
        }
        this.$message.success((res && res.message) || "审评结论已导出");
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "Request failed");
      } finally {
        this.exportingReviewConclusions = false;
      }
    },
    async viewRun(row, projectToken = this.sessionProjectToken) {
      if (!row || !row.run_id) {
        return;
      }
      const requestedRunId = String(row.run_id || "").trim();
      const previousRunId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      if (previousRunId && previousRunId !== requestedRunId) {
        this.cancelAllLongWorkflowTaskPolling("已切换运行记录，原后台操作不再回写当前结果");
      }
      const requestToken = this.runResultRequestToken + 1;
      this.runResultRequestToken = requestToken;
      const isCurrentRunRequest = () => (
        this.isProjectSessionTokenActive(projectToken)
        && this.runResultRequestToken === requestToken
        && String((this.selectedRun && this.selectedRun.run_id) || "").trim() === requestedRunId
      );
      this.traceArtifactRequestToken += 1;
      this.loadingTraceArtifact = false;
      this.p52FeedbackPatchesRequestToken += 1;
      this.loadingP52FeedbackPatches = false;
      const isSameDisplayedRun = String(this.displayedRunId || "").trim() === requestedRunId;
      if (!isSameDisplayedRun) {
        this.resultOverview = null;
        this.sectionTraceMap = {};
        this.sectionPatchMap = {};
        this.sectionFeedbackHistory = null;
        this.sectionExecutionAuditDetail = null;
        this.sectionExampleDetail = null;
        this.sectionRunDetailLoadState = { key: "", promise: null };
        this.displayedRunId = requestedRunId;
      }
      this.runResultLoadError = "";
      this.selectedRun = row;
      this.feedbackForm.enableOptimize = (row.feedback_loop_mode || "feedback_optimize") !== "feedback_only";
      this.resetSectionFeedbackDraft();
      this.feedbackForm.enableOptimize = (row.feedback_loop_mode || "feedback_optimize") !== "feedback_only";
      this.syncSectionAblationFromCache();
      try {
        const capture = (promise) => promise.then(
          (response) => ({ ok: true, response }),
          (error) => ({ ok: false, error }),
        );
        const [overviewResult, traceResult, patchResult] = await Promise.all([
          capture(sectionOverview(row.run_id, { compact: true, silentError: true })),
          capture(sectionTraces(row.run_id, { compact: true }, { silentError: true })),
          capture(sectionPatchCandidates(row.run_id, {}, { silentError: true })),
        ]);
        if (!isCurrentRunRequest()) {
          return;
        }
        if (overviewResult.ok) {
          this.resultOverview = (overviewResult.response && overviewResult.response.data) || null;
          if (this.resultOverview && this.resultOverview.run_summary) {
            this.selectedRun = { ...this.selectedRun, summary_payload: this.resultOverview.run_summary };
          }
        }
        if (traceResult.ok) {
          const traces = (traceResult.response && traceResult.response.data) || [];
          this.sectionTraceMap = Array.isArray(traces)
            ? traces.reduce((acc, item) => {
                if (item && item.section_id) {
                  acc[item.section_id] = item;
                }
                return acc;
              }, {})
            : {};
        }
        if (patchResult.ok) {
          const patches = (patchResult.response && patchResult.response.data) || [];
          this.sectionPatchMap = Array.isArray(patches)
            ? patches.reduce((acc, item) => {
                const sid = String((item && item.section_id) || "").trim();
                if (!sid) {
                  return acc;
                }
                if (!acc[sid]) {
                  acc[sid] = [];
                }
                acc[sid].push(item);
                return acc;
              }, {})
            : {};
        }
        const failedCount = [overviewResult, traceResult, patchResult].filter((item) => !item.ok).length;
        if (failedCount) {
          this.runResultLoadError = isSameDisplayedRun
            ? `审评结果刷新有 ${failedCount} 项请求失败，已保留上一次成功数据。`
            : `审评结果有 ${failedCount} 项数据加载失败，请重新加载。`;
        }
        if (this.resultOverview) {
          this.ensureSectionSelectionForRun();
        }
        this.syncSectionAblationFromCache();
        await this.loadSelectedSectionDetailBundle(true);
      } catch (error) {
        if (!isCurrentRunRequest()) {
          return;
        }
        this.runResultLoadError = isSameDisplayedRun
          ? "审评结果刷新失败，已保留上一次成功数据。"
          : "审评结果加载失败，请重新加载。";
      }
    },
    async retrySelectedRunResult() {
      if (!this.selectedRun || !this.selectedRun.run_id) {
        return;
      }
      await this.viewRun(this.selectedRun, this.sessionProjectToken);
    },
    async loadSelectedSectionFeedbackHistory() {
      if (!this.selectedRun || !this.selectedRun.run_id) {
        this.sectionFeedbackHistory = null;
        this.hydrateFeedbackOptimizeResultFromHistory(null);
        return;
      }
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!sectionId) {
        this.sectionFeedbackHistory = null;
        this.hydrateFeedbackOptimizeResultFromHistory(null);
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(true);
      try {
        const res = await feedbackStats(requestContext.runId, { section_id: sectionId });
        if (!this.isSectionDetailRequestContextActive(requestContext, true)) {
          return;
        }
        const data = (res && res.data) || {};
        const history = data.history && typeof data.history === "object"
          ? data.history
          : this.emptySectionFeedbackHistory();
        this.sectionFeedbackHistory = history;
        this.hydrateFeedbackOptimizeResultFromHistory(history);
        if (Array.isArray(data.reference_examples)) {
          this.sectionExampleDetail = this.normalizeSectionExampleDetail(data.reference_examples);
        }
      } catch (_) {
        if (!this.isSectionDetailRequestContextActive(requestContext, true)) {
          return;
        }
        if (!this.sectionFeedbackHistory) {
          this.sectionFeedbackHistory = this.emptySectionFeedbackHistory();
          this.hydrateFeedbackOptimizeResultFromHistory(null);
        }
      }
    },
    async loadSelectedSectionTrace(force = false) {
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!runId || !sectionId) {
        return;
      }
      const existing = this.sectionTraceMap[sectionId];
      if (!force && existing && String(existing.response_mode || "").trim() !== "compact") {
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(true);
      try {
        const res = await sectionTraces(
          runId,
          { section_id: sectionId, compact: false },
          { silentError: true },
        );
        if (!this.isSectionDetailRequestContextActive(requestContext, true)) {
          return;
        }
        const rows = Array.isArray(res && res.data) ? res.data : [];
        const detail = rows.find((item) => String((item && item.section_id) || "").trim() === sectionId) || null;
        if (detail) {
          this.$set(this.sectionTraceMap, sectionId, detail);
        }
      } catch (_) {
        // 列表中的精简 trace 仍可用；详情读取失败时不清空已有摘要。
      }
    },
    runHistoryOptionLabel(item) {
      const row = item && typeof item === "object" ? item : {};
      const version = row.version_no === undefined || row.version_no === null ? "-" : row.version_no;
      const source = String(row.source_file_name || row.source_doc_id || "未知文件").trim();
      const time = String(row.finish_time || row.create_time || "未完成").trim();
      const sectionCount = Number(row.reviewed_section_count || 0);
      return `V${version}｜${source}｜${time}｜${sectionCount}章｜${row.run_id || "-"}`;
    },
    onRunHistoryChange(runId) {
      const normalizedRunId = String(runId || "").trim();
      const matched = this.runs.find((item) => String((item && item.run_id) || "").trim() === normalizedRunId);
      if (!matched) {
        this.$message.warning("未找到所选运行记录");
        return;
      }
      const currentQueryRunId = String(this.$route.query.run_id || "").trim();
      if (currentQueryRunId !== normalizedRunId) {
        this.$router.replace({
          name: "pre-review-session",
          params: { projectId: this.projectId },
          query: { ...this.$route.query, run_id: normalizedRunId },
        }).catch(() => {});
        return;
      }
      this.viewRun(matched);
    },
    async loadSelectedP52FeedbackPatches(force = false) {
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      const requestToken = this.p52FeedbackPatchesRequestToken + 1;
      this.p52FeedbackPatchesRequestToken = requestToken;
      if (!runId || !sectionId || !this.isP52SectionId(sectionId)) {
        this.p52FeedbackPatches = [];
        this.loadingP52FeedbackPatches = false;
        return;
      }
      if (!force && Array.isArray(this.p52FeedbackPatches) && this.p52FeedbackPatches.length) {
        this.loadingP52FeedbackPatches = false;
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(true);
      const isCurrentRequest = () => (
        this.p52FeedbackPatchesRequestToken === requestToken
        && this.isSectionDetailRequestContextActive(requestContext, true)
      );
      this.loadingP52FeedbackPatches = true;
      try {
        const res = await listP52FeedbackPatches(runId, sectionId, {});
        if (!isCurrentRequest()) {
          return;
        }
        const data = (res && res.data) || {};
        const items = Array.isArray(data.items) ? data.items : [];
        this.p52FeedbackPatches = items;
        this.feedbackOptimizeResult = {
          ...(this.feedbackOptimizeResult || {}),
          candidate_patches: items,
        };
      } catch (_) {
        if (!isCurrentRequest()) {
          return;
        }
        // 同一运行/章节刷新失败时保留已有 patch；跨资源切换会提前清空。
      } finally {
        if (isCurrentRequest()) {
          this.loadingP52FeedbackPatches = false;
        }
      }
    },
    async refreshSelectedRunAfterP52Workflow() {
      await this.loadSelectedSectionFeedbackHistory();
      await this.loadRuns();
      if (this.selectedRun && this.selectedRun.run_id) {
        const matched = this.runs.find((item) => item.run_id === this.selectedRun.run_id) || this.selectedRun;
        await this.viewRun(matched);
      }
    },
    async loadSelectedSectionRules() {
      if (!this.projectId) {
        this.sectionRulesDetail = null;
        return;
      }
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!sectionId) {
        this.sectionRulesDetail = null;
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(false);
      try {
        const res = await sectionRules(requestContext.projectId, sectionId);
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        const data = (res && res.data) || {};
        this.sectionRulesDetail = data && typeof data === "object"
          ? data
          : this.emptySectionRulesDetail();
      } catch (_) {
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        if (!this.sectionRulesDetail) {
          this.sectionRulesDetail = this.emptySectionRulesDetail();
        }
      }
    },
    async loadSelectedSectionPromptRules() {
      if (!this.projectId) {
        this.sectionPromptRuleDetail = null;
        return;
      }
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!sectionId) {
        this.sectionPromptRuleDetail = null;
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(false);
      try {
        const res = await sectionPromptRules(requestContext.projectId, sectionId);
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        const data = (res && res.data) || {};
        this.sectionPromptRuleDetail = data && typeof data === "object"
          ? data
          : this.emptySectionPromptRuleDetail();
      } catch (_) {
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        if (!this.sectionPromptRuleDetail) {
          this.sectionPromptRuleDetail = this.emptySectionPromptRuleDetail();
        }
      }
    },
    async loadSelectedSectionExamples() {
      if (!this.projectId) {
        this.sectionExampleDetail = null;
        return;
      }
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!sectionId) {
        this.sectionExampleDetail = null;
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(false);
      try {
        const res = await sectionExamples(requestContext.projectId, sectionId);
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        const data = (res && res.data) || {};
        this.sectionExampleDetail = data && typeof data === "object"
          ? data
          : this.emptySectionExampleDetail();
      } catch (_) {
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        if (!this.sectionExampleDetail) {
          this.sectionExampleDetail = this.emptySectionExampleDetail();
        }
      }
    },
    normalizeSectionExampleDetail(items) {
      const rows = Array.isArray(items) ? items.filter((item) => item && typeof item === "object") : [];
      const typeBreakdown = {};
      rows.forEach((item) => {
        const exampleType = String(item.example_type || "").trim() || "unknown";
        typeBreakdown[exampleType] = Number(typeBreakdown[exampleType] || 0) + 1;
      });
      return {
        items: rows,
        type_breakdown: typeBreakdown,
        summary: {
          total: rows.length,
          reference_count: Number(typeBreakdown.reference || 0),
          few_shot_count: Number(typeBreakdown.few_shot || 0),
          evaluation_case_count: Number(typeBreakdown.evaluation_case || 0),
        },
      };
    },
    async loadSelectedSectionExperienceMemory() {
      if (!this.projectId) {
        this.sectionExperienceMemoryDetail = null;
        return;
      }
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!sectionId) {
        this.sectionExperienceMemoryDetail = null;
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(false);
      try {
        const res = await sectionExperienceMemory(requestContext.projectId, sectionId);
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        const data = (res && res.data) || {};
        this.sectionExperienceMemoryDetail = data && typeof data === "object"
          ? data
          : this.emptySectionExperienceMemoryDetail();
      } catch (_) {
        if (!this.isSectionDetailRequestContextActive(requestContext, false)) {
          return;
        }
        if (!this.sectionExperienceMemoryDetail) {
          this.sectionExperienceMemoryDetail = this.emptySectionExperienceMemoryDetail();
        }
      }
    },
    async loadSelectedSectionExecutionAudits() {
      if (!this.selectedRun || !this.selectedRun.run_id) {
        this.sectionExecutionAuditDetail = null;
        return;
      }
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!sectionId) {
        this.sectionExecutionAuditDetail = null;
        return;
      }
      const requestContext = this.createSectionDetailRequestContext(true);
      try {
        const res = await executionAudits(requestContext.runId, { section_id: sectionId });
        if (!this.isSectionDetailRequestContextActive(requestContext, true)) {
          return;
        }
        const data = (res && res.data) || {};
        this.sectionExecutionAuditDetail = data && typeof data === "object"
          ? data
          : this.emptySectionExecutionAuditDetail();
      } catch (_) {
        if (!this.isSectionDetailRequestContextActive(requestContext, true)) {
          return;
        }
        if (!this.sectionExecutionAuditDetail) {
          this.sectionExecutionAuditDetail = this.emptySectionExecutionAuditDetail();
        }
      }
    },
    ensureSectionSelectionForRun() {
      if (!this.resultOverview) {
        return;
      }
      const reviewed = Array.isArray(this.resultOverview.reviewed_section_ids) ? this.resultOverview.reviewed_section_ids : [];
      const allSections = Array.isArray(this.resultOverview.sections) ? this.resultOverview.sections : [];
      const allSectionIds = allSections
        .map((item) => String((item && item.section_id) || "").trim())
        .filter(Boolean);
      const current = String(this.currentBrowseSectionId || "").trim();
      if (current && this.hasSectionReviewResult(current)) {
        return;
      }
      if (reviewed.length) {
        const firstReviewed = reviewed.find((sid) => allSectionIds.includes(sid)) || reviewed[0];
        this.currentBrowseSectionId = firstReviewed;
        return;
      }
      if (current && allSectionIds.includes(current)) {
        return;
      }
      if (allSectionIds.length) {
        this.currentBrowseSectionId = allSectionIds[0];
      }
    },
    hasSectionReviewResult(sectionId) {
      const sid = String(sectionId || "").trim();
      if (!sid || !this.resultOverview) {
        return false;
      }
      const standardized = (this.resultOverview.standardized_output_by_section_id || {})[sid];
      const sectionOutput = (this.resultOverview.section_output_by_section_id || {})[sid];
      const conclusion = (this.resultOverview.conclusion_by_section_id || {})[sid];
      return !!(standardized || sectionOutput || conclusion);
    },
    currentEditorBaselineText() {
      const current = this.currentContentSection;
      if (!current) {
        return "";
      }
      const rawText = String(current.raw_content || current.content || "").trim();
      const cleanedText = String(current.cleaned_markdown || current.display_content || "").trim();
      return this.contentViewMode === "raw" ? rawText : cleanedText || rawText;
    },
    buildSectionFeedbackPayload(chainModeOverride = "") {
      const sectionId = String((this.selectedDisplayReview && this.selectedDisplayReview.section_id) || this.currentBrowseSectionId || "").trim();
      const chainMode = chainModeOverride || (this.selectedRunFeedbackLoopMode === "feedback_only" ? "feedback_only" : "feedback_optimize");
      const buildEmptyFieldFeedback = () => ({
        rule: "unknown",
        fact: "unknown",
        evidence: "unknown",
        reasoning: "unknown",
        conclusion: "unknown",
      });
      const hasDetailedFieldFeedback = (fieldFeedback) => {
        if (!fieldFeedback || typeof fieldFeedback !== "object") {
          return false;
        }
        return Object.values(fieldFeedback).some((value) => {
          const normalized = String(value || "").trim().toLowerCase();
          return !!normalized && normalized !== "unknown";
        });
      };
      const labels = [];
      const issueFeedbackItems = Object.keys(this.feedbackForm.issue_feedback_map || {}).map((issueKey) => {
        const item = this.feedbackForm.issue_feedback_map[issueKey] || {};
        const fieldFeedback = {
          ...buildEmptyFieldFeedback(),
          ...(item.field_feedback && typeof item.field_feedback === "object" ? item.field_feedback : {}),
        };
        return {
          issue_key: issueKey,
          feedback_kind: item.feedback_kind || "task_verdict",
          verdict: item.verdict || "",
          feedback_text: item.feedback_text || "",
          error_reason: item.error_reason || "",
          task_code: item.task_code || "",
          task_question: item.task_question || "",
          task_status: item.task_status || "",
          rule_code: item.rule_code || "",
          rule_text: item.rule_text || "",
          requirement_point: item.requirement_point || "",
          basis: item.basis || "",
          material_fact: item.material_fact || "",
          evidence_support: item.evidence_support || "",
          comparison: item.comparison || "",
          judgment_reason: item.judgment_reason || "",
          location: item.location || "",
          issue: item.issue || item.problem || "",
          problem: item.problem || item.issue || "",
          advice: item.advice || "",
          violating_text: item.violating_text || "",
          evidence_files: Array.isArray(item.evidence_files) ? item.evidence_files : [],
          field_feedback: fieldFeedback,
        };
      }).filter((item) => item.verdict || item.feedback_text || item.error_reason || hasDetailedFieldFeedback(item.field_feedback));
      const missingItemFeedback = {
        text: String(this.feedbackForm.missing_item_feedback_text || "").trim(),
        reason: String(this.feedbackForm.missing_item_feedback_reason || "").trim(),
        section_id: sectionId,
        section_name: this.currentBrowseSectionLabel,
      };
      if (missingItemFeedback.text || missingItemFeedback.reason) {
        labels.push("missing_item_feedback:provided");
        issueFeedbackItems.push({
          issue_key: `missing_item:${sectionId || "section"}`,
          feedback_kind: "missing_item",
          verdict: "missing",
          feedback_text: missingItemFeedback.text,
          error_reason: missingItemFeedback.reason,
          task_code: "",
          task_question: "补充系统遗漏的问题项或判断项",
          task_status: "missing",
          rule_code: "",
          rule_text: "",
          requirement_point: "",
          basis: "",
          material_fact: "",
          evidence_support: "",
          comparison: "",
          judgment_reason: missingItemFeedback.reason,
          location: this.currentBrowseSectionLabel,
          issue: missingItemFeedback.text,
          problem: missingItemFeedback.text,
          advice: missingItemFeedback.reason,
          violating_text: missingItemFeedback.text,
          evidence_files: [],
          field_feedback: buildEmptyFieldFeedback(),
        });
      }
      const hasMissingItem = issueFeedbackItems.some((item) => item.feedback_kind === "missing_item");
      const hasIncorrectIssue = issueFeedbackItems.some((item) => {
        if (String(item.verdict || "").trim().toLowerCase() === "incorrect") {
          return true;
        }
        return Object.values(item.field_feedback || {}).some((value) => String(value || "").trim().toLowerCase() === "incorrect");
      });
      const hasAnyIssueFeedback = issueFeedbackItems.length > 0;
      const evidenceVerdicts = Object.keys(this.feedbackForm.evidence_feedback_map || {})
        .map((key) => String(this.feedbackForm.evidence_feedback_map[key] || "").trim().toLowerCase())
        .filter(Boolean);
      const evidenceFieldSignals = issueFeedbackItems
        .map((item) => String(((item.field_feedback || {}).evidence) || "").trim().toLowerCase())
        .filter((value) => value && value !== "unknown");
      const hasIncorrectEvidence = [...evidenceVerdicts, ...evidenceFieldSignals].some((value) => value === "incorrect");
      const hasPositiveEvidence = [...evidenceVerdicts, ...evidenceFieldSignals].some((value) => value === "correct" || value === "partial");
      const derivedDecision = hasMissingItem
        ? (hasIncorrectIssue ? "partial" : "missed")
        : (hasIncorrectIssue ? "false_positive" : "valid");
      const derivedConclusionFeedback = hasAnyIssueFeedback
        ? (hasIncorrectIssue || hasMissingItem ? "incorrect" : "correct")
        : "unknown";
      const derivedRetrievalFeedback = hasIncorrectEvidence
        ? "incorrect"
        : (hasPositiveEvidence ? "correct" : "unknown");
      labels.push(`conclusion_feedback:${derivedConclusionFeedback}`);
      labels.push(`retrieval_feedback:${derivedRetrievalFeedback}`);
      labels.push(`derived_decision:${derivedDecision}`);
      return {
        section_id: sectionId,
        decision: derivedDecision,
        feedback_type: derivedDecision,
        chain_mode: chainMode,
        manual_modified: this.currentEditorBaselineText().trim() !== String(this.editorText || "").trim(),
        original_output: this.selectedDisplayReview || {},
        revised_output: { edited_content: this.editorText || "" },
        feedback_text: this.feedbackForm.feedback_text || "",
        suggestion: this.feedbackForm.feedback_text || "",
        conclusion_feedback: derivedConclusionFeedback,
        retrieval_feedback: derivedRetrievalFeedback,
        labels,
        issue_feedback: issueFeedbackItems,
        evidence_feedback: Object.keys(this.feedbackForm.evidence_feedback_map || {}).map((evidenceId) => ({
          evidence_id: evidenceId,
          verdict: this.feedbackForm.evidence_feedback_map[evidenceId],
        })).filter((item) => item.evidence_id && item.verdict),
        missing_item_feedback: missingItemFeedback,
        reference_example: this.feedbackForm.reference_example_content
          ? {
              title: this.feedbackForm.reference_example_title || `${this.currentBrowseSectionLabel} 参考示例`,
              content: this.feedbackForm.reference_example_content,
              input: {
                section_id: sectionId,
                section_name: this.currentBrowseSectionLabel,
              },
              expected_output: this.selectedDisplayReview || {},
            }
          : {},
        operator: "reviewer",
      };
    },
    normalizeFeedbackLoopResult(data) {
      const payload = data && typeof data === "object" ? data : {};
      const chapterLoop = payload.chapter_feedback_loop && typeof payload.chapter_feedback_loop === "object"
        ? payload.chapter_feedback_loop
        : payload;
      return chapterLoop && typeof chapterLoop === "object" ? chapterLoop : {};
    },
    async submitSectionFeedback() {
      if (!this.selectedRun || !this.selectedDisplayReview) {
        return this.$message.warning("请先选择一个章节审评结果");
      }
      const requestRunId = String(this.selectedRun.run_id || "").trim();
      const requestSectionId = String(this.currentBrowseSectionId || "").trim();
      const requestProjectToken = this.sessionProjectToken;
      const isFeedbackContextCurrent = () => (
        this.isProjectSessionTokenActive(requestProjectToken)
        && String((this.selectedRun && this.selectedRun.run_id) || "").trim() === requestRunId
        && String(this.currentBrowseSectionId || "").trim() === requestSectionId
      );
      let workflowContext = null;
      this.submittingFeedback = true;
      try {
        const payload = this.buildSectionFeedbackPayload();
        if (this.selectedRunFeedbackLoopMode !== "feedback_only" && this.isP52SectionId(this.currentBrowseSectionId)) {
          workflowContext = this.beginLongWorkflowOperation("p52_feedback_optimize");
          await this.executeP52FeedbackOptimize(payload, workflowContext);
          if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
          this.$message.success("P52 反馈优化已生成候选 patch");
        } else if (this.selectedRunFeedbackLoopMode !== "feedback_only") {
          workflowContext = this.beginLongWorkflowOperation("feedback_optimize");
          const res = await this.executeLongWorkflowTask(
            workflowContext,
            () => optimizeFeedbackAsync(requestRunId, payload, { silentError: true }),
          );
          if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
          const chapterLoop = this.normalizeFeedbackLoopResult((res && res.data) || {});
          this.feedbackOptimizeResult = {
            feedback_key: chapterLoop.feedback_key || "",
            analysis_result: chapterLoop.analysis_result || null,
            patch_result: chapterLoop.patch_result || null,
            candidate_patches: [],
            verification_result: null,
            verification_plan: null,
            meta_reflection: chapterLoop.meta_reflection || null,
            feedback_projection: chapterLoop.feedback_projection || null,
            reference_example: chapterLoop.reference_example || null,
            optimize_evaluation: chapterLoop.optimize_evaluation || null,
            feedback_optimize_status: chapterLoop.feedback_optimize_status || "",
            candidate_register_status: chapterLoop.candidate_register_status || "",
            replay_status: chapterLoop.replay_status || "",
            error_message: chapterLoop.error_message || "",
          };
          this.optimizeEvaluationForm.patch_feedback_map = {};
          this.$message.success("反馈已进入优化闭环");
        } else {
          await addFeedback(requestRunId, payload);
          if (!isFeedbackContextCurrent()) return;
          this.feedbackOptimizeResult = {
            feedback_key: "",
            analysis_result: null,
            patch_result: null,
            candidate_patches: [],
            verification_result: null,
            verification_plan: null,
            meta_reflection: null,
            feedback_projection: null,
            reference_example: null,
            optimize_evaluation: null,
            feedback_optimize_status: "",
            candidate_register_status: "",
            replay_status: "",
            error_message: "",
          };
          this.$message.success("反馈已提交");
        }
        if (workflowContext && !this.isLongWorkflowContextCurrent(workflowContext)) return;
        if (!workflowContext && !isFeedbackContextCurrent()) return;
        await this.loadRuns();
        if (this.selectedRun) {
          const matched = this.runs.find((item) => item.run_id === this.selectedRun.run_id) || this.selectedRun;
          await this.viewRun(matched);
        }
      } catch (e) {
        if (this.isLongWorkflowCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || this.longWorkflowErrorMessage(e, "反馈提交失败"));
      } finally {
        if (!workflowContext || this.isLongWorkflowContextCurrent(workflowContext)) {
          this.submittingFeedback = false;
        }
      }
    },
    async handleSubmitP52FeedbackOptimize() {
      if (!this.selectedRun || !this.selectedDisplayReview || !this.isP52SectionId(this.currentBrowseSectionId)) {
        return this.$message.warning("当前章节不支持 P52 专用反馈优化");
      }
      const workflowContext = this.beginLongWorkflowOperation("p52_feedback_optimize");
      this.submittingP52FeedbackOptimize = true;
      try {
        const payload = this.buildSectionFeedbackPayload();
        await this.executeP52FeedbackOptimize(payload, workflowContext);
        if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
        this.$message.success("P52 反馈优化已完成");
        await this.loadSelectedSectionFeedbackHistory();
      } catch (e) {
        if (this.isLongWorkflowCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || this.longWorkflowErrorMessage(e, "P52 反馈优化失败"));
      } finally {
        if (this.isLongWorkflowContextCurrent(workflowContext)) {
          this.submittingP52FeedbackOptimize = false;
        }
      }
    },
    async executeP52FeedbackOptimize(payload, workflowContext = null) {
      const context = workflowContext || this.beginLongWorkflowOperation("p52_feedback_optimize");
      const res = await this.executeLongWorkflowTask(
        context,
        () => optimizeP52FeedbackAsync(context.runId, context.sectionId, payload, { silentError: true }),
      );
      if (!this.isLongWorkflowContextCurrent(context)) return;
      const data = (res && res.data) || {};
      const patches = Array.isArray(data.candidate_patches) ? data.candidate_patches : [];
      this.feedbackOptimizeResult = {
        ...(this.feedbackOptimizeResult || {}),
        feedback_key: data.feedback_key || "",
        analysis_result: data.analysis_result || null,
        patch_result: {
          patches,
        },
        candidate_patches: patches,
        verification_plan: data.verification_plan || null,
        optimize_evaluation: null,
        feedback_optimize_status: "completed",
        candidate_register_status: patches.length ? "candidate_generated" : "skipped",
        replay_status: "",
        error_message: "",
      };
      this.p52FeedbackPatches = patches;
      await this.loadSelectedP52FeedbackPatches(true);
      if (!this.isLongWorkflowContextCurrent(context)) return;
      await this.refreshSelectedRunAfterP52Workflow();
    },
    async submitOptimizeEvaluation(payload) {
      if (!this.selectedRun || !this.feedbackOptimizeResult.feedback_key) {
        return this.$message.warning("当前没有可评价的反馈优化结果");
      }
      try {
        const res = await evaluateOptimizeFeedback(this.selectedRun.run_id, this.feedbackOptimizeResult.feedback_key, {
          section_id: this.currentBrowseSectionId,
          ...payload,
        });
        const data = (res && res.data) || {};
        this.feedbackOptimizeResult = {
          ...(this.feedbackOptimizeResult || {}),
          optimize_evaluation: data && typeof data === "object" ? data : null,
        };
        this.$message.success("反馈优化评价已保存");
        await this.loadSelectedSectionFeedbackHistory();
      } catch (e) {
        this.$message.error((e && e.message) || "保存反馈优化评价失败");
      }
    },
    buildFeedbackReplayPayload() {
      return this.buildSectionFeedbackPayload();
    },
    applyReplayResult(data, replayStatus) {
      const normalized = this.normalizeFeedbackLoopResult(data);
      const current = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult === "object"
        ? this.feedbackOptimizeResult
        : {};
      const optimizeStatus = replayStatus === "feedback_optimize_replayed" ? "replayed" : (normalized.feedback_optimize_status || current.feedback_optimize_status || "");
      this.feedbackOptimizeResult = {
        feedback_key: normalized.feedback_key || current.feedback_key || "",
        analysis_result: normalized.analysis_result || current.analysis_result || null,
        patch_result: normalized.patch_result || current.patch_result || null,
        meta_reflection: normalized.meta_reflection || current.meta_reflection || null,
        feedback_projection: normalized.feedback_projection || current.feedback_projection || null,
        reference_example: normalized.reference_example || current.reference_example || null,
        optimize_evaluation: normalized.optimize_evaluation || current.optimize_evaluation || null,
        feedback_optimize_status: optimizeStatus,
        candidate_register_status: normalized.candidate_register_status || current.candidate_register_status || "",
        replay_status: replayStatus,
        error_message: normalized.error_message || "",
      };
    },
    applyP52MetaReflectionResult(data) {
      const current = this.feedbackOptimizeResult && typeof this.feedbackOptimizeResult === "object"
        ? this.feedbackOptimizeResult
        : {};
      this.feedbackOptimizeResult = {
        ...current,
        meta_reflection: data && data.meta_reflection ? data.meta_reflection : null,
        replay_status: "p52_meta_reflection_replayed",
        error_message: "",
      };
    },
    async handleReplayFeedbackOptimize() {
      if (!this.selectedRun || !this.currentBrowseSectionId) {
        return this.$message.warning("请先选择运行记录和章节");
      }
      if (this.isP52SectionId(this.currentBrowseSectionId)) {
        return this.handleReplayVerifyP52Feedback();
      }
      const workflowContext = this.beginLongWorkflowOperation("feedback_replay_optimize");
      this.replayingFeedbackOptimize = true;
      try {
        const payload = this.buildFeedbackReplayPayload();
        const res = await this.executeLongWorkflowTask(
          workflowContext,
          () => replayFeedbackOptimizeAsync(workflowContext.runId, workflowContext.sectionId, payload, { silentError: true }),
        );
        if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
        const data = (res && res.data) || {};
        this.applyReplayResult(data, "feedback_optimize_replayed");
        this.$message.success((res && res.message) || "反馈优化回放完成");
      } catch (e) {
        if (this.isLongWorkflowCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || this.longWorkflowErrorMessage(e, "反馈优化回放失败"));
      } finally {
        if (this.isLongWorkflowContextCurrent(workflowContext)) {
          this.replayingFeedbackOptimize = false;
        }
      }
    },
    async handleReplayVerifyP52Feedback() {
      if (!this.selectedRun || !this.currentBrowseSectionId || !this.isP52SectionId(this.currentBrowseSectionId)) {
        return this.$message.warning("当前章节不支持 P52 回放验证");
      }
      this.stopP52VerifyResultPolling();
      const workflowContext = this.beginLongWorkflowOperation("p52_feedback_verify");
      this.replayingP52FeedbackVerify = true;
      try {
        const payload = this.buildFeedbackReplayPayload();
        const res = await this.executeLongWorkflowTask(
          workflowContext,
          () => replayVerifyP52FeedbackAsync(workflowContext.runId, workflowContext.sectionId, payload, { silentError: true }),
        );
        if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
        const data = (res && res.data) || {};
        this.feedbackOptimizeResult = {
          ...(this.feedbackOptimizeResult || {}),
          verification_result: data.verification_result || null,
          verification_plan: data.verification_result || null,
          replay_status: "p52_replay_verified",
          error_message: "",
        };
        this.$message.success((res && res.message) || "P52 回放验证已完成");
        await this.refreshSelectedRunAfterP52Workflow();
        if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
        this.startP52VerifyResultPolling();
      } catch (e) {
        if (this.isLongWorkflowCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || this.longWorkflowErrorMessage(e, "P52 回放验证失败"));
      } finally {
        if (this.isLongWorkflowContextCurrent(workflowContext)) {
          this.replayingP52FeedbackVerify = false;
        }
      }
    },
    stopP52VerifyResultPolling() {
      if (this.p52VerifyPollTimer) {
        clearTimeout(this.p52VerifyPollTimer);
        this.p52VerifyPollTimer = null;
      }
      this.p52VerifyPollCount = 0;
    },
    startP52VerifyResultPolling() {
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!runId || !sectionId || !this.isP52SectionId(sectionId)) {
        return;
      }
      this.stopP52VerifyResultPolling();
      const poll = async () => {
        if (!this.selectedRun
          || String((this.selectedRun && this.selectedRun.run_id) || "").trim() !== runId
          || String(this.currentBrowseSectionId || "").trim() !== sectionId) {
          this.stopP52VerifyResultPolling();
          return;
        }
        this.p52VerifyPollCount += 1;
        try {
          await this.loadSelectedSectionFeedbackHistory();
          await this.loadSelectedP52FeedbackPatches(true);
        } catch (_) {
          // 中文注释：轮询失败时不打断后续轮询，避免偶发网络抖动导致刷新中断
        }
        if (this.p52VerifyPollCount >= P52_VERIFY_RESULT_POLL_MAX_TIMES) {
          this.stopP52VerifyResultPolling();
          return;
        }
        this.p52VerifyPollTimer = setTimeout(poll, P52_VERIFY_RESULT_POLL_INTERVAL_MS);
      };
      this.p52VerifyPollTimer = setTimeout(poll, P52_VERIFY_RESULT_POLL_INTERVAL_MS);
    },
    async handleApproveP52FeedbackPatch(patch) {
      const patchId = String((patch && patch.patch_id) || "").trim();
      if (!this.selectedRun || !this.currentBrowseSectionId || !patchId) {
        return this.$message.warning("当前没有可批准的候选 patch");
      }
      this.actingP52PatchId = patchId;
      try {
        await approveP52FeedbackPatch(this.selectedRun.run_id, this.currentBrowseSectionId, patchId, {
          operator: "reviewer",
          comment: "前端结果页批准 P52 候选 patch",
        });
        this.$message.success("候选 patch 已批准");
        await this.loadSelectedP52FeedbackPatches(true);
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "批准 patch 失败");
      } finally {
        this.actingP52PatchId = "";
      }
    },
    async handleRejectP52FeedbackPatch(patch) {
      const patchId = String((patch && patch.patch_id) || "").trim();
      if (!this.selectedRun || !this.currentBrowseSectionId || !patchId) {
        return this.$message.warning("当前没有可拒绝的候选 patch");
      }
      this.actingP52PatchId = patchId;
      try {
        await rejectP52FeedbackPatch(this.selectedRun.run_id, this.currentBrowseSectionId, patchId, {
          operator: "reviewer",
          comment: "前端结果页拒绝 P52 候选 patch",
        });
        this.$message.success("候选 patch 已拒绝");
        await this.loadSelectedP52FeedbackPatches(true);
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "拒绝 patch 失败");
      } finally {
        this.actingP52PatchId = "";
      }
    },
    async handleReplayMetaReflection() {
      if (!this.selectedRun || !this.currentBrowseSectionId) {
        return this.$message.warning("请先选择运行记录和章节");
      }
      const isP52 = this.isP52SectionId(this.currentBrowseSectionId);
      const workflowContext = this.beginLongWorkflowOperation(isP52 ? "p52_meta_reflection" : "feedback_meta_reflection");
      const payload = this.buildFeedbackReplayPayload();
      this.replayingMetaReflection = true;
      try {
        const res = await this.executeLongWorkflowTask(
          workflowContext,
          () => (isP52
            ? replayP52MetaReflectionAsync(workflowContext.runId, workflowContext.sectionId, payload, { silentError: true })
            : replayMetaReflectionAsync(workflowContext.runId, workflowContext.sectionId, payload, { silentError: true })),
        );
        if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
        const data = (res && res.data) || {};
        if (isP52) {
          this.applyP52MetaReflectionResult(data);
          await this.refreshSelectedRunAfterP52Workflow();
          if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
          this.$message.success((res && res.message) || "P52 元反思已完成");
        } else {
          this.applyReplayResult(data, "meta_reflection_replayed");
          this.$message.success((res && res.message) || "元反思回放完成");
        }
      } catch (e) {
        if (this.isLongWorkflowCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || this.longWorkflowErrorMessage(e, isP52 ? "P52 元反思失败" : "元反思回放失败"));
      } finally {
        if (this.isLongWorkflowContextCurrent(workflowContext)) {
          this.replayingMetaReflection = false;
        }
      }
    },
    async runSectionAblationStudy() {
      if (!this.selectedRun || !this.selectedRun.run_id || !this.currentBrowseSectionId) {
        return this.$message.warning("请先选择运行记录和章节");
      }
      if (this.isP52SectionId(this.currentBrowseSectionId)) {
        this.runningAblation = true;
        try {
          const res = await runP52AblationStudy(this.selectedRun.run_id, this.currentBrowseSectionId, this.buildFeedbackReplayPayload());
          const detail = (res && res.data && typeof res.data === "object") ? res.data : this.emptySectionAblationDetail();
          const cacheKey = this.currentSectionAblationCacheKey();
          if (cacheKey) {
            this.$set(this.sectionAblationCache, cacheKey, detail);
          }
          this.sectionAblationDetail = detail;
          await this.refreshSelectedRunAfterP52Workflow();
          this.$message.success((res && res.message) || "P52 消融实验已执行");
        } catch (e) {
          const backendMessage = e && e.response && e.response.data && e.response.data.message;
          this.$message.error(backendMessage || (e && e.message) || "P52 消融实验失败");
        } finally {
          this.runningAblation = false;
        }
        return;
      }
      const requestRunId = String(this.selectedRun.run_id || "").trim();
      const requestSectionId = String(this.currentBrowseSectionId || "").trim();
      const requestProjectToken = this.sessionProjectToken;
      let workflowContext = null;
      this.runningAblation = true;
      try {
        const runConfig = this.resultOverview && typeof this.resultOverview.run_config === "object"
          ? this.resultOverview.run_config
          : {};
        const regressionRes = await generateRegressionCases({
          run_id: requestRunId,
          section_id: requestSectionId,
          run_config: runConfig,
        });
        if (
          !this.isProjectSessionTokenActive(requestProjectToken)
          || String((this.selectedRun && this.selectedRun.run_id) || "").trim() !== requestRunId
          || String(this.currentBrowseSectionId || "").trim() !== requestSectionId
        ) return;
        const regressionData = (regressionRes && regressionRes.data) || {};
        const cases = Array.isArray(regressionData.cases) ? regressionData.cases : [];
        const caseIds = cases
          .map((item) => String((item && item.case_id) || "").trim())
          .filter(Boolean);
        if (!caseIds.length) {
          this.sectionAblationDetail = this.emptySectionAblationDetail();
          return this.$message.warning("当前章节没有可用的 replay case，无法执行消融实验");
        }
        workflowContext = this.beginLongWorkflowOperation("feedback_evaluation_ablation");
        const ablationRes = await this.executeLongWorkflowTask(
          workflowContext,
          () => runAblationStudyAsyncApi({
            run_id: requestRunId,
            section_id: requestSectionId,
            case_ids: caseIds,
            version_config: { run_config: runConfig },
            study_config: { include_results: false },
          }, { silentError: true }),
        );
        if (!this.isLongWorkflowContextCurrent(workflowContext)) return;
        const detail = {
          ...((ablationRes && ablationRes.data && typeof ablationRes.data === "object") ? ablationRes.data : {}),
          generated_case_count: caseIds.length,
          source_run_id: requestRunId,
          source_section_id: requestSectionId,
        };
        const cacheKey = this.currentSectionAblationCacheKey();
        if (cacheKey) {
          this.$set(this.sectionAblationCache, cacheKey, detail);
        }
        this.sectionAblationDetail = detail;
        this.$message.success((ablationRes && ablationRes.message) || "消融实验已执行");
      } catch (e) {
        if (this.isLongWorkflowCancellation(e)) return;
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || this.longWorkflowErrorMessage(e, "消融实验失败"));
      } finally {
        if (!workflowContext || this.isLongWorkflowContextCurrent(workflowContext)) {
          this.runningAblation = false;
        }
      }
    },
    openPreviewDialog() {
      if (!this.activeDocId || !this.previewUrl) {
        this.$message.warning("请先选择可预览的文件");
        return;
      }
      this.previewDialog.visible = true;
    },
    closePreviewDialog() {
      this.previewDialog.visible = false;
    },
    async openTraceArtifactDialog() {
      const runId = String((this.selectedRun && this.selectedRun.run_id) || "").trim();
      const sectionId = String(this.currentBrowseSectionId || "").trim();
      if (!runId || !sectionId) {
        return this.$message.warning("请先选择一个运行记录和章节");
      }
      const projectToken = this.sessionProjectToken;
      const requestToken = this.traceArtifactRequestToken + 1;
      this.traceArtifactRequestToken = requestToken;
      const isCurrentRequest = () => (
        this.isProjectSessionTokenActive(projectToken)
        && this.traceArtifactRequestToken === requestToken
        && String((this.selectedRun && this.selectedRun.run_id) || "").trim() === runId
        && String(this.currentBrowseSectionId || "").trim() === sectionId
      );
      this.loadingTraceArtifact = true;
      try {
        const res = await sectionTraceArtifact(runId, sectionId);
        if (!isCurrentRequest()) {
          return;
        }
        const data = (res && res.data) || {};
        this.traceArtifactDialog = {
          visible: true,
          title: `原始 Trace 文件 - ${sectionId}`,
          content: String(data.content || ""),
          file_path: String((data.trace_artifact && data.trace_artifact.file_path) || ""),
        };
      } catch (e) {
        if (!isCurrentRequest()) {
          return;
        }
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "Request failed");
      } finally {
        if (isCurrentRequest()) {
          this.loadingTraceArtifact = false;
        }
      }
    },
    openPreviewInNewTab() {
      if (!this.previewUrl) {
        return;
      }
      window.open(this.previewUrl, "_blank");
    },
    selectSummarySection(sectionId) {
      const sid = String(sectionId || "").trim();
      if (!sid) {
        return;
      }
      this.currentBrowseNodeId = sid;
      this.currentBrowseSectionId = sid;
      const candidates = (Array.isArray(this.currentBrowseFiles) && this.currentBrowseFiles.length)
        ? this.currentBrowseFiles
        : (Array.isArray(this.submissions) ? this.submissions : []);
      const matched = candidates.find((item) => {
        const currentId = String((item && item.section_id) || "").trim();
        return currentId === sid || currentId.startsWith(`${sid}.`) || sid.startsWith(`${currentId}.`);
      });
      if (matched && matched.doc_id !== this.activeDocId) {
        this.activeDocId = matched.doc_id;
        this.onDocChange(this.activeDocId);
      }
    },
  },
};
</script>

<style scoped>
.session-page { display: flex; flex-direction: column; gap: 16px; }
.page-header { display: block; }
.page-header-main {
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}
.page-header-main h2 {
  margin: 0;
  flex: 0 0 auto;
  line-height: 1.2;
  white-space: nowrap;
}
.session-meta-inline {
  display: flex;
  flex: 0 1 auto;
  flex-direction: column;
  gap: 6px;
  padding-top: 2px;
  font-size: 12px;
  color: #475569;
}
.session-meta-line { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.reviewed-section-line { align-items: flex-start; }
.reviewed-section-picker { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.reviewed-section-picker :deep(.el-select) { width: min(420px, 60vw); }
.feedback-loop-inline { display: inline-flex; align-items: center; gap: 8px; }
.session-meta-hint { color: #64748b; }
.meta-row { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; color: #5a667a; font-size: 12px; }
.clickable { cursor: pointer; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.run-result-load-error { margin: 12px 0; }
.workspace { display: grid; grid-template-columns: 280px minmax(0, 1fr) 420px; gap: 16px; min-height: 680px; }
.workspace.left-pane-collapsed { grid-template-columns: 56px minmax(0, 1fr) 420px; }
.pane { display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.pane.left.collapsed { overflow: hidden; }
.pane-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.title { font-size: 14px; font-weight: 600; color: #2d3648; }
.muted, .empty, .list-item { color: #5a667a; font-size: 12px; line-height: 1.6; }
.actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.block { border: 1px solid #eef1f6; border-radius: 8px; padding: 12px; background: #fff; }
.chapter-tree { flex: 1; overflow: auto; border: 1px solid #eef1f6; border-radius: 8px; padding: 8px; }
.content-panel { display: grid; grid-template-rows: auto 1fr; gap: 12px; min-height: 0; }
.markdown-preview { min-height: 260px; padding: 12px; border: 1px solid #eef1f6; border-radius: 8px; overflow: auto; background: #fafcff; }
.preview-meta { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; color: #5a667a; font-size: 12px; }
.preview-frame-wrap { height: 78vh; border: 1px solid #eef1f6; border-radius: 8px; overflow: hidden; background: #fff; }
.preview-frame { width: 100%; height: 100%; }
.markdown-preview :deep(h2), .markdown-preview :deep(h3), .markdown-preview :deep(h4) { margin: 8px 0; }
.markdown-preview :deep(p) { margin: 0 0 8px; }
.markdown-preview :deep(ul), .markdown-preview :deep(ol) { margin: 0 0 8px 20px; }
.markdown-preview :deep(.md-table) { width: 100%; border-collapse: collapse; }
.markdown-preview :deep(.md-table th), .markdown-preview :deep(.md-table td) { border: 1px solid #dfe6f0; padding: 6px 8px; }
.stream-log-list { max-height: 240px; overflow: auto; border: 1px solid #eef1f6; border-radius: 8px; padding: 8px; background: #fafcff; }
.stream-log-item { display: flex; gap: 8px; padding: 4px 0; font-size: 12px; line-height: 1.6; color: #4b5565; }
.stream-log-time { color: #8b96a8; flex: 0 0 64px; }
.stream-log-text { flex: 1; white-space: pre-wrap; word-break: break-word; }
.stream-group-title { font-size: 12px; color: #2d3648; font-weight: 600; }
.stream-group-count { margin-left: 6px; font-size: 12px; color: #8b96a8; }
@media (max-width: 1440px) { .workspace { grid-template-columns: 260px minmax(0, 1fr) 380px; } .workspace.left-pane-collapsed { grid-template-columns: 56px minmax(0, 1fr) 380px; } }
@media (max-width: 1440px) {
  .page-header-main {
    align-items: flex-start;
    gap: 12px;
  }
}
@media (max-width: 960px) {
  .page-header-main {
    flex-direction: column;
  }
}
@media (max-width: 1200px) { .workspace { grid-template-columns: 1fr; } }
</style>
