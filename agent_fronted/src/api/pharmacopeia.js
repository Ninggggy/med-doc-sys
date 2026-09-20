import http from "./http";

export function listPharmacopeiaEntries(params, options = {}) {
  return http.post(
    "/pharmacopeia/list",
    params || {},
    { silentError: options.silentError === true },
  );
}

export function createPharmacopeiaEntry(data) {
  return http.post("/pharmacopeia", data || {});
}

export function updatePharmacopeiaEntry(entryId, data) {
  return http.post(`/pharmacopeia/${entryId}`, data || {});
}

export function deletePharmacopeiaEntry(entryId) {
  return http.post(`/pharmacopeia/${entryId}/delete`, {});
}

export function importPharmacopeiaJson(formData) {
  return http.post("/pharmacopeia/import-json", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}
