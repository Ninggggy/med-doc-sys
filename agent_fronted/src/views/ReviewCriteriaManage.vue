<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">审评规则</h2>
        <div class="page-desc">
          审评规则按章节统一维护，JSON 导入与手工新增/删除都会直接作用到章节规则资源。
        </div>
      </div>
      <div class="header-actions">
        <el-upload
          action="#"
          :auto-upload="false"
          :show-file-list="false"
          :disabled="!canImportJsonRules"
          :on-change="onImportFileChange"
          accept=".json,application/json"
        >
          <el-button :loading="importingRules">导入 JSON 规则</el-button>
        </el-upload>
      </div>
    </div>

    <el-card class="k-card">
      <div slot="header">规则作用域</div>
      <div class="filter-row">
        <el-select
          v-model="ruleScope.registration_scope"
          clearable
          filterable
          style="width: 160px"
          placeholder="注册大类"
          @change="onRuleScopeRegistrationScopeChange"
        >
          <el-option v-for="item in registrationScopeOptions" :key="item" :label="item" :value="item" />
        </el-select>
        <el-cascader
          v-model="ruleScope.registration_path"
          :options="registrationOptions"
          :props="registrationCascaderProps"
          clearable
          filterable
          style="width: 360px"
          placeholder="注册分类"
          @change="onRuleScopeRegistrationPathChange"
        />
        <el-select
          v-model="selectedModule"
          clearable
          filterable
          style="width: 240px"
          placeholder="选择模块"
          @change="onModuleChange"
        >
          <el-option v-for="item in moduleOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button size="mini" @click="resetRuleScope">清空作用域</el-button>
        <el-button
          size="mini"
          type="danger"
          plain
          :disabled="!canDeleteCurrentScopeRules"
          :loading="deletingScopedRules"
          @click="deleteCurrentScopeRules"
        >
          删除该作用域全部规则
        </el-button>
        <div class="muted">当前作用域：{{ currentRuleScopeSummary }}</div>
      </div>
    </el-card>

    <div class="workspace">
      <el-card class="tree-card">
        <div slot="header" class="tree-header">
          <span>章节目录</span>
          <div class="tree-tools" />
        </div>
        <el-tree
          v-if="moduleTreeData.length"
          ref="ruleTree"
          :data="moduleTreeData"
          node-key="section_id"
          :props="treeProps"
          :highlight-current="true"
          :default-expand-all="false"
          :expand-on-click-node="false"
          @node-click="onSectionSelect"
        />
        <div v-else class="empty-tip">先选择作用域与模块，再绑定对应章节规则。</div>
      </el-card>

      <div class="right-column">
        <el-card class="k-card">
          <div slot="header">章节信息</div>
          <div class="meta-grid">
            <div><strong>当前模块：</strong>{{ selectedModuleLabel }}</div>
            <div><strong>章节 ID：</strong>{{ selectedSectionId || "-" }}</div>
            <div><strong>章节名称：</strong>{{ selectedSectionName || "-" }}</div>
            <div><strong>当前规则总数：</strong>{{ effectiveRuleItems.length }}</div>
            <div><strong>匹配模块：</strong>{{ currentRuleScopeMetadata.module || "-" }}</div>
            <div><strong>注册分类：</strong>{{ currentRuleScopeMetadata.registration_class_sub || currentRuleScopeMetadata.registration_class || "-" }}</div>
          </div>
        </el-card>

        <el-card class="k-card">
          <div slot="header" class="section-header">
            <span>章节审评规则维护</span>
            <div class="header-actions">
              <el-button size="mini" @click="appendManualRule">新增规则</el-button>
              <el-button
                type="primary"
                size="mini"
                :disabled="!selectedSectionId"
                :loading="savingRules"
                @click="saveCurrentSectionRules"
              >
                保存规则
              </el-button>
            </div>
          </div>

          <div v-if="selectedSectionId">
            <div v-if="manualRules.length" class="manual-rule-list">
              <div v-for="(item, index) in manualRules" :key="`manual-rule-${index}`" class="manual-rule-row">
                <el-input
                  v-model="manualRules[index]"
                  type="textarea"
                  :rows="2"
                  placeholder="输入当前章节需要补充的统一审评规则"
                />
                <div class="row-actions">
                  <el-button size="mini" @click="insertManualRule(index)">下方插入</el-button>
                  <el-button size="mini" type="danger" @click="removeManualRule(index)">删除</el-button>
                </div>
              </div>
            </div>
            <div v-else class="empty-tip">当前章节还没有手工维护的规则，可以新增后保存。</div>
          </div>
          <div v-else class="empty-tip">先在左侧章节目录中选择一个章节。</div>
        </el-card>

        <el-card class="k-card">
          <div slot="header">当前生效规则</div>
          <div v-if="loadingRules" class="empty-tip">正在加载规则...</div>
          <div v-else-if="effectiveRuleItems.length" class="effective-rule-list">
            <div class="batch-toolbar">
              <el-checkbox
                :value="allDeletableRulesSelected"
                :indeterminate="deletableRuleSelectionIndeterminate"
                @change="toggleSelectAllDeletableRules"
              >
                选择可删除规则
              </el-checkbox>
              <el-button
                size="mini"
                type="danger"
                plain
                :disabled="!selectedDeletableRuleKeys.length"
                @click="batchDeleteRules"
              >
                批量删除
              </el-button>
            </div>
            <div v-for="item in effectiveRuleItems" :key="item.rule_id || item.rule_code" class="effective-rule-item">
              <div class="effective-rule-head">
                <div class="effective-rule-meta">
                  <el-checkbox
                    v-if="canDeleteRule(item)"
                    :value="selectedDeletableRuleKeys.includes(ruleSelectionKey(item))"
                    @change="toggleRuleSelection(item, $event)"
                  />
                  <el-tag size="mini" :type="sourceTagType(item.source_type)">
                    {{ sourceTypeLabel(item.source_type) }}
                  </el-tag>
                  <span class="rule-code">{{ item.rule_code || "-" }}</span>
                </div>
                <el-button
                  v-if="canDeleteRule(item)"
                  size="mini"
                  type="danger"
                  plain
                  :loading="isDeletingRule(item.rule_code)"
                  @click="deleteRule(item)"
                >
                  删除
                </el-button>
              </div>
              <div class="rule-text">{{ item.rule_text || "-" }}</div>
            </div>
          </div>
          <div v-else class="empty-tip">当前章节还没有生效规则。</div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script>
