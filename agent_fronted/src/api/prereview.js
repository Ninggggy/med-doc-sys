import http from "./http";

export function createProject(data) {
  return http.post("/pre-review/projects", data);
}

export function deleteProject(projectId) {
  return http.post(`/pre-review/projects/${projectId}/delete`, {});
}

export function batchDeleteProjects(data) {
  return http.post("/pre-review/projects/batch-delete", data || {});
}

export function listProjects(params, options = {}) {
  return http.post(
    "/pre-review/projects/list",
    params || {},
    {
      silentError: options.silentError === true,
      timeout: Number(options.timeout || 190000),
    },
  );
}

export function projectDetail(projectId) {
  return http.post(`/pre-review/projects/${projectId}/detail`, {});
}

export function uploadSubmission(projectId, formData) {
  return http.post(`/pre-review/projects/${projectId}/submissions/upload`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function uploadSubmissionAsync(projectId, formData) {
  return http.post(`/pre-review/projects/${projectId}/submissions/async-upload`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function listSubmissions(projectId, params) {
  return http.post(`/pre-review/projects/${projectId}/submissions/list`, params || {});
}

export function ctdCatalog(projectId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/ctd-catalog`,
    {},
    { silentError: options.silentError === true },
  );
}

export function globalCtdCatalog() {
  return http.post("/pre-review/sections/catalog", {});
}

export function globalSectionRules(sectionId) {
  return http.post(`/pre-review/sections/${sectionId}/rules`, {});
}

export function filteredGlobalSectionRules(sectionId, data, options = {}) {
  return http.post(
    `/pre-review/sections/${sectionId}/rules`,
    data || {},
    {
      silentError: options.silentError === true,
      timeout: Number(options.timeout || 190000),
    },
  );
}

export function saveGlobalSectionRules(sectionId, data) {
  return http.post(`/pre-review/sections/${sectionId}/rules/save`, data || {});
}

export function saveGlobalSeedSectionRules(sectionId, data) {
  return http.post(`/pre-review/sections/${sectionId}/seed-rules/save`, data || {});
}

export function deleteGlobalSeedSectionRules(sectionId) {
  return http.post(`/pre-review/sections/${sectionId}/seed-rules/delete`, {});
}

export function deleteGlobalSeedSectionRule(sectionId, ruleCode) {
  return http.post(`/pre-review/sections/${sectionId}/seed-rules/${encodeURIComponent(ruleCode)}/delete`, {});
}

export function deleteGlobalSectionRule(sectionId, ruleCode) {
  return http.post(`/pre-review/sections/${sectionId}/rules/${encodeURIComponent(ruleCode)}/delete`, {});
}

export function batchDeleteGlobalSectionRules(sectionId, data) {
  return http.post(`/pre-review/sections/${sectionId}/rules/batch-delete`, data || {});
}

export function deleteGlobalSectionRulesByScope(data) {
  return http.post("/pre-review/sections/rules/delete-by-scope", data || {});
}

export function previewDeleteGlobalSectionRulesByScope(data) {
  return http.post("/pre-review/sections/rules/delete-by-scope/preview", data || {});
}

export function importGlobalSectionRules(data) {
  if (data instanceof FormData) {
    return http.post("/pre-review/sections/rules/import-json", data, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  }
  return http.post("/pre-review/sections/rules/import-json", data || {});
}

export function sectionRules(projectId, sectionId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/sections/${sectionId}/rules`,
    {},
    { silentError: options.silentError !== false },
  );
}

export function saveSectionRules(projectId, sectionId, data) {
  return http.post(`/pre-review/projects/${projectId}/sections/${sectionId}/rules/save`, data || {});
}

export function sectionExamples(projectId, sectionId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/sections/${sectionId}/examples`,
    {},
    { silentError: options.silentError !== false },
  );
}

export function sectionPromptRules(projectId, sectionId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/sections/${sectionId}/prompt-rules`,
    {},
    { silentError: options.silentError !== false },
  );
}

export function sectionExperienceMemory(projectId, sectionId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/sections/${sectionId}/experience-memory`,
    {},
    { silentError: options.silentError !== false },
  );
}

export function submissionContent(projectId, docId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/content`,
    { compact: options.compact !== false },
    {
      silentError: options.silentError !== false,
      timeout: Number(options.timeout || 190000),
    },
  );
}

export function saveSubmissionContent(projectId, docId, data, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/content/save`,
    data || {},
    { silentError: options.silentError !== false },
  );
}

export function submissionSections(projectId, docId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/sections`,
    { compact: options.compact !== false },
    {
      silentError: options.silentError === true,
      timeout: Number(options.timeout || 190000),
    },
  );
}

