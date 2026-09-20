import http from "./http";

export function getFilingParseReadiness(projectId) {
  return http.get(`/filing-change-review/projects/${encodeURIComponent(projectId)}/review/readiness`, { silentError: true });
}
function parseReviewUrl(projectId, kind, docId) {
  return `/filing-change-review/projects/${encodeURIComponent(projectId)}/parse-review/${encodeURIComponent(kind)}/${encodeURIComponent(docId)}`;
}
export function getFilingParseReview(projectId, kind, docId) {
  return http.get(parseReviewUrl(projectId, kind, docId), { silentError: true });
}
export function saveFilingParseReview(projectId, kind, docId, payload) {
  return http.post(parseReviewUrl(projectId, kind, docId), payload, { silentError: true });
}
export function getFilingParseReviewPage(projectId, kind, docId, page, identity) {
  return http.post(`${parseReviewUrl(projectId, kind, docId)}/page/${page}`, { source_identity: identity }, { silentError: true });
}

export function getFilingProjectParseTasks(projectId, options = {}) {
  return http.get(`/filing-change-review/projects/${projectId}/parse-tasks`, { silentError: options.silentError === true });
}

export function createFilingChangeProject(data) {
  return http.post("/filing-change-review/projects", data || {});
}

export function listFilingChangeProjects(params, options = {}) {
  return http.post(
    "/filing-change-review/projects/list",
    params || {},
    { silentError: options.silentError === true },
  );
}

export function deleteFilingChangeProject(projectId) {
  return http.post(`/filing-change-review/projects/${projectId}/delete`, {});
}

export function getFilingChangeProjectDetail(projectId, options = {}) {
  return http.get(`/filing-change-review/projects/${projectId}/detail`, { silentError: options.silentError === true });
}

export function saveApplicationForm(projectId, data, options = {}) {
  return http.post(`/filing-change-review/projects/${projectId}/application-form/save`, data || {}, { silentError: options.silentError === true });
}

export function getApplicationForm(projectId, options = {}) {
  return http.get(`/filing-change-review/projects/${projectId}/application-form`, { silentError: options.silentError === true });
}

export function importApplicationForm(projectId, formData, options = {}) {
  return http.post(`/filing-change-review/projects/${projectId}/application-form/import`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
    silentError: options.silentError === true,
  });
}

export function importApplicationFormWord(projectId, formData) {
  return importApplicationForm(projectId, formData);
}

export function startApplicationFormImport(projectId, formData, options = {}) {
  return http.post(
    `/filing-change-review/projects/${projectId}/application-form/import/start`,
    formData,
    {
      headers: { "Content-Type": "multipart/form-data" },
      silentError: options.silentError === true,
    },
  );
}

export function parseApplicationForm(projectId) {
  return http.post(`/filing-change-review/projects/${projectId}/application-form/parse`, {});
}

export function startApplicationFormParse(projectId, options = {}) {
  return http.post(
    `/filing-change-review/projects/${projectId}/application-form/parse/start`,
    {},
    { silentError: options.silentError === true },
  );
}

export function uploadFilingSubmissions(projectId, formData) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/upload`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function listFilingSubmissions(projectId, params, options = {}) {
  return http.post(
    `/filing-change-review/projects/${projectId}/submissions/list`,
    params || {},
    { silentError: options.silentError === true },
  );
}

export function deleteFilingSubmission(projectId, docId) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/${docId}/delete`, {});
}

export function parseFilingSubmission(projectId, docId) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/${docId}/parse`, {});
}

export function startFilingSubmissionParse(projectId, docId, options = {}) {
  return http.post(
    `/filing-change-review/projects/${projectId}/submissions/${docId}/parse/start`,
    {},
    { silentError: options.silentError === true },
  );
}

export function batchParseFilingSubmissions(projectId, data) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/parse-batch`, data || {});
}

export function startBatchFilingSubmissionsParse(projectId, data, options = {}) {
  return http.post(
    `/filing-change-review/projects/${projectId}/submissions/parse-batch/start`,
    data || {},
    { silentError: options.silentError === true },
  );
}

export function getFilingSubmissionParsedMarkdown(projectId, docId, options = {}) {
  return http.get(
    `/filing-change-review/projects/${projectId}/submissions/${docId}/parsed-markdown`,
    { silentError: options.silentError === true },
  );
}

export async function downloadFilingSubmissionOriginal(projectId, docId) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), http.defaults.timeout);
  try {
    const response = await fetch(`/api/filing-change-review/projects/${encodeURIComponent(projectId)}/submissions/${encodeURIComponent(docId)}/original`, {
      credentials: "same-origin", signal: controller.signal,
    });
    if (!response.ok) throw new Error(`原件下载失败（HTTP ${response.status}），请确认资料仍存在后重试`);
    if (!(response.headers.get("content-disposition") || "").toLowerCase().includes("attachment")) {
      throw new Error("服务端未返回原件附件，请刷新后重试");
    }
    return await response.blob();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("原件下载超时，请重试");
    throw error;
  } finally { clearTimeout(timer); }
}

export function updateFilingSubmissionMetadata(projectId, docId, data) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/${docId}/metadata`, data || {});
}

export function confirmFilingSubmissionNumbers(projectId, docId, data) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/${docId}/numeric-confirmations`, data, { silentError: true });
}

export function setFilingSubmissionCategoryNotApplicable(projectId, data) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/category/not-applicable`, data || {});
}

export function getFilingSubmissionCatalog(projectId, options = {}) {
  return http.get(`/filing-change-review/projects/${projectId}/submissions/catalog`, { silentError: options.silentError === true });
}

export function checkFilingSubmissionCompleteness(projectId) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/completeness-check`, {});
}