import {
  batchDeleteGlobalSectionRules,
  deleteGlobalSectionRulesByScope,
  deleteGlobalSectionRule,
  filteredGlobalSectionRules,
  globalCtdCatalog,
  importGlobalSectionRules,
  previewDeleteGlobalSectionRulesByScope,
  saveGlobalSectionRules,
} from "@/api/prereview";
import { PRE_REVIEW_REGISTRATION_OPTIONS } from "@/constants/registration";
import { buildSectionDisplayLabel, stripSectionDisplayName } from "@/utils/ctdDisplay";

function cloneTree(nodes) {
  return Array.isArray(nodes) ? JSON.parse(JSON.stringify(nodes)) : [];
}

export default {
  name: "ReviewCriteriaManage",
  data() {
    return {
      selectedModule: "",
      selectedSectionId: "",
      selectedSectionName: "",
      catalogTree: [],
      loadingRules: false,
      rulesRequestToken: 0,
      savingRules: false,
      importingRules: false,
      deletingRuleCodes: [],
      deletingScopedRules: false,
      manualRules: [],
      effectiveRuleItems: [],
      selectedDeletableRuleKeys: [],
      ruleScope: {
        registration_scope: "",
        registration_path: [],
      },
      treeProps: { children: "children_sections", label: "label" },
      registrationCascaderProps: {
        value: "value",
        label: "label",
        children: "children",
        emitPath: true,
        checkStrictly: false,
      },
    };
  },
  computed: {
    registrationScopeOptions() {
      return PRE_REVIEW_REGISTRATION_OPTIONS.map((item) => item.value);
    },
    registrationOptions() {
      if (!this.ruleScope.registration_scope) {
        return PRE_REVIEW_REGISTRATION_OPTIONS;
      }
      return PRE_REVIEW_REGISTRATION_OPTIONS.filter((item) => item.value === this.ruleScope.registration_scope);
    },
    moduleOptions() {
      return (this.catalogTree || []).map((item) => ({
        value: String((item && item.section_id) || "").trim(),
        label:
          buildSectionDisplayLabel(item && item.section_id, item && item.section_name) ||
          String((item && item.section_id) || "").trim(),
      }));
    },
    selectedModuleLabel() {
      const current = this.moduleOptions.find((item) => item.value === this.selectedModule);
      return current ? current.label : "-";
    },
    currentRuleScopeMetadata() {
      const registrationPath = Array.isArray(this.ruleScope.registration_path) ? this.ruleScope.registration_path : [];
      return {
        registration_scope: String(this.ruleScope.registration_scope || "").trim(),
        registration_class: registrationPath.length >= 2 ? String(registrationPath[1] || "").trim() : "",
        registration_class_sub: registrationPath.length >= 3 ? String(registrationPath[2] || "").trim() : "",
        module: String(this.selectedModule || "").trim(),
        section_path_prefix: String(this.selectedSectionId || "").trim(),
      };
    },
    currentRuleScopeSummary() {
      const parts = [
        this.currentRuleScopeMetadata.registration_scope,
        this.currentRuleScopeMetadata.registration_class,
        this.currentRuleScopeMetadata.registration_class_sub,
        this.currentRuleScopeMetadata.module,
      ].filter(Boolean);
      return parts.length ? parts.join(" / ") : "通用规则";
    },
    currentScopeDeletionMetadata() {
      return {
        registration_scope: String(this.currentRuleScopeMetadata.registration_scope || "").trim(),
        registration_class: String(this.currentRuleScopeMetadata.registration_class || "").trim(),
        registration_class_sub: String(this.currentRuleScopeMetadata.registration_class_sub || "").trim(),
        module: String(this.currentRuleScopeMetadata.module || "").trim(),
      };
    },
    currentImportScopeMetadata() {
      return {
        registration_scope: String(this.currentRuleScopeMetadata.registration_scope || "").trim(),
        registration_class: String(this.currentRuleScopeMetadata.registration_class || "").trim(),
        registration_class_sub: String(this.currentRuleScopeMetadata.registration_class_sub || "").trim(),
        module: String(this.currentRuleScopeMetadata.module || "").trim(),
      };
    },
    canDeleteCurrentScopeRules() {
      return Object.values(this.currentScopeDeletionMetadata).some((value) => String(value || "").trim());
    },
    canImportJsonRules() {
      const scope = this.currentImportScopeMetadata;
      const hasRegistrationScope = [scope.registration_scope, scope.registration_class, scope.registration_class_sub]
        .some((value) => String(value || "").trim());
      return hasRegistrationScope && Boolean(String(scope.module || "").trim());
    },
    deletableRuleCodes() {
      return (this.effectiveRuleItems || [])
        .filter((item) => this.canDeleteRule(item))
        .map((item) => this.ruleSelectionKey(item))
        .filter(Boolean);
    },
    allDeletableRulesSelected() {
      return !!this.deletableRuleCodes.length && this.deletableRuleCodes.every((code) => this.selectedDeletableRuleKeys.includes(code));
    },
    deletableRuleSelectionIndeterminate() {
      return this.selectedDeletableRuleKeys.length > 0 && !this.allDeletableRulesSelected;
    },
    moduleTreeData() {
      if (!this.selectedModule) {
        return [];
      }
      const target = (this.catalogTree || []).find((item) => String((item && item.section_id) || "").trim() === this.selectedModule);
      return this.decorateCatalogTree(target ? [target] : []);
    },
  },
  mounted() {
    this.loadCatalog();
  },
  beforeDestroy() {
    this.rulesRequestToken += 1;
  },
  methods: {
    decorateCatalogTree(nodes) {
      return cloneTree(nodes).map((node) => ({
        ...node,
        label: buildSectionDisplayLabel(node.section_id, node.section_name),
        children_sections: this.decorateCatalogTree(node.children_sections || []),
      }));
    },
    async loadCatalog() {
      try {
        const res = await globalCtdCatalog();
        const data = res && res.data ? res.data : {};
        this.catalogTree = Array.isArray(data.chapter_structure) ? data.chapter_structure : [];
        this.selectedModule = "";
      } catch (e) {
        this.catalogTree = [];
        this.selectedModule = "";
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "加载章节目录失败");
      }
    },
    onModuleChange() {
      this.rulesRequestToken += 1;
      this.loadingRules = false;
      this.selectedSectionId = "";
      this.selectedSectionName = "";
      this.manualRules = [];
      this.effectiveRuleItems = [];
      this.selectedDeletableRuleKeys = [];
    },
    onRuleScopeRegistrationScopeChange() {
      this.ruleScope.registration_path = this.ruleScope.registration_scope ? [this.ruleScope.registration_scope] : [];
      this.onModuleChange();
      this.selectedModule = "";
    },
    onRuleScopeRegistrationPathChange(path) {
      if (!Array.isArray(path) || !path.length) {
        this.ruleScope.registration_scope = "";
        this.ruleScope.registration_path = [];
      } else {
        this.ruleScope.registration_scope = path[0] || "";
      }
      this.onModuleChange();
      this.selectedModule = "";
    },
    resetRuleScope() {
      this.ruleScope = {
        registration_scope: "",
        registration_path: [],
      };
      this.selectedModule = "";
      this.onModuleChange();
    },
    async onSectionSelect(node) {
      const nextSectionId = String((node && node.section_id) || "").trim();
      if (nextSectionId !== String(this.selectedSectionId || "").trim()) {
        this.rulesRequestToken += 1;
        this.loadingRules = false;
        this.manualRules = [];
        this.effectiveRuleItems = [];
        this.selectedDeletableRuleKeys = [];
      }
      this.selectedSectionId = nextSectionId;
      this.selectedSectionName = stripSectionDisplayName((node && node.section_name) || (node && node.label) || "");
      await this.loadCurrentSectionRules();
    },
    async loadCurrentSectionRules() {
      if (!this.selectedSectionId) {
        this.rulesRequestToken += 1;
        this.loadingRules = false;
        this.manualRules = [];
        this.effectiveRuleItems = [];
        return;
      }
      const sectionId = String(this.selectedSectionId || "").trim();
      const scopeMetadata = { ...this.currentRuleScopeMetadata };
      const requestToken = this.rulesRequestToken + 1;
      this.rulesRequestToken = requestToken;
      const isCurrentRequest = () => (
        this.rulesRequestToken === requestToken
        && String(this.selectedSectionId || "").trim() === sectionId
      );
      this.loadingRules = true;
      try {
        const res = await filteredGlobalSectionRules(
          sectionId,
          { scope_metadata: scopeMetadata },
          { silentError: true },
        );
        if (!isCurrentRequest()) {
          return;
        }
        const data = res && res.data ? res.data : {};
        this.effectiveRuleItems = Array.isArray(data.items) ? data.items : [];
        this.manualRules = Array.isArray(data.manual_rule_texts) ? data.manual_rule_texts.slice() : [];
        this.selectedDeletableRuleKeys = [];
      } catch (e) {
        if (!isCurrentRequest()) {
          return;
        }
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "加载章节规则失败");
      } finally {
        if (isCurrentRequest()) {
          this.loadingRules = false;
        }
      }
    },
    appendManualRule() {
      this.manualRules.push("");
    },
    insertManualRule(index) {
      this.manualRules.splice(index + 1, 0, "");
    },
    removeManualRule(index) {
      this.manualRules.splice(index, 1);
    },
    sourceTypeLabel(sourceType) {
      const mapping = {
        ctd_seed: "历史导入",
        json_import: "JSON 导入",
        manual: "手工规则",
        feedback_experience: "反馈经验",
        feedback_patch: "反馈补丁",
      };
      const key = String(sourceType || "").trim();
      return mapping[key] || (key || "未知来源");
    },
    sourceTagType(sourceType) {
      const mapping = {
        ctd_seed: "success",
        json_import: "success",
        manual: "primary",
        feedback_experience: "warning",
        feedback_patch: "danger",
      };
      return mapping[String(sourceType || "").trim()] || "info";
    },
    canDeleteRule(item) {
      if (!item || typeof item !== "object") {
        return false;
      }
      return ["ctd_seed", "json_import", "manual"].includes(String(item.source_type || "").trim()) && Boolean(item.rule_code);
    },
    ruleSelectionKey(item) {
      const sectionId = String((item && item.section_id) || "").trim();
      const ruleCode = String((item && item.rule_code) || "").trim();
      if (!sectionId || !ruleCode) {
        return "";
      }
      return `${sectionId}::${ruleCode}`;
    },
    isDeletingRule(ruleCode) {
      return this.deletingRuleCodes.includes(String(ruleCode || "").trim());
    },
    toggleRuleSelection(item, checked) {
      const selectionKey = this.ruleSelectionKey(item);
      if (!selectionKey) {
        return;
      }
      if (checked) {
        if (!this.selectedDeletableRuleKeys.includes(selectionKey)) {
          this.selectedDeletableRuleKeys.push(selectionKey);
        }
        return;
      }
      this.selectedDeletableRuleKeys = this.selectedDeletableRuleKeys.filter((key) => key !== selectionKey);
    },
    toggleSelectAllDeletableRules(checked) {
      this.selectedDeletableRuleKeys = checked ? this.deletableRuleCodes.slice() : [];
    },
    async deleteRule(item) {
      const ruleCode = String((item && item.rule_code) || "").trim();
      const ruleSectionId = String((item && item.section_id) || this.selectedSectionId || "").trim();
      if (!ruleSectionId || !ruleCode) {
        return;
      }
      try {
        await this.$confirm("确认删除这条规则吗？", "删除确认", {
          type: "warning",
        });
      } catch (e) {
        return;
      }
      this.deletingRuleCodes = [...this.deletingRuleCodes, ruleCode];
      try {
        const res = await deleteGlobalSectionRule(ruleSectionId, ruleCode);
        this.selectedDeletableRuleKeys = this.selectedDeletableRuleKeys.filter((key) => key !== this.ruleSelectionKey(item));
        await this.loadCurrentSectionRules();
        this.$message.success((res && res.message) || "规则已删除");
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "删除规则失败");
      } finally {
        this.deletingRuleCodes = this.deletingRuleCodes.filter((code) => code !== ruleCode);
      }
    },
    async batchDeleteRules() {
      if (!this.selectedSectionId || !this.selectedDeletableRuleKeys.length) {
        return;
      }
      try {
        await this.$confirm(`确认删除已选择的 ${this.selectedDeletableRuleKeys.length} 条规则吗？`, "批量删除确认", {
          type: "warning",
        });
      } catch (e) {
        return;
      }
      try {
        const groupedRuleCodes = {};
        (this.selectedDeletableRuleKeys || []).forEach((key) => {
          const [sectionId, ruleCode] = String(key || "").split("::");
          if (!sectionId || !ruleCode) {
            return;
          }
          if (!Array.isArray(groupedRuleCodes[sectionId])) {
            groupedRuleCodes[sectionId] = [];
          }
          groupedRuleCodes[sectionId].push(ruleCode);
        });
        for (const [sectionId, ruleCodes] of Object.entries(groupedRuleCodes)) {
          await batchDeleteGlobalSectionRules(sectionId, {
            rule_codes: ruleCodes,
          });
        }
        this.selectedDeletableRuleKeys = [];
        await this.loadCurrentSectionRules();
        this.$message.success("规则已批量删除");
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "批量删除规则失败");
      }
    },
    async deleteCurrentScopeRules() {
      if (!this.canDeleteCurrentScopeRules) {
        this.$message.warning("请先选择作用域或模块");
        return;
      }
      try {
        const previewRes = await previewDeleteGlobalSectionRulesByScope({
          scope_metadata: this.currentScopeDeletionMetadata,
        });
        const previewData = (previewRes && previewRes.data) || {};
        const ruleCount = Number(previewData.matched_rule_count || 0);
        const sectionCount = Number(previewData.matched_section_count || 0);
        await this.$confirm(
          `确认删除当前作用域下的全部规则吗？\n将删除 ${ruleCount} 条规则，涉及 ${sectionCount} 个章节。`,
          "删除确认",
          {
          type: "warning",
          }
        );
      } catch (e) {
        return;
      }
      this.deletingScopedRules = true;
      try {
        const res = await deleteGlobalSectionRulesByScope({
          scope_metadata: this.currentScopeDeletionMetadata,
        });
        this.selectedDeletableRuleKeys = [];
        if (this.selectedSectionId) {
          await this.loadCurrentSectionRules();
        } else {
          this.effectiveRuleItems = [];
          this.manualRules = [];
        }
        this.$message.success((res && res.message) || "当前作用域规则已删除");
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "删除当前作用域规则失败");
      } finally {
        this.deletingScopedRules = false;
      }
    },
    async saveCurrentSectionRules() {
      if (!this.selectedSectionId) {
        this.$message.warning("请先选择章节");
        return;
      }
      this.savingRules = true;
      try {
        const payload = {
          rule_texts: (this.manualRules || []).map((item) => String(item || "").trim()).filter(Boolean),
          scope_metadata: this.currentRuleScopeMetadata,
        };
        const res = await saveGlobalSectionRules(this.selectedSectionId, payload);
        const data = res && res.data ? res.data : {};
        this.effectiveRuleItems = Array.isArray(data.items) ? data.items : [];
        this.manualRules = Array.isArray(data.manual_rule_texts) ? data.manual_rule_texts.slice() : [];
        this.$message.success((res && res.message) || "规则已保存");
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "保存规则失败");
      } finally {
        this.savingRules = false;
      }
    },
    async onImportFileChange(file) {
      const raw = file && file.raw;
      if (!raw) {
        return;
      }
      if (!this.canImportJsonRules) {
        this.$message.warning("请先明确作用域并选择模块后再导入 JSON 规则");
        return;
      }
      const formData = new FormData();
      formData.append("file", raw, file.name || "section_rules.json");
      formData.append("scope_metadata", JSON.stringify(this.currentImportScopeMetadata));
      this.importingRules = true;
      try {
        const res = await importGlobalSectionRules(formData);
        const data = res && res.data ? res.data : {};
        const importedItems = Array.isArray(data.items) ? data.items : [];
        const importedSectionIds = importedItems
          .map((item) => String((item && item.section_id) || "").trim())
          .filter(Boolean);
        const preferredSectionId =
          importedSectionIds.find((sectionId) => sectionId === this.selectedSectionId)
          || importedSectionIds.find((sectionId) => sectionId === this.selectedModule || sectionId.startsWith(`${this.selectedModule}.`))
          || importedSectionIds[0]
          || "";
        if (preferredSectionId) {
          const matchedNode = this.findTreeNodeBySectionId(this.catalogTree, preferredSectionId);
          this.selectedSectionId = preferredSectionId;
          this.selectedSectionName = stripSectionDisplayName(
            (matchedNode && matchedNode.section_name) || (matchedNode && matchedNode.label) || preferredSectionId
          );
          await this.loadCurrentSectionRules();
          this.$nextTick(() => {
            if (this.$refs.ruleTree && this.$refs.ruleTree.setCurrentKey) {
              this.$refs.ruleTree.setCurrentKey(preferredSectionId);
            }
          });
        }
        this.$message.success((res && res.message) || "JSON 规则已导入");
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "导入 JSON 规则失败");
      } finally {
        this.importingRules = false;
      }
    },
    findTreeNodeBySectionId(nodes, sectionId) {
      const target = String(sectionId || "").trim();
      const queue = Array.isArray(nodes) ? [...nodes] : [];
      while (queue.length) {
        const node = queue.shift();
        if (!node || typeof node !== "object") {
          continue;
        }
        if (String((node && node.section_id) || "").trim() === target) {
          return node;
        }
        const children = Array.isArray(node.children_sections) ? node.children_sections : [];
        queue.push(...children);
      }
      return null;
    },
  },
};
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.muted {
  font-size: 12px;
  color: #64748b;
}

.workspace {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 16px;
}

.tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tree-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tree-card {
  min-height: 680px;
}

.right-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
  font-size: 13px;
  color: #334155;
}

.manual-rule-list,
.effective-rule-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.batch-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.manual-rule-row,
.effective-rule-item {
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.effective-rule-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.effective-rule-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}

.rule-code {
  font-size: 12px;
  color: #64748b;
}

.rule-text {
  white-space: pre-wrap;
  line-height: 1.7;
  color: #1f2937;
}

.empty-tip {
  color: #64748b;
  font-size: 13px;
}
</style>