export function submissionSectionDiagnostics(projectId, docId, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/section-diagnostics`,
    {},
    {
      silentError: options.silentError !== false,
      timeout: Number(options.timeout || 190000),
    },
  );
}

export function submissionPreviewUrl(projectId, docId) {
  return `/api/pre-review/projects/${projectId}/submissions/${docId}/preview`;
}

export function dashboardSummary() {
  return http.post("/pre-review/dashboard", {});
}

export function runPreReview(data) {
  return http.post("/pre-review/runs", data);
}

export function startPreReviewTask(data, options = {}) {
  return http.post(
    "/pre-review/runs/async-start",
    data,
    { silentError: options.silentError !== false },
  );
}

export function getPreReviewTaskProgress(taskId, params, options = {}) {
  return http.get(`/pre-review/runs/tasks/${taskId}/progress`, {
    params: params || {},
    silentError: options.silentError === true,
  });
}

export function getLatestPreReviewMainTask(projectId, params = {}, options = {}) {
  return http.get(`/pre-review/projects/${projectId}/runs/tasks/latest`, {
    params: {
      include_terminal: params.includeTerminal === false ? 0 : 1,
    },
    silentError: options.silentError !== false,
  });
}

export function runHistory(params, options = {}) {
  return http.post(
    "/pre-review/runs/history",
    params || {},
    { silentError: options.silentError === true },
  );
}

export function sectionConclusions(runId, params) {
  return http.post(`/pre-review/runs/${runId}/sections`, params || {});
}

export function sectionOverview(runId, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/sections/overview`,
    { compact: options.compact !== false },
    {
      silentError: options.silentError === true,
      timeout: Number(options.timeout || 190000),
    },
  );
}

export function sectionTraces(runId, params, options = {}) {
  const payload = { ...(params || {}) };
  if (!Object.prototype.hasOwnProperty.call(payload, "compact")) {
    payload.compact = true;
  }
  return http.post(
    `/pre-review/runs/${runId}/traces`,
    payload,
    {
      silentError: options.silentError === true,
      timeout: Number(options.timeout || 190000),
    },
  );
}

export function executionAudits(runId, params, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/audits`,
    params || {},
    { silentError: options.silentError !== false },
  );
}

export function sectionTraceArtifact(runId, sectionId, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/traces/${sectionId}/artifact`,
    {},
    { silentError: options.silentError !== false },
  );
}

export function sectionPatchCandidates(runId, params, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/patches`,
    params || {},
    { silentError: options.silentError === true },
  );
}

export function replaySection(projectId, docId, sectionId, data) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/sections/${sectionId}/replay`,
    data || {}
  );
}

export function startSectionReplayTask(projectId, docId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/sections/${sectionId}/async-replay`,
    data || {},
    { silentError: options.silentError !== false },
  );
}

export function replayModule(projectId, docId, sectionId, data) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/sections/${sectionId}/module-replay`,
    data || {}
  );
}

