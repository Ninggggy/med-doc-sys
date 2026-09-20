import http from "./http";

export function uploadKnowledge(formData) {
  return http.post("/knowledge/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function queryKnowledge(params) {
  return http.post("/knowledge/query", params || {});
}

export function semanticQuery(params, options = {}) {
  return http.post(
    "/knowledge/semantic-query",
    params || {},
    { silentError: options.silentError === true },
  );
}

export function updateKnowledge(docId, data) {
  return http.post(`/knowledge/${docId}`, data || {});
}

export function deleteKnowledge(docId) {
  return http.post(`/knowledge/${docId}/delete`, {});
}

export function listDeletionCleanups(params) {
  return http.post("/files/deletion-cleanups", params || {});
}

export function batchDeleteKnowledge(docIds) {
  return http.post("/knowledge/batch-delete", { doc_ids: Array.isArray(docIds) ? docIds : [] });
}

export function parseKnowledge(docId) {
  return http.post(`/knowledge/${docId}/parse`, {});
}

// CDE 指导原则同步相关 API

export function fetchCDEGuidelines(params) {
  return http.post("/knowledge/cde-guidelines/fetch", params || {});
}

export function compareCDEGuidelines(params) {
  return http.post("/knowledge/cde-guidelines/compare", params || {});
}

export function syncCDEGuidelines(params) {
  return http.post("/knowledge/cde-guidelines/sync", params || {});
}

export function getCESyncHistory() {
  return http.get("/knowledge/cde-guidelines/sync/history");
}