export function compareFilingSubmissions(projectId, data) {
  return http.post(`/filing-change-review/projects/${projectId}/submissions/compare`, data || {});
}

export function startFilingReview(projectId) {
  return http.post(`/filing-change-review/projects/${projectId}/review/start`, {});
}

export function startFilingAIReview(projectId, options = {}) {
  return http.post(`/filing-change-review/projects/${projectId}/ai-review/start`, {}, { silentError: options.silentError === true });
}

export function getFilingReviewTaskProgress(taskId) {
  return http.get(`/filing-change-review/review/tasks/${taskId}/progress`);
}

export function getFilingAIReviewTaskProgress(taskId) {
  return http.get(`/filing-change-review/ai-review/tasks/${taskId}/progress`);
}

export function listFilingReviewHistory(projectId, options = {}) {
  return http.post(`/filing-change-review/projects/${projectId}/review/history`, {}, { silentError: options.silentError === true });
}

export function getFilingRunResult(runId, options = {}) {
  return http.get(`/filing-change-review/runs/${runId}/result`, { silentError: options.silentError === true });
}

export function getFilingRunEvidence(runId) {
  return http.get(`/filing-change-review/runs/${runId}/evidence`);
}

export function generateFilingCorrectionNotice(runId) {
  return http.post(`/filing-change-review/runs/${runId}/correction-notice/generate`, {});
}

export function manualConfirmFilingRun(runId, data) {
  return http.post(`/filing-change-review/runs/${runId}/manual-confirm`, data || {});
}

export function filingReportWordExportUrl(reportId) {
  return `/api/filing-change-review/reports/${reportId}/export-word`;
}

export async function downloadFilingReportWord(reportId, runId) {
  // 保留同源会话；拿到完整二进制响应后才能确认服务端导出准备完成。
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), http.defaults.timeout);
  try {
    const response = await fetch(filingReportWordExportUrl(reportId), {
      credentials: "same-origin", signal: controller.signal,
    });
    const blob = await response.blob();
    const type = (response.headers.get("content-type") || "").toLowerCase();
    if (!response.ok || type.includes("json")) {
      let message = `Word导出失败（HTTP ${response.status}）`;
      try { message = JSON.parse(await blob.text()).message || message; } catch (_) { /* 非JSON错误页 */ }
      throw new Error(message);
    }
    const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
    if (!blob.size || signature.join(",") !== "80,75,3,4") throw new Error("服务端未返回有效的Word文件，请重试或核对报告");
    const disposition = response.headers.get("content-disposition") || "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plain = disposition.match(/filename="([^"]+)"|filename=([^;]+)/i);
    let filename = `${runId}.docx`;
    try { filename = encoded ? decodeURIComponent(encoded[1]) : plain ? (plain[1] || plain[2]).trim() : filename; } catch (_) { /* 使用本轮文件名 */ }
    filename = filename.split(/[\\/]/).pop() || `${runId}.docx`;
    return { blob, filename };
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Word导出请求超时，请重新读取报告后重试");
    throw error;
  } finally { clearTimeout(timer); }
}

export function getFilingProjectReport(projectId, runId = "", options = {}) {
  return http.get(`/filing-change-review/projects/${projectId}/report`, { params: { run_id: runId }, silentError: options.silentError === true });
}

export function uploadFilingReferenceMaterials(formData) {
  return http.post("/filing-change-review/reference-materials/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function listFilingReferenceMaterials(params, options = {}) {
  return http.post(
    "/filing-change-review/reference-materials/list",
    params || {},
    { silentError: options.silentError === true },
  );
}

export function deleteFilingReferenceMaterial(docId) {
  return http.post(`/filing-change-review/reference-materials/${docId}/delete`, {});
}

export function parseFilingReferenceMaterial(docId) {
  return http.post(`/filing-change-review/reference-materials/${docId}/parse`, {});
}

export function startFilingReferenceMaterialParse(docId, options = {}) {
  return http.post(
    `/filing-change-review/reference-materials/${docId}/parse/start`,
    {},
    { silentError: options.silentError === true },
  );
}

export function getFilingParseTaskProgress(taskId, params = {}, options = {}) {
  return http.get(
    `/filing-change-review/tasks/${taskId}/progress`,
    {
      params: params || {},
      silentError: options.silentError === true,
    },
  );
}

export function updateFilingReferenceMaterialMetadata(docId, data) {
  return http.post(`/filing-change-review/reference-materials/${docId}/metadata`, data || {});
}

export function getFilingReferenceParsedMarkdown(docId, options = {}) {
  return http.get(
    `/filing-change-review/reference-materials/${docId}/parsed-markdown`,
    { silentError: options.silentError === true },
  );
}

export function getFilingReferenceTaxonomy(options = {}) {
  return http.get("/filing-change-review/reference-materials/taxonomy", { silentError: options.silentError === true });
}

export function createFilingRule(data) {
  return http.post("/filing-change-review/rules", data || {});
}

export function listFilingRules(params, options = {}) {
  return http.post(
    "/filing-change-review/rules/list",
    params || {},
    { silentError: options.silentError === true },
  );
}

export function importFilingRules(formData) {
  return http.post("/filing-change-review/rules/import", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function updateFilingRule(ruleId, data) {
  return http.post(`/filing-change-review/rules/${ruleId}`, data || {});
}

export function deleteFilingRule(ruleId) {
  return http.post(`/filing-change-review/rules/${ruleId}/delete`, {});
}

export function generateFilingRunReport(runId) {
  return http.post(`/filing-change-review/runs/${runId}/report/generate`);
}