export function startModuleReplayTask(projectId, docId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/projects/${projectId}/submissions/${docId}/sections/${sectionId}/async-module-replay`,
    data || {},
    { silentError: options.silentError !== false },
  );
}

export function exportReport(runId) {
  return http.post(`/pre-review/runs/${runId}/export`);
}

export function exportReviewConclusions(runId) {
  return http.post(`/pre-review/runs/${runId}/review-conclusions/export`);
}

export function addFeedback(runId, data) {
  return http.post(`/pre-review/runs/${runId}/feedback`, data);
}

export function optimizeFeedback(runId, data) {
  return http.post(`/pre-review/runs/${runId}/feedback/optimize`, data || {});
}

export function optimizeFeedbackAsync(runId, data, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/feedback/optimize/async`,
    data || {},
    { silentError: options.silentError === true },
  );
}

export function optimizeP52Feedback(runId, sectionId, data) {
  return http.post(`/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/optimize`, data || {});
}

export function optimizeP52FeedbackAsync(runId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/optimize/async`,
    data || {},
    { silentError: options.silentError === true },
  );
}

export function listP52FeedbackPatches(runId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/patches`,
    data || {},
    { silentError: options.silentError !== false },
  );
}

export function replayVerifyP52Feedback(runId, sectionId, data) {
  return http.post(`/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/replay-verify`, data || {});
}

export function replayVerifyP52FeedbackAsync(runId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/replay-verify/async`,
    data || {},
    { silentError: options.silentError === true },
  );
}

export function replayP52MetaReflection(runId, sectionId, data) {
  return http.post(`/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/meta-reflection`, data || {});
}

export function replayP52MetaReflectionAsync(runId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/meta-reflection/async`,
    data || {},
    { silentError: options.silentError === true },
  );
}

export function runP52AblationStudy(runId, sectionId, data) {
  return http.post(`/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/ablation`, data || {});
}

export function approveP52FeedbackPatch(runId, sectionId, patchId, data) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/patches/${patchId}/approve`,
    data || {}
  );
}

export function rejectP52FeedbackPatch(runId, sectionId, patchId, data) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/p52-feedback/patches/${patchId}/reject`,
    data || {}
  );
}

export function evaluateOptimizeFeedback(runId, feedbackKey, data) {
  return http.post(`/pre-review/runs/${runId}/feedback/${feedbackKey}/evaluate`, data || {});
}

export function feedbackStats(runId, params, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/feedback/stats`,
    params || {},
    { silentError: options.silentError !== false },
  );
}

export function replayFeedbackOptimize(runId, sectionId, data) {
  return http.post(`/pre-review/runs/${runId}/sections/${sectionId}/replay/feedback-optimize`, data || {});
}

export function replayFeedbackOptimizeAsync(runId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/replay/feedback-optimize/async`,
    data || {},
    { silentError: options.silentError === true },
  );
}

export function replayMetaReflection(runId, sectionId, data) {
  return http.post(`/pre-review/runs/${runId}/sections/${sectionId}/replay/meta-reflection`, data || {});
}

export function replayMetaReflectionAsync(runId, sectionId, data, options = {}) {
  return http.post(
    `/pre-review/runs/${runId}/sections/${sectionId}/replay/meta-reflection/async`,
    data || {},
    { silentError: options.silentError === true },
  );
}

export function generateRegressionCases(data) {
  return http.post("/pre-review/feedback/evaluation/regression-cases", data || {});
}

export function replayEvaluationCases(data) {
  return http.post("/pre-review/feedback/evaluation/replay-cases", data || {});
}

export function replayEvaluationCasesAsync(data, options = {}) {
  return http.post(
    "/pre-review/feedback/evaluation/replay-cases/async",
    data || {},
    { silentError: options.silentError === true },
  );
}

export function runAblationStudy(data) {
  return http.post("/pre-review/feedback/evaluation/ablation", data || {});
}

export function runAblationStudyAsync(data, options = {}) {
  return http.post(
    "/pre-review/feedback/evaluation/ablation/async",
    data || {},
    { silentError: options.silentError === true },
  );
}
