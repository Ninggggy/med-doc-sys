<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">辅助审评项目管理</h2>
        <div class="page-desc">
          管理辅助审评项目、申报资料上传、运行记录和模块上传进度。
        </div>
      </div>
      <div class="header-actions">
        <el-button @click="confirmBatchDelete" :disabled="!selectedProjectIds.length">批量删除</el-button>
        <el-button type="primary" @click="openCreateDialog">创建项目</el-button>
      </div>
    </div>

    <el-card class="k-card">
      <div slot="header">项目筛选</div>
      <div class="filter-row">
        <el-input
          v-model.trim="filters.project_name"
          placeholder="输入项目名称"
          clearable
          style="width: 240px"
          @keyup.enter.native="loadProjects"
        />
        <el-select
          v-model="filters.registration_scope"
          placeholder="注册大类"
          clearable
          style="width: 160px"
          @change="onFilterRegistrationScopeChange"
        >
          <el-option
            v-for="item in registrationScopeOptions"
            :key="item"
            :label="item"
            :value="item"
          />
        </el-select>
        <el-cascader
          v-model="filters.registration_path"
          :options="registrationFilterOptions"
          :props="registrationCascaderProps"
          clearable
          filterable
          style="width: 320px"
          placeholder="选择注册分类"
          @change="onFilterRegistrationPathChange"
        />
        <el-button type="primary" @click="loadProjects">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>
    </el-card>

    <el-card class="k-card">
      <div slot="header">项目列表</div>
      <el-table
        :data="projects"
        v-loading="loadingProjects"
        border
        class="k-table compact-table"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="48" />
        <el-table-column prop="project_name" label="项目名称" min-width="180" />
        <el-table-column prop="registration_scope" label="注册大类" width="120" />
        <el-table-column
          prop="registration_leaf"
          label="注册分类"
          min-width="240"
          show-overflow-tooltip
        />
        <el-table-column label="模块上传状态" min-width="320">
          <template slot-scope="scope">
            <div class="module-status-list">
              <div v-if="isProjectUploadParsing(scope.row.project_id)" class="module-status-item parsing">
                <span class="module-name">上传解析</span>
                <span class="module-count">解析中（{{ activeProjectUploadTaskCount(scope.row.project_id) }}）</span>
              </div>
              <div
                v-for="item in normalizeModuleStatus(scope.row.module_status)"
                :key="`${scope.row.project_id}-${item.module_id}`"
                class="module-status-item"
              >
                <span class="module-name">{{ item.module_name }}</span>
                <span class="module-count">{{ item.status_text }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="version_count" label="运行数" width="90" />
        <el-table-column prop="current_version" label="当前版本" width="96" />
        <el-table-column prop="create_time" label="创建时间" width="170" />
        <el-table-column prop="update_time" label="更新时间" width="170" />
        <el-table-column label="操作" width="280" fixed="right">
          <template slot-scope="scope">
            <el-button type="text" @click="openProjectUpload(scope.row)">上传资料</el-button>
            <el-button type="text" @click="openSession(scope.row)">进入会话</el-button>
            <el-button type="text" @click="viewRuns(scope.row)">运行记录</el-button>
            <el-button type="text" style="color: #c62828" @click="dropProject(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="selectedProject.project_id" class="k-card">
      <div slot="header" class="project-run-header">
        <span>运行记录 - {{ selectedProject.project_name }}</span>
        <div class="header-actions">
          <el-button size="mini" @click="openProjectUpload(selectedProject)">上传资料</el-button>
          <el-button size="mini" type="primary" @click="openSession(selectedProject)">进入会话</el-button>
        </div>
      </div>
      <el-table :data="runs" v-loading="loadingRuns" border class="k-table">
        <el-table-column prop="run_id" label="Run ID" min-width="190" />
        <el-table-column prop="version_no" label="版本" width="90" />
        <el-table-column prop="source_file_name" label="来源文件" min-width="220" />
        <el-table-column prop="accuracy" label="准确率" width="100" />
        <el-table-column prop="feedback_count" label="反馈数" width="90" />
        <el-table-column prop="create_time" label="创建时间" width="170" />
        <el-table-column prop="finish_time" label="完成时间" width="170" />
        <el-table-column prop="summary" label="摘要" min-width="260" show-overflow-tooltip />
        <el-table-column label="操作" width="90" fixed="right">
          <template slot-scope="scope">
            <el-button type="text" @click="openRun(scope.row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog title="创建项目" :visible.sync="createDialog.visible" width="760px">
      <el-form label-width="110px" size="small">
        <el-form-item label="项目名称">
          <el-input v-model="projectForm.project_name" placeholder="输入项目名称" />
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="projectForm.owner" placeholder="输入负责人" />
        </el-form-item>
        <el-form-item label="项目说明">
          <el-input
            v-model="projectForm.description"
            type="textarea"
            :rows="3"
            placeholder="输入项目说明"
          />
        </el-form-item>
        <el-form-item label="注册大类">
          <el-select
            v-model="projectForm.registration_scope"
            placeholder="选择注册大类"
            style="width: 100%"
            @change="onRegistrationScopeChange"
          >
            <el-option
              v-for="item in registrationScopeOptions"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="注册分类">
          <el-cascader
            v-model="projectForm.registration_path"
            :options="registrationOptions"
            :props="registrationCascaderProps"
            clearable
            filterable
            style="width: 100%"
            placeholder="选择注册分类"
            @change="onRegistrationPathChange"
          />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button @click="closeCreateDialog">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createProjectOnly">创建项目</el-button>
      </span>
    </el-dialog>

    <pre-review-upload-dialog
      :visible.sync="projectUploadDialog.visible"
      :dialog-title="projectUploadDialogTitle"
      :branch="projectUploadDialog.branch"
      :branch-options="projectUploadBranchOptions"
      :branch-selector-label="'上传模块'"
      :mode="projectUploadDialog.mode"
      :parse-mode="projectUploadDialog.parse_mode"
      :section-name="projectUploadDialog.section_name"
      :file-list="projectUploadDialog.file_list"
      :tree-props="treeProps"
      :upload-tree-data="projectUploadTreeData"
      :uploading="uploading"
      @update:branch="onProjectUploadBranchChange"
      @update:mode="onProjectUploadModeChange"
      @update:parse-mode="onProjectUploadParseModeChange"
      @select-section="onProjectUploadSectionSelect"
      @file-change="onProjectUploadFileChange"
      @file-remove="onProjectUploadFileRemove"
      @close="closeProjectUploadDialog"
      @submit="submitProjectUpload"
    />
  </div>
</template>

<script>
import {
  batchDeleteProjects,
  createProject,
  ctdCatalog,
  deleteProject,
  getPreReviewTaskProgress,
  listProjects,
  runHistory,
  uploadSubmissionAsync,
} from "@/api/prereview";
import PreReviewUploadDialog from "@/components/pre-review/PreReviewUploadDialog.vue";
import { PRE_REVIEW_REGISTRATION_OPTIONS, findRegistrationNodes } from "@/constants/registration";
import { buildSectionDisplayLabel } from "@/utils/ctdDisplay";

function cloneTree(nodes) {
  return Array.isArray(nodes) ? JSON.parse(JSON.stringify(nodes)) : [];
}

function normalizeBranchValue(value) {
  return String(value || "").trim();
}

function flattenCatalogNodes(nodes) {
  const out = [];
  const queue = Array.isArray(nodes) ? [...nodes] : [];
  while (queue.length) {
    const node = queue.shift();
    if (!node || typeof node !== "object") {
      continue;
    }
    out.push(node);
    const children = Array.isArray(node.children_sections) ? node.children_sections : [];
    queue.push(...children);
  }
  return out;
}

function deriveUploadBranchKey(sectionId) {
  const sid = normalizeBranchValue(sectionId).toLowerCase();
  if (!sid) {
    return "";
  }
  if (sid === "supplement" || sid.startsWith("supplement.")) {
    return "supplement";
  }
  if (["3.2.s", "3.2.p", "3.2.a", "3.2.r"].includes(sid)) {
    return sid;
  }
  if (sid.startsWith("3.2.s.")) {
    return "3.2.s";
  }
  if (sid.startsWith("3.2.p.")) {
    return "3.2.p";
  }
  if (sid.startsWith("3.2.a.")) {
    return "3.2.a";
  }
  if (sid.startsWith("3.2.r.")) {
    return "3.2.r";
  }
  const first = sid.split(".")[0];
  if (["1", "2", "3", "4", "5"].includes(first)) {
    return first;
  }
  return sid;
}

function buildUploadBranchLabel(branchKey, nodeMap = {}) {
  const normalized = normalizeBranchValue(branchKey).toLowerCase();
  const known = {
    "1": "1 模块1 行政文件和药品信息",
    "2": "2 模块2 通用技术文档总结",
    "3": "3 模块3 质量",
    "4": "4 模块4 非临床试验报告",
    "5": "5 模块5 临床研究报告",
    supplement: "药品补充申请",
    "3.2.s": "3.2.S 原料药",
    "3.2.p": "3.2.P 制剂",
    "3.2.a": "3.2.A 附录",
    "3.2.r": "3.2.R 区域性信息",
  };
  if (known[normalized]) {
    return known[normalized];
  }
  const node = nodeMap[normalized] || nodeMap[branchKey];
  return buildSectionDisplayLabel(branchKey, node && node.section_name) || branchKey;
}

const UPLOAD_POLL_INTERVAL_MS = 15000;

export default {
  name: "PreReviewManage",
  components: {
    PreReviewUploadDialog,
  },
  data() {
    return {
      projects: [],
      selectedProjectIds: [],
      selectedProject: { project_id: "", project_name: "" },
      runs: [],
      filters: {
        project_name: "",
        registration_scope: "",
        registration_path: [],
        registration_leaf: "",
      },
      projectForm: this.getDefaultProjectForm(),
      creating: false,
      uploading: false,
      createDialog: { visible: false },
      treeProps: { children: "children_sections", label: "label" },
      registrationCascaderProps: {
        value: "value",
        label: "label",
        children: "children",
        emitPath: true,
        checkStrictly: false,
      },
      projectUploadDialog: {
        visible: false,
        project_id: "",
        project_name: "",
        registration_scope: "",
        branch: "",
        mode: "zip",
        parse_mode: "quick",
        section_id: "",
        section_name: "",
        file_list: [],
      },
      projectUploadCatalog: [],
      uploadTaskStateMap: {},
      uploadPollTimer: null,
      projectListRequestToken: 0,
      loadingProjects: false,
      runsRequestToken: 0,
      loadingRuns: false,
      uploadCatalogRequestToken: 0,
    };
  },
  computed: {
    registrationScopeOptions() {
      return PRE_REVIEW_REGISTRATION_OPTIONS.map((item) => item.value);
    },
    registrationOptions() {
      if (!this.projectForm.registration_scope) {
        return [];
      }
      return PRE_REVIEW_REGISTRATION_OPTIONS.filter(
        (item) => item.value === this.projectForm.registration_scope
      );
    },
    registrationFilterOptions() {
      if (!this.filters.registration_scope) {
        return PRE_REVIEW_REGISTRATION_OPTIONS;
      }
      return PRE_REVIEW_REGISTRATION_OPTIONS.filter(
        (item) => item.value === this.filters.registration_scope
      );
    },
    projectUploadDialogTitle() {
      const name = String(this.projectUploadDialog.project_name || "").trim();
      return name ? `上传申报资料 - ${name}` : "上传申报资料";
    },
    projectUploadBranchOptions() {
      const roots = Array.isArray(this.projectUploadCatalog) ? this.projectUploadCatalog : [];
      const allNodes = flattenCatalogNodes(roots);
      const nodeMap = {};
      allNodes.forEach((node) => {
        const sid = normalizeBranchValue(node && node.section_id);
        if (!sid) {
          return;
        }
        nodeMap[sid] = node;
        nodeMap[sid.toLowerCase()] = node;
      });
      const options = [{ value: "all", label: "全部模块" }];
      const seen = new Set();
      roots.forEach((node) => {
        const branchKey = deriveUploadBranchKey(node && node.section_id);
        if (!branchKey || seen.has(branchKey)) {
          return;
        }
        seen.add(branchKey);
        options.push({
          value: branchKey,
          label: buildUploadBranchLabel(branchKey, nodeMap),
        });
      });
      const fallbackBranches = ["1", "2", "3", "4", "5", "supplement"];
      fallbackBranches.forEach((branchKey) => {
        if (seen.has(branchKey)) {
          return;
        }
        seen.add(branchKey);
        options.push({
          value: branchKey,
          label: buildUploadBranchLabel(branchKey, nodeMap),
        });
      });
      return options;
    },
    projectUploadTreeData() {
      const roots = Array.isArray(this.projectUploadCatalog) ? this.projectUploadCatalog : [];
      return this.decorateCatalogTree(this.filterTreeByBranch(roots, this.projectUploadDialog.branch));
    },
  },
  mounted() {
    this.loadProjects();
  },
  beforeDestroy() {
    this.projectListRequestToken += 1;
    this.runsRequestToken += 1;
    this.uploadCatalogRequestToken += 1;
    this.stopUploadPolling();
  },
  methods: {
    getDefaultProjectForm() {
      return {
        project_name: "",
        owner: "",
        description: "",
        registration_scope: "",
        registration_path: [],
      };
    },
    stopUploadPolling() {
      if (this.uploadPollTimer) {
        clearTimeout(this.uploadPollTimer);
        this.uploadPollTimer = null;
      }
    },
    syncUploadTaskStateFromProjects(projects) {
      const rows = Array.isArray(projects) ? projects : [];
      rows.forEach((item) => {
        const projectId = String((item && item.project_id) || "").trim();
        const uploadTasks = Array.isArray(item && item.upload_tasks) ? item.upload_tasks : [];
        if (!projectId) {
          return;
        }
        const activeTasks = uploadTasks
          .map((task) => ({
            task_id: String((task && task.task_id) || "").trim(),
            status: String((task && task.status) || "").trim(),
            file_count: Number((task && task.file_count) || 0),
            updated_at: (task && task.update_time) || "",
            last_result: (task && task.result) || null,
            persisted: true,
          }))
          .filter((task) => task.task_id && ["pending", "running"].includes(task.status));
        if (activeTasks.length) {
          this.$set(this.uploadTaskStateMap, projectId, activeTasks);
          return;
        }
        if (!Array.isArray(this.uploadTaskStateMap[projectId]) || !this.uploadTaskStateMap[projectId].length) {
          this.$delete(this.uploadTaskStateMap, projectId);
        }
      });
      this.scheduleUploadPolling();
    },
    isProjectUploadParsing(projectId) {
      const key = String(projectId || "").trim();
      const states = key ? this.uploadTaskStateMap[key] : null;
      return !!(Array.isArray(states) && states.some((item) => ["pending", "running"].includes(String((item && item.status) || "").trim())));
    },
    activeProjectUploadTaskCount(projectId) {
      const key = String(projectId || "").trim();
      const states = key ? this.uploadTaskStateMap[key] : null;
      return Array.isArray(states) ? states.length : 0;
    },
    scheduleUploadPolling() {
      this.stopUploadPolling();
      const hasActiveTask = Object.values(this.uploadTaskStateMap || {}).some((items) =>
        Array.isArray(items) && items.some((item) => ["pending", "running"].includes(String((item && item.status) || "").trim()))
      );
      if (!hasActiveTask) {
        return;
      }
      this.uploadPollTimer = setTimeout(() => {
        this.pollUploadTasks();
      }, UPLOAD_POLL_INTERVAL_MS);
    },
    async pollUploadTasks() {
      const entries = [];
      Object.entries(this.uploadTaskStateMap || {}).forEach(([projectId, items]) => {
        (Array.isArray(items) ? items : []).forEach((item) => {
          if (["pending", "running"].includes(String((item && item.status) || "").trim())) {
            entries.push([projectId, item]);
          }
        });
      });
      if (!entries.length) {
        this.stopUploadPolling();
        return;
      }
      const completedMessages = [];
      const warningMessages = [];
      const failedMessages = [];
      let projectListNeedsRefresh = false;
      for (const [projectId, taskState] of entries) {
        const taskId = String((taskState && taskState.task_id) || "").trim();
        if (!taskId) {
          this.$delete(this.uploadTaskStateMap, projectId);
          continue;
        }
        try {
          const res = await getPreReviewTaskProgress(taskId, {});
          const snapshot = (res && res.data) || {};
          const status = String(snapshot.status || "").trim() || "pending";
          const currentStates = Array.isArray(this.uploadTaskStateMap[projectId]) ? this.uploadTaskStateMap[projectId].slice() : [];
          const stateIndex = currentStates.findIndex((item) => String((item && item.task_id) || "").trim() === taskId);
          if (stateIndex >= 0) {
            currentStates.splice(stateIndex, 1, {
              ...(taskState || {}),
              status,
              last_result: snapshot.result || null,
              updated_at: snapshot.updated_at || "",
            });
            this.$set(this.uploadTaskStateMap, projectId, currentStates);
          }
          if (status === "completed") {
            projectListNeedsRefresh = true;
            const message = String(
              (snapshot.result && snapshot.result.message)
              || "上传解析完成"
            ).trim();
            const resultItems = (((snapshot.result || {}).data || {}).items) || [];
            const warningCount = (Array.isArray(resultItems) ? resultItems : []).reduce((total, item) => {
              const warnings = Array.isArray(item && item.parser_warnings) ? item.parser_warnings.length : 0;
              const qualityStatus = String((((item || {}).parse_quality || {}).status_code) || "").trim();
              return total + warnings + (qualityStatus === "manual_review" && !warnings ? 1 : 0);
            }, 0);
            if (warningCount > 0) {
              warningMessages.push(`${message}；有 ${warningCount} 项章节映射需人工确认，原文已完整保留`);
            } else {
              completedMessages.push(message);
            }
            const remained = currentStates.filter((item) => String((item && item.task_id) || "").trim() !== taskId);
            if (remained.length) {
              this.$set(this.uploadTaskStateMap, projectId, remained);
            } else {
              this.$delete(this.uploadTaskStateMap, projectId);
            }
          } else if (status === "failed") {
            projectListNeedsRefresh = true;
            const message = String(
              snapshot.error_message
              || (snapshot.result && snapshot.result.message)
              || "上传解析失败"
            ).trim();
            failedMessages.push(message);
            const remained = currentStates.filter((item) => String((item && item.task_id) || "").trim() !== taskId);
            if (remained.length) {
              this.$set(this.uploadTaskStateMap, projectId, remained);
            } else {
              this.$delete(this.uploadTaskStateMap, projectId);
            }
          }
        } catch (e) {
          const backendMessage = e && e.response && e.response.data && e.response.data.message;
          failedMessages.push(backendMessage || (e && e.message) || "上传解析状态查询失败");
          const currentStates = Array.isArray(this.uploadTaskStateMap[projectId]) ? this.uploadTaskStateMap[projectId].slice() : [];
          const remained = currentStates.filter((item) => String((item && item.task_id) || "").trim() !== taskId);
          if (remained.length) {
            this.$set(this.uploadTaskStateMap, projectId, remained);
          } else {
            this.$delete(this.uploadTaskStateMap, projectId);
          }
        }
      }
      if (projectListNeedsRefresh) {
        await this.loadProjects();
      }
      completedMessages.forEach((message) => this.$message.success(message));
      warningMessages.forEach((message) => this.$message.warning({ message, duration: 8000, showClose: true }));
      failedMessages.forEach((message) => this.$message.error(message));
      this.scheduleUploadPolling();
    },
    normalizeModuleStatus(moduleStatus) {
      return Array.isArray(moduleStatus) ? moduleStatus : [];
    },
    decorateCatalogTree(nodes) {
      return cloneTree(nodes).map((node) => ({
        ...node,
        label:
          normalizeBranchValue(node && node.section_id) === "3"
            ? buildSectionDisplayLabel("3", "质量（药学）") || "3 质量（药学）"
            : buildSectionDisplayLabel(node.section_id, node.section_name) || node.section_id,
        children_sections: this.decorateCatalogTree(node.children_sections || []),
      }));
    },
    filterTreeByBranch(nodes, branch) {
      const branchValue = normalizeBranchValue(branch);
      if (!branchValue || branchValue === "all") {
        return cloneTree(nodes);
      }
      const normalizedBranch = branchValue.toLowerCase();
      const matchesBranch = (sid) => {
        const normalizedSid = normalizeBranchValue(sid).toLowerCase();
        if (!normalizedSid) {
          return false;
        }
        return normalizedSid === normalizedBranch || normalizedSid.startsWith(`${normalizedBranch}.`);
      };
      const filterNode = (node) => {
        if (!node || typeof node !== "object") {
          return null;
        }
        const sid = normalizeBranchValue(node.section_id);
        const children = Array.isArray(node.children_sections) ? node.children_sections : [];
        const filteredChildren = children.map(filterNode).filter(Boolean);
        if (matchesBranch(sid)) {
          return {
            ...cloneTree([node])[0],
            children_sections: filteredChildren.length ? filteredChildren : cloneTree(children),
          };
        }
        if (filteredChildren.length) {
          return {
            ...cloneTree([node])[0],
            children_sections: filteredChildren,
          };
        }
        return null;
      };
      const filtered = (nodes || []).map(filterNode).filter(Boolean);
      if (filtered.length) {
        return filtered;
      }
      return cloneTree((nodes || []).filter((node) => matchesBranch(node && node.section_id)));
    },
    defaultUploadBranch() {
      const preferred = ["3", "4", "5", "1", "2", "supplement"];
      for (const sectionId of preferred) {
        if (this.projectUploadBranchOptions.some((item) => item.value === sectionId)) {
          return sectionId;
        }
      }
      return this.projectUploadBranchOptions[0] ? this.projectUploadBranchOptions[0].value : "all";
    },
    normalizeProjectUploadMode() {
      if (!this.projectUploadBranchOptions.some((item) => item.value === this.projectUploadDialog.branch)) {
        this.projectUploadDialog.branch = this.defaultUploadBranch();
      }
      if (this.projectUploadDialog.mode !== "section") {
        this.projectUploadDialog.section_id = "";
        this.projectUploadDialog.section_name = "";
      }
      if (!["single", "section"].includes(this.projectUploadDialog.mode)) {
        this.projectUploadDialog.parse_mode = "quick";
      }
    },
    resolveRegistrationLeaf(path) {
      if (!Array.isArray(path) || !path.length) {
        return "";
      }
      const nodes = findRegistrationNodes(path);
      if (!nodes.length) {
        return "";
      }
      const last = nodes[nodes.length - 1] || {};
      const hasChildren = Array.isArray(last.children) && last.children.length > 0;
      if (hasChildren && path.length === 1) {
        return "";
      }
      return String(last.value || "").trim();
    },
    async loadProjects() {
      const registrationLeaf = this.resolveRegistrationLeaf(this.filters.registration_path);
      this.filters.registration_leaf = registrationLeaf;
      const requestToken = this.projectListRequestToken + 1;
      this.projectListRequestToken = requestToken;
      const params = {
        page: 1,
        page_size: 200,
        project_name: this.filters.project_name,
        registration_scope: this.filters.registration_scope,
        registration_leaf: registrationLeaf,
      };
      const isCurrentRequest = () => this.projectListRequestToken === requestToken;
      this.loadingProjects = true;
      try {
        const res = await listProjects(params, { silentError: true });
        if (!isCurrentRequest()) {
          return;
        }
        this.projects = (res.data && res.data.list) || [];
        this.syncUploadTaskStateFromProjects(this.projects);
        if (this.selectedProject.project_id) {
          const matched = this.projects.find(
            (item) => String((item && item.project_id) || "").trim() === String(this.selectedProject.project_id || "").trim()
          );
          if (matched) {
            this.selectedProject = matched;
          }
        }
      } catch (e) {
        if (!isCurrentRequest()) {
          return;
        }
        this.projects = [];
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "加载项目列表失败");
      } finally {
        if (isCurrentRequest()) {
          this.loadingProjects = false;
        }
      }
    },
    resetFilters() {
      this.filters = {
        project_name: "",
        registration_scope: "",
        registration_path: [],
        registration_leaf: "",
      };
      this.loadProjects();
    },
    onFilterRegistrationScopeChange() {
      this.filters.registration_path = [];
      this.filters.registration_leaf = "";
    },
    onFilterRegistrationPathChange(path) {
      if (!Array.isArray(path) || !path.length) {
        this.filters.registration_leaf = "";
        return;
      }
      this.filters.registration_scope = path[0] || "";
      this.filters.registration_leaf = this.resolveRegistrationLeaf(path);
    },
    onSelectionChange(rows) {
      this.selectedProjectIds = (rows || [])
        .map((item) => String((item && item.project_id) || "").trim())
        .filter(Boolean);
    },
    openCreateDialog() {
      this.createDialog.visible = true;
    },
    closeCreateDialog() {
      this.createDialog.visible = false;
      this.projectForm = this.getDefaultProjectForm();
    },
    onRegistrationScopeChange() {
      this.projectForm.registration_path = this.projectForm.registration_scope
        ? [this.projectForm.registration_scope]
        : [];
    },
    onRegistrationPathChange(path) {
      if (!Array.isArray(path) || !path.length) {
        this.projectForm.registration_path = [];
        return;
      }
      this.projectForm.registration_scope = path[0] || "";
    },
    async createProjectOnly() {
      if (!this.projectForm.project_name) {
        this.$message.warning("请输入项目名称");
        return;
      }
      if (!this.projectForm.registration_scope) {
        this.$message.warning("请选择注册大类");
        return;
      }
      if (!this.projectForm.registration_path.length) {
        this.$message.warning("请选择注册分类");
        return;
      }
      this.creating = true;
      try {
        const nodes = findRegistrationNodes(this.projectForm.registration_path);
        const registrationLeaf = nodes.length ? nodes[nodes.length - 1].value : "";
        const registrationDescription = nodes
          .map((item) => `${item.label}：${item.description || ""}`)
          .join("\n\n");
        const payload = {
          project_name: this.projectForm.project_name,
          owner: this.projectForm.owner,
          description: this.projectForm.description,
          registration_scope: this.projectForm.registration_scope,
          registration_path: this.projectForm.registration_path,
          registration_leaf: registrationLeaf,
          registration_description: registrationDescription,
        };
        const res = await createProject(payload);
        this.$message.success((res && res.message) || "项目创建成功");
        this.closeCreateDialog();
        await this.loadProjects();
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "创建项目失败");
      } finally {
        this.creating = false;
      }
    },
    async dropProject(row) {
      try {
        await this.$confirm(`确认删除项目 ${row.project_name}？`, "提示", { type: "warning" });
        await deleteProject(row.project_id);
        this.$message.success("项目删除成功");
        if (this.selectedProject.project_id === row.project_id) {
          this.selectedProject = { project_id: "", project_name: "" };
          this.runs = [];
        }
        await this.loadProjects();
      } catch (_) {}
    },
    async confirmBatchDelete() {
      if (!this.selectedProjectIds.length) {
        return;
      }
      try {
        await this.$confirm(
          `确认批量删除已选择的 ${this.selectedProjectIds.length} 个项目？`,
          "提示",
          { type: "warning" }
        );
        const res = await batchDeleteProjects({ project_ids: this.selectedProjectIds });
        this.$message.success((res && res.message) || "批量删除完成");
        if (this.selectedProjectIds.includes(this.selectedProject.project_id)) {
          this.selectedProject = { project_id: "", project_name: "" };
          this.runs = [];
        }
        this.selectedProjectIds = [];
        await this.loadProjects();
      } catch (_) {}
    },
    openSession(row) {
      this.$router.push({ name: "pre-review-session", params: { projectId: row.project_id } });
    },
    openRun(row) {
      const projectId = String((row && row.project_id) || (this.selectedProject && this.selectedProject.project_id) || "").trim();
      const runId = String((row && row.run_id) || "").trim();
      if (!projectId || !runId) {
        this.$message.warning("运行记录缺少项目或运行标识，无法查看");
        return;
      }
      this.$router.push({
        name: "pre-review-session",
        params: { projectId },
        query: { run_id: runId },
      });
    },
    async viewRuns(row) {
      const projectId = String((row && row.project_id) || "").trim();
      const requestToken = this.runsRequestToken + 1;
      this.runsRequestToken = requestToken;
      this.selectedProject = row || { project_id: "", project_name: "" };
      this.runs = [];
      this.loadingRuns = true;
      try {
        const res = await runHistory(
          { project_id: projectId, page: 1, page_size: 20 },
          { silentError: true },
        );
        if (
          this.runsRequestToken !== requestToken
          || String((this.selectedProject && this.selectedProject.project_id) || "").trim() !== projectId
        ) return;
        const data = res && res.data;
        this.runs = Array.isArray(data)
          ? data
          : (data && Array.isArray(data.list) ? data.list : []);
      } catch (e) {
        if (
          this.runsRequestToken !== requestToken
          || String((this.selectedProject && this.selectedProject.project_id) || "").trim() !== projectId
        ) return;
        this.runs = [];
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.userMessage) || (e && e.message) || "加载运行记录失败");
      } finally {
        if (this.runsRequestToken === requestToken) {
          this.loadingRuns = false;
        }
      }
    },
    async openProjectUpload(row) {
      const projectId = String((row && row.project_id) || "").trim();
      const requestToken = this.uploadCatalogRequestToken + 1;
      this.uploadCatalogRequestToken = requestToken;
      this.projectUploadDialog = {
        visible: true,
        project_id: projectId,
        project_name: String((row && row.project_name) || "").trim(),
        registration_scope: String((row && row.registration_scope) || "").trim(),
        branch: "",
        mode: "zip",
        parse_mode: "quick",
        section_id: "",
        section_name: "",
        file_list: [],
      };
      this.projectUploadCatalog = [];
      try {
        const res = await ctdCatalog(projectId, { silentError: true });
        if (
          this.uploadCatalogRequestToken !== requestToken
          || !this.projectUploadDialog.visible
          || String(this.projectUploadDialog.project_id || "").trim() !== projectId
        ) return;
        const data = res && res.data ? res.data : {};
        this.projectUploadCatalog = Array.isArray(data.chapter_structure) ? data.chapter_structure : [];
        this.projectUploadDialog.branch = this.defaultUploadBranch();
        this.normalizeProjectUploadMode();
      } catch (e) {
        if (
          this.uploadCatalogRequestToken !== requestToken
          || !this.projectUploadDialog.visible
          || String(this.projectUploadDialog.project_id || "").trim() !== projectId
        ) return;
        this.projectUploadCatalog = [];
        this.projectUploadDialog.branch = "all";
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.userMessage) || (e && e.message) || "加载上传章节目录失败");
      }
    },
    closeProjectUploadDialog() {
      this.uploadCatalogRequestToken += 1;
      this.projectUploadDialog.visible = false;
      this.projectUploadCatalog = [];
    },
    onProjectUploadBranchChange(value) {
      this.projectUploadDialog.branch = normalizeBranchValue(value) || "all";
      this.normalizeProjectUploadMode();
    },
    onProjectUploadModeChange(value) {
      this.projectUploadDialog.mode = String(value || "zip").trim() || "zip";
      this.normalizeProjectUploadMode();
    },
    onProjectUploadParseModeChange(value) {
      this.projectUploadDialog.parse_mode = String(value || "quick").trim() || "quick";
    },
    onProjectUploadSectionSelect(node) {
      this.projectUploadDialog.section_id = String((node && node.section_id) || "").trim();
      this.projectUploadDialog.section_name = buildSectionDisplayLabel(
        node && node.section_id,
        (node && node.section_name) || (node && node.label) || ""
      );
    },
    onProjectUploadFileChange(file, fileList) {
      this.projectUploadDialog.file_list = fileList;
    },
    onProjectUploadFileRemove(file, fileList) {
      this.projectUploadDialog.file_list = fileList;
    },
    async submitProjectUpload() {
      const files = (this.projectUploadDialog.file_list || []).map((item) => item.raw).filter(Boolean);
      if (!files.length) {
        this.$message.warning("请选择上传文件");
        return;
      }
      if (
        this.projectUploadDialog.mode === "zip" &&
        files.some((item) => !String(item.name || "").toLowerCase().endsWith(".zip"))
      ) {
        this.$message.warning("ZIP 模式只允许上传 .zip 文件");
        return;
      }
      if (
        this.projectUploadDialog.mode !== "zip" &&
        files.some((item) => String(item.name || "").toLowerCase().endsWith(".zip"))
      ) {
        this.$message.warning("单文件或按章节上传不允许上传 .zip 文件");
        return;
      }
      if (
        this.projectUploadDialog.parse_mode === "detailed" &&
        files.some((item) => !String(item.name || "").toLowerCase().endsWith(".pdf"))
      ) {
        this.$message.warning("详细解析当前只支持 PDF 文件");
        return;
      }
      if (this.projectUploadDialog.mode === "section" && !this.projectUploadDialog.section_id) {
        this.$message.warning("请选择目标章节");
        return;
      }
      const formData = new FormData();
      files.forEach((file) => formData.append("files", file));
      formData.append("material_category", this.projectUploadDialog.registration_scope || "other");
      formData.append("mode", this.projectUploadDialog.mode);
      formData.append("parse_mode", this.projectUploadDialog.parse_mode);
      const sectionId =
        this.projectUploadDialog.mode === "section"
          ? this.projectUploadDialog.section_id
          : this.projectUploadDialog.branch && this.projectUploadDialog.branch !== "all"
          ? this.projectUploadDialog.branch
          : "";
      if (sectionId) {
        formData.append("section_id", sectionId);
      }
      this.uploading = true;
      try {
        const projectId = String(this.projectUploadDialog.project_id || "").trim();
        const res = await uploadSubmissionAsync(projectId, formData);
        const data = (res && res.data) || {};
        const taskId = String(data.task_id || "").trim();
        if (!taskId) {
          throw new Error("上传任务未返回 task_id");
        }
        const currentStates = Array.isArray(this.uploadTaskStateMap[projectId]) ? this.uploadTaskStateMap[projectId].slice() : [];
        currentStates.push({
          task_id: taskId,
          status: "pending",
          file_count: files.length,
          started_at: Date.now(),
        });
        this.$set(this.uploadTaskStateMap, projectId, currentStates);
        this.scheduleUploadPolling();
        await this.loadProjects();
        this.$message.success("文件已提交，正在解析");
        this.closeProjectUploadDialog();
      } catch (e) {
        const backendMessage = e && e.response && e.response.data && e.response.data.message;
        this.$message.error(backendMessage || (e && e.message) || "上传失败");
      } finally {
        this.uploading = false;
      }
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

.module-status-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.module-status-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #475569;
}

.module-status-item.parsing .module-name,
.module-status-item.parsing .module-count {
  color: #d97706;
}

.module-name {
  font-weight: 600;
  color: #1f2937;
}

.module-count {
  white-space: nowrap;
}

.project-run-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
