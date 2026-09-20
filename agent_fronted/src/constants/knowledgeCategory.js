import { PRE_REVIEW_REGISTRATION_OPTIONS } from "@/constants/registration";

export const EXPERIENCE_TYPE_OPTIONS = [
  "成功经验",
  "经验教训",
];

export const KNOWLEDGE_AFFECT_RANGE_OPTIONS = [
  "中药",
  "化学药",
  "生物制品",
];

export const KNOWLEDGE_PROFESSION_OPTIONS = [
  "药学",
  "临床",
  "非临床",
  "临床药理",
  "统计分析",
  "多学科",
];

export const ICH_PROFESSION_OPTIONS = Array.from({ length: 14 }, (_, index) => `Q${index + 1}`);

export const KNOWLEDGE_CATEGORY_META = {
  "指导原则": {
    mode: "guideline",
    displayName: "指导原则",
    queryFields: ["affect_range", "profession_classification"],
    uploadFields: ["affect_range", "profession_classification"],
  },
  "审评规则": {
    mode: "review_rule",
    displayName: "审评准则",
    queryFields: ["affect_range"],
    uploadFields: ["affect_range"],
  },
  "历史经验": {
    mode: "experience",
    displayName: "历史经验",
    queryFields: ["affect_range", "profession_classification", "experience_type"],
    uploadFields: ["affect_range", "profession_classification", "experience_type"],
  },
  ICH: {
    mode: "ich",
    displayName: "ICH",
    queryFields: ["affect_range", "profession_classification"],
    uploadFields: ["affect_range", "profession_classification"],
    affectRangeOptions: ["药学"],
    professionOptions: ICH_PROFESSION_OPTIONS,
  },
};

export const KNOWLEDGE_FORM_OPTIONS = {
  affect_range: KNOWLEDGE_AFFECT_RANGE_OPTIONS,
  profession_classification: KNOWLEDGE_PROFESSION_OPTIONS,
  experience_type: EXPERIENCE_TYPE_OPTIONS,
};

export function getKnowledgeCategoryMeta(classification) {
  return (
    KNOWLEDGE_CATEGORY_META[classification] || {
      mode: "default",
      displayName: classification || "",
      queryFields: [],
      uploadFields: [],
      affectRangeOptions: KNOWLEDGE_AFFECT_RANGE_OPTIONS,
      professionOptions: KNOWLEDGE_PROFESSION_OPTIONS,
    }
  );
}

export function getRegistrationOptionsByScope(scope) {
  if (!scope) {
    return [];
  }
  return PRE_REVIEW_REGISTRATION_OPTIONS.filter((item) => item.value === scope);
}
