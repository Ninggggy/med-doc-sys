function isSupplementSectionId(value) {
  const sid = String(value || "").trim();
  return sid === "supplement" || sid.startsWith("supplement.");
}

function stripSupplementPrefix(value) {
  return String(value || "")
    .replace(/^supplement(?:\.[A-Za-z0-9_]+)*\s*/i, "")
    .trim();
}

export function stripSectionDisplayName(value) {
  const raw = String(value || "");
  const withoutSupplementPrefix = stripSupplementPrefix(raw);
  return withoutSupplementPrefix
    .replace(/\s*[（(][^()（）]*[）)]\s*/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

export function buildSectionDisplayLabel(sectionId, sectionName) {
  const sid = String(sectionId || "").trim();
  const name = stripSectionDisplayName(sectionName);
  if (isSupplementSectionId(sid)) {
    return name || "药品补充申请";
  }
  return [sid, name].filter(Boolean).join(" ");
}
