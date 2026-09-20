export const REVIEW_DOMAIN = {
  PHARMACY: "pharmacy",
  CLINICAL: "clinical",
  NONCLINICAL: "nonclinical",
};

export const REVIEW_DOMAIN_OPTIONS = [
  { value: REVIEW_DOMAIN.PHARMACY, label: "药学" },
  { value: REVIEW_DOMAIN.CLINICAL, label: "临床" },
  { value: REVIEW_DOMAIN.NONCLINICAL, label: "非临床" },
];

export const PHARMACY_BRANCH_OPTIONS = [
  { value: "3.2", label: "质量（药学）" },
  { value: "3.2.s", label: "原料药" },
  { value: "3.2.p", label: "制剂" },
  { value: "3.2.a", label: "附录" },
  { value: "3.2.r", label: "区域性信息" },
];

export const UPLOAD_MODE_OPTIONS = [
  { value: "zip", label: "上传 ZIP" },
  { value: "single", label: "上传单个 CTD 文件" },
  { value: "section", label: "按章节上传" },
];

export function reviewDomainLabel(value) {
  const item = REVIEW_DOMAIN_OPTIONS.find((option) => option.value === value);
  return item ? item.label : value || "-";
}

export function branchLabel(value) {
  const item = PHARMACY_BRANCH_OPTIONS.find((option) => option.value === value);
  return item ? item.label : value || "-";
}

export function normalizeReviewDomain(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) {
    return REVIEW_DOMAIN.PHARMACY;
  }
  if (["pharmacy", "药学"].includes(raw)) {
    return REVIEW_DOMAIN.PHARMACY;
  }
  if (["clinical", "临床"].includes(raw)) {
    return REVIEW_DOMAIN.CLINICAL;
  }
  if (["nonclinical", "non_clinical", "非临床"].includes(raw)) {
    return REVIEW_DOMAIN.NONCLINICAL;
  }
  return REVIEW_DOMAIN.PHARMACY;
}
