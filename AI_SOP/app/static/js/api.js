async function request(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) {
    const error = new Error(payload.error?.message || "요청을 처리하지 못했습니다.");
    error.code = payload.error?.code;
    error.details = payload.error?.details;
    throw error;
  }
  return payload.data;
}

export const api = {
  status: () => request("/api/status"),
  session: () => request("/api/session", { method: "POST" }),
  drafts: () => request("/api/drafts"),
  draft: draftId => request(`/api/drafts/${draftId}`),
  createDraft: description => request("/api/drafts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ description }) }),
  upload: (draftId, file) => { const body = new FormData(); body.append("file", file); return request(`/api/drafts/${draftId}/sources`, { method: "POST", body }); },
  questions: draftId => request(`/api/drafts/${draftId}/questions`, { method: "POST" }),
  message: (draftId, content, questionIndex = null) => request(`/api/drafts/${draftId}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content, questionIndex }) }),
  generate: draftId => request(`/api/drafts/${draftId}/generate`, { method: "POST" }),
  revise: (draftId, ir) => request(`/api/drafts/${draftId}/revise`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ir }) }),
  approve: (draftId, targetVisibility, confirmed, sensitiveContentReviewed) => request(`/api/drafts/${draftId}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ targetVisibility, confirmed, sensitiveContentReviewed }) }),
  wiki: () => request("/api/wiki"),
  wikiDetail: documentId => request(`/api/wiki/${documentId}`),
};
