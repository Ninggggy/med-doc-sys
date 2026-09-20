export const KNOWLEDGE_STRUCTURED_CATEGORIES = ["指导原则", "历史经验", "审评规则"];

export const KNOWLEDGE_AFFECT_RANGE_OPTIONS = ["中药", "化药", "生物制品"];

export const KNOWLEDGE_PROFESSION_OPTIONS = ["药学", "临床", "非临床", "临床药理", "生物统计", "多学科"];

export const SUBMISSION_MATERIAL_CATEGORIES = ["药学", "临床", "非临床"];

export const PRE_REVIEW_REGISTRATION_OPTIONS = [
  {
    value: "中药",
    label: "中药",
    description: "中药是指在我国中医药理论指导下使用的药用物质及其制剂。",
    children: [
      {
        value: "中药创新药",
        label: "1. 中药创新药",
        description: "处方未在国家药品标准、药品注册标准及古代经典名方目录中收载，具有临床价值且未在境外上市的中药新处方制剂。",
        children: [
          { value: "1.1 中药复方制剂", label: "1.1 中药复方制剂", description: "由多味饮片、提取物等在中医药理论指导下组方而成的制剂。" },
          { value: "1.2 单一来源提取物及其制剂", label: "1.2 单一来源提取物及其制剂", description: "从单一植物、动物、矿物等物质中提取得到的提取物及其制剂。" },
          { value: "1.3 新药材及其制剂", label: "1.3 新药材及其制剂", description: "未被国家药品标准、注册标准及地方药材标准收载的药材及其制剂，以及新的药用部位及其制剂。" }
        ]
      },
      {
        value: "中药改良型新药",
        label: "2. 中药改良型新药",
        description: "改变已上市中药的给药途径、剂型，或增加功能主治等，且具有临床应用优势和特点。",
        children: [
          { value: "2.1 改变给药途径", label: "2.1 改变给药途径", description: "不同给药途径或不同吸收部位之间相互改变的制剂。" },
          { value: "2.2 改变剂型", label: "2.2 改变剂型", description: "在给药途径不变的情况下改变剂型的制剂。" },
          { value: "2.3 增加功能主治", label: "2.3 增加功能主治", description: "已上市中药增加功能主治。" },
          { value: "2.4 工艺或辅料改变", label: "2.4 工艺或辅料改变", description: "生产工艺或辅料改变引起药用物质基础或药物吸收、利用明显改变。" }
        ]
      },
      {
        value: "古代经典名方中药复方制剂",
        label: "3. 古代经典名方中药复方制剂",
        description: "来源于古代经典名方的中药复方制剂。",
        children: [
          { value: "3.1 经典名方目录管理制剂", label: "3.1 经典名方目录管理制剂", description: "按古代经典名方目录管理的中药复方制剂。" },
          { value: "3.2 其他经典名方制剂", label: "3.2 其他经典名方制剂", description: "包括未按目录管理的经典名方制剂及基于经典名方加减化裁的中药复方制剂。" }
        ]
      },
      { value: "同名同方药", label: "4. 同名同方药", description: "通用名称、处方、剂型、功能主治、用法及日用饮片量与已上市中药相同，且综合水平不低于已上市中药的制剂。" },
      { value: "天然药物参照中药注册分类", label: "天然药物参照中药注册分类", description: "天然药物在现代医药理论指导下使用，注册分类参照中药。" },
      { value: "其他情形", label: "其他情形", description: "主要包括境外已上市、境内未上市的中药或天然药物制剂。" }
    ]
  },
  {
    value: "化药",
    label: "化药",
    description: "化学药品注册分类分为创新药、改良型新药、仿制药、境外已上市境内未上市化学药品。",
    children: [
      { value: "1类 创新药", label: "1类 创新药", description: "境内外均未上市的创新药，含新的结构明确、具有药理作用且具有临床价值的化合物。" },
      {
        value: "2类 改良型新药",
        label: "2类 改良型新药",
        description: "在已知活性成份基础上，对结构、剂型、工艺、给药途径、适应症等进行优化，且具有明显临床优势。",
        children: [
          { value: "2.1 异构体/成酯/成盐等优化", label: "2.1 异构体/成酯/成盐等优化", description: "已知活性成份的光学异构体、成酯、成盐或其他非共价衍生物优化。" },
          { value: "2.2 新剂型/新工艺/新给药途径", label: "2.2 新剂型/新工艺/新给药途径", description: "含有已知活性成份的新剂型、新处方工艺、新给药途径。" },
          { value: "2.3 新复方制剂", label: "2.3 新复方制剂", description: "含有已知活性成份的新复方制剂。" },
          { value: "2.4 新适应症", label: "2.4 新适应症", description: "含有已知活性成份的新适应症药品。" }
        ]
      },
      { value: "3类 境外上市境内未上市原研仿制", label: "3类 境外上市境内未上市原研仿制", description: "境内申请人仿制境外上市但境内未上市原研药品，应与参比制剂质量和疗效一致。" },
      { value: "4类 境内已上市原研仿制", label: "4类 境内已上市原研仿制", description: "境内申请人仿制已在境内上市原研药品，应与参比制剂质量和疗效一致。" },
      {
        value: "5类 境外已上市药品境内上市",
        label: "5类 境外已上市药品境内上市",
        description: "境外上市药品申请在境内上市。",
        children: [
          { value: "5.1 原研/改良型药品", label: "5.1 原研/改良型药品", description: "境外上市的原研药品和改良型药品申请在境内上市。" },
          { value: "5.2 境外上市仿制药", label: "5.2 境外上市仿制药", description: "境外上市的仿制药申请在境内上市。" }
        ]
      }
    ]
  },
  {
    value: "生物制品",
    label: "生物制品",
    description: "生物制品注册分类包括预防用生物制品、治疗用生物制品和按生物制品管理的体外诊断试剂。",
    children: [
      {
        value: "预防用生物制品",
        label: "第一部分 预防用生物制品",
        description: "预防用生物制品注册分类。",
        children: [
          {
            value: "1类 创新型疫苗",
            label: "1类 创新型疫苗",
            description: "境内外均未上市的疫苗。",
            children: [
              { value: "1.1 无有效预防手段疾病疫苗", label: "1.1 无有效预防手段疾病疫苗", description: "针对无有效预防手段疾病的疫苗。" },
              { value: "1.2 新抗原形式疫苗", label: "1.2 新抗原形式疫苗", description: "如新基因重组疫苗、新核酸疫苗、新结合疫苗等。" },
              { value: "1.3 含新佐剂或佐剂系统", label: "1.3 含新佐剂或佐剂系统", description: "含新佐剂或新佐剂系统的疫苗。" },
              { value: "1.4 新抗原多联/多价疫苗", label: "1.4 新抗原多联/多价疫苗", description: "含新抗原或新抗原形式的多联/多价疫苗。" }
            ]
          },
          {
            value: "2类 改良型疫苗",
            label: "2类 改良型疫苗",
            description: "对已上市疫苗产品进行改良，且具有明显优势。",
            children: [
              { value: "2.1 改变抗原谱或型别", label: "2.1 改变抗原谱或型别", description: "在已上市产品基础上改变抗原谱或型别。" },
              { value: "2.2 重大技术改进", label: "2.2 重大技术改进", description: "对菌毒种、细胞基质、生产工艺、剂型等的改进。" },
              { value: "2.3 新的多联/多价疫苗", label: "2.3 新的多联/多价疫苗", description: "已有同类产品上市的疫苗组成新的多联/多价疫苗。" },
              { value: "2.4 改变给药途径", label: "2.4 改变给药途径", description: "改变给药途径且具有明显临床优势。" },
              { value: "2.5 改变免疫剂量或程序", label: "2.5 改变免疫剂量或程序", description: "新的免疫剂量或程序具有明显临床优势。" },
              { value: "2.6 改变适用人群", label: "2.6 改变适用人群", description: "改变适用人群的疫苗。" }
            ]
          },
          {
            value: "3类 已上市疫苗",
            label: "3类 已上市疫苗",
            description: "境内或境外已上市的疫苗。",
            children: [
              { value: "3.1 境外生产境外已上市境内未上市疫苗", label: "3.1 境外生产境外已上市境内未上市疫苗", description: "境外生产的境外已上市、境内未上市疫苗申报上市。" },
              { value: "3.2 境外已上市境内生产上市", label: "3.2 境外已上市境内生产上市", description: "境外已上市、境内未上市疫苗申报在境内生产上市。" },
              { value: "3.3 境内已上市疫苗", label: "3.3 境内已上市疫苗", description: "境内已上市疫苗。" }
            ]
          }
        ]
      },
      {
        value: "治疗用生物制品",
        label: "第二部分 治疗用生物制品",
        description: "治疗用生物制品注册分类。",
        children: [
          { value: "1类 创新型生物制品", label: "1类 创新型生物制品", description: "境内外均未上市的治疗用生物制品。" },
          {
            value: "2类 改良型生物制品",
            label: "2类 改良型生物制品",
            description: "对已上市制品进行改良，且具有明显优势。",
            children: [
              { value: "2.1 优化剂型或给药途径", label: "2.1 优化剂型或给药途径", description: "对剂型、给药途径等进行优化。" },
              { value: "2.2 新适应症或用药人群", label: "2.2 新适应症或用药人群", description: "增加境内外均未获批的新适应症和/或改变用药人群。" },
              { value: "2.3 新复方制品", label: "2.3 新复方制品", description: "已有同类制品上市的生物制品组成新的复方制品。" },
              { value: "2.4 重大技术改进", label: "2.4 重大技术改进", description: "如重组技术替代生物组织提取技术等。" }
            ]
          },
          {
            value: "3类 已上市生物制品",
            label: "3类 已上市生物制品",
            description: "境内或境外已上市生物制品。",
            children: [
              { value: "3.1 境外生产境外已上市境内未上市", label: "3.1 境外生产境外已上市境内未上市", description: "境外生产的境外已上市、境内未上市生物制品申报上市。" },
              { value: "3.2 境外已上市境内生产上市", label: "3.2 境外已上市境内生产上市", description: "境外已上市、境内未上市生物制品申报在境内生产上市。" },
              { value: "3.3 生物类似药", label: "3.3 生物类似药", description: "生物类似药。" },
              { value: "3.4 其他生物制品", label: "3.4 其他生物制品", description: "其他生物制品。" }
            ]
          }
        ]
      },
      {
        value: "按生物制品管理的体外诊断试剂",
        label: "第三部分 按生物制品管理的体外诊断试剂",
        description: "按生物制品管理的体外诊断试剂注册分类。",
        children: [
          { value: "1类 创新型体外诊断试剂", label: "1类 创新型体外诊断试剂", description: "创新型体外诊断试剂。" },
          { value: "2类 境内外已上市体外诊断试剂", label: "2类 境内外已上市体外诊断试剂", description: "境内外已上市的体外诊断试剂。" }
        ]
      }
    ]
  }
];

export function findRegistrationNodes(path = [], options = PRE_REVIEW_REGISTRATION_OPTIONS) {
  const nodes = [];
  let current = options;
  for (const value of path || []) {
    const node = (current || []).find((item) => item.value === value);
    if (!node) {
      break;
    }
    nodes.push(node);
    current = node.children || [];
  }
  return nodes;
}
