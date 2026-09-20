export function formatReviewConfidence(value) {
  if (value == null || value === "") {
    return "-";
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    const label = String(value.label || value.level || "").trim();
    const numeric = Number(value.score ?? value.value ?? value.confidence);
    if (label && Number.isFinite(numeric) && numeric >= 0 && numeric <= 1) {
      return `${label} (${(numeric * 100).toFixed(1)}%)`;
    }
    if (label) {
      return label;
    }
    if (Number.isFinite(numeric) && numeric >= 0 && numeric <= 1) {
      return `${(numeric * 100).toFixed(1)}%`;
    }
    return "-";
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric >= 0 && numeric <= 1) {
    return `${(numeric * 100).toFixed(1)}%`;
  }
  return String(value);
}

export function extractFactBasisPreview(factBasis, limit = 3) {
  if (Array.isArray(factBasis)) {
    return factBasis
      .map((item) => String(item || "").trim())
      .filter(Boolean)
      .slice(0, limit)
      .join("；");
  }
  if (!factBasis || typeof factBasis !== "object") {
    return "";
  }
  const values = [
    ...(Array.isArray(factBasis.explicit_in_text) ? factBasis.explicit_in_text : []),
    ...(Array.isArray(factBasis.inferred_from_evidence) ? factBasis.inferred_from_evidence : []),
    ...(Array.isArray(factBasis.supported_by_evidence) ? factBasis.supported_by_evidence : []),
    ...(Array.isArray(factBasis.experience_warning) ? factBasis.experience_warning : []),
  ]
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  return [...new Set(values)].slice(0, limit).join("；");
}
