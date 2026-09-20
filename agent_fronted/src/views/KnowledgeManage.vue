<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">知识库管理 - {{ displayClassification || "全部类别" }}</h2>
        <div class="page-desc">按当前知识类别维护上传元信息、查询条件、异步解析和语义检索结果。</div>
      </div>
      <div class="header-actions">
        <el-button @click="openDeletionCleanups">删除清理记录</el-button>
        <el-button :disabled="!selectedRows.length" @click="clearSelected">取消选择</el-button>
        <el-button type="danger" :disabled="!selectedRows.length" @click="removeSelectedRows">批量删除</el-button>
        <el-button type="primary" :loading="uploading" @click="openUploadDialog">上传文件</el-button>
        <el-button type="success" :loading="sync.cde.loading" @click="openCDESyncDialog" style="margin-left: 12px">从 CDE 同步指导原则</el-button>
      </div>
    </div>

    <el-dialog title="待清理记录（全部知识类别）" :visible.sync="deletionCleanup.visible" width="800px">
      <el-alert type="warning" :closable="false" title="这些资料已停用，不应继续作为有效资料使用；以下资源清理尚未完成，可重试。" />
      <el-button size="small" :loading="deletionCleanup.loading" @click="loadDeletionCleanups">刷新</el-button>
      <el-table :data="deletionCleanup.list" v-loading="deletionCleanup.loading" row-key="doc_id">
        <el-table-column prop="file_name" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column label="待清理步骤" min-width="180">
          <template slot-scope="scope">{{ cleanupStepNames(scope.row.pending_steps) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template slot-scope="scope"><el-button type="text" :loading="!!deletionBusy[scope.row.doc_id]" @click="retryDeletionCleanup(scope.row)">重试清理</el-button></template>
        </el-table-column>
      </el-table>
      <el-pagination :current-page.sync="deletionCleanup.page" :page-size="20" :total="deletionCleanup.total" layout="total, prev, pager, next" @current-change="loadDeletionCleanups" />
    </el-dialog>

    <el-card class="k-card">
      <div slot="header">知识查询</div>

      <el-form :inline="true" size="small">
        <el-form-item label="文件名">
          <el-input v-model="filters.file_name" clearable />
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" clearable />
        </el-form-item>
        <el-form-item v-if="showFilter('affect_range')" :label="rangeLabel">
          <el-select v-model="filters.affect_range" clearable style="width: 160px">
            <el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="showFilter('profession_classification')" label="专业分类">
          <el-select v-model="filters.profession_classification" clearable style="width: 160px">
            <el-option v-for="item in professionOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="showFilter('registration_scope')" label="注册大类">
          <el-select v-model="filters.registration_scope" clearable style="width: 160px" @change="onFilterRegistrationScopeChange">
            <el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="showFilter('registration_path')" label="注册分类路径">
          <el-cascader v-model="filterRegistrationPath" :options="filterRegistrationOptions" :props="registrationCascaderProps" clearable filterable style="width: 300px" @change="onFilterRegistrationPathChange" />
        </el-form-item>
        <el-form-item v-if="showFilter('experience_type')" label="经验类型">
          <el-select v-model="filters.experience_type" clearable style="width: 160px">
            <el-option v-for="item in experienceTypeOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">查询</el-button>
        </el-form-item>
      </el-form>

      <el-form :inline="true" size="small" style="margin-top: 8px">
        <el-form-item label="语义检索">
          <el-input v-model="semantic.query" placeholder="请输入检索问题" style="width: 460px" />
        </el-form-item>
        <el-form-item>
          <el-button :loading="semantic.loading" @click="doSemantic">检索</el-button>
        </el-form-item>
      </el-form>

      <el-table ref="knowledgeTable" :data="table.list" :row-key="getRowKey" border class="k-table compact-table" style="margin-top: 12px" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="48" reserve-selection />
        <el-table-column prop="file_name" label="文件名" min-width="240" show-overflow-tooltip />
        <el-table-column v-if="showColumn('affect_range')" prop="affect_range" :label="rangeLabel" width="110" />
        <el-table-column v-if="showColumn('profession_classification')" prop="profession_classification" label="专业分类" width="120" />
        <el-table-column v-if="showColumn('registration_scope')" prop="registration_scope" label="注册大类" width="100" />
        <el-table-column v-if="showColumn('registration_path')" prop="registration_path" label="注册分类路径" min-width="180" show-overflow-tooltip />
        <el-table-column v-if="showColumn('experience_type')" prop="experience_type" label="经验类型" width="110" />
        <el-table-column label="解析状态" width="110">
          <template slot-scope="scope">
            <span v-if="scope.row.parse_status === 'running' || scope.row.parse_status === 'pending'"><i class="el-icon-loading" style="margin-right: 4px" />解析中</span>
            <span v-else-if="scope.row.parse_status === 'completed' || scope.row.is_chunked">已完成</span>
            <span v-else-if="scope.row.parse_status === 'failed'">失败</span>
            <span v-else>未开始</span>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="120">
          <template slot-scope="scope">
            <div class="progress-cell">
              <el-progress
                type="circle"
                :width="42"
                :stroke-width="5"
                :show-text="false"
                :percentage="Math.round((Number(scope.row.parse_progress || 0) || 0) * 100)"
                :status="progressStatus(scope.row)"
              />
              <div class="progress-number">{{ Math.round((Number(scope.row.parse_progress || 0) || 0) * 100) }}%</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="上传时间" width="165" />
        <el-table-column label="操作" width="200">
          <template slot-scope="scope">
            <el-button type="text" :disabled="scope.row.parse_status === 'running' || scope.row.parse_status === 'pending'" @click="triggerParse(scope.row)">解析</el-button>
            <el-button type="text" @click="editRow(scope.row)">编辑</el-button>
            <el-button type="text" style="color: #d03050" @click="removeRow(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination style="margin-top: 12px; text-align: right" layout="total, prev, pager, next" :total="table.total" :page-size="table.page_size" :current-page.sync="table.page" @current-change="search" />

      <el-divider>语义检索结果（按文档聚合）</el-divider>
      <el-empty v-if="!semantic.loading && !semantic.grouped_docs.length" description="暂无检索结果" />
      <div class="doc-card-list" v-loading="semantic.loading">
        <el-card v-for="doc in semantic.grouped_docs" :key="doc.doc_id" class="doc-card" shadow="hover">
          <div slot="header" class="doc-card-header">
            <div><span class="doc-id">{{ doc.doc_id }}</span></div>
            <el-tag size="mini" type="info">命中 {{ (doc.matched_hits || []).length }} 条</el-tag>
          </div>
          <div class="doc-summary" v-if="doc.doc_summary"><span class="label">整文摘要：</span><span>{{ doc.doc_summary }}</span></div>
          <div class="doc-keywords" v-if="(doc.doc_keywords || []).length">
            <span class="label">整文关键词：</span>
            <el-tag v-for="kw in doc.doc_keywords" :key="`${doc.doc_id}-${kw}`" size="mini" type="success" effect="plain" class="kw-tag">{{ kw }}</el-tag>
          </div>
          <div class="block-title">命中片段</div>
          <div v-if="!(doc.matched_hits || []).length" class="placeholder">无命中片段</div>
          <div v-for="(hit, idx) in doc.matched_hits || []" :key="`${doc.doc_id}-hit-${idx}`" class="hit-row">
            <div class="hit-meta">
              <el-tag size="mini" type="warning" effect="plain">{{ hit.item_type || "chunk" }}</el-tag>
              <span>chunk: {{ hit.chunk_id || "-" }}</span>
              <span>score: {{ formatScore(hit.score) }}</span>
              <span v-if="hit.page_start || hit.page_end">页码: {{ renderPageSpan(hit) }}</span>
            </div>
            <div class="hit-summary">{{ hit.summary || "（无摘要）" }}</div>
            <div v-if="hit.content" class="related-text">{{ hit.content }}</div>
          </div>

          <el-collapse class="related-collapse">
            <el-collapse-item name="related">
              <template slot="title">
                相关片段（同文组织）
                <el-tag size="mini" type="info" style="margin-left: 8px">{{ (doc.related_chunks || []).length }}</el-tag>
              </template>
              <div v-if="!(doc.related_chunks || []).length" class="placeholder">无相关片段</div>
              <div v-for="(rel, ridx) in doc.related_chunks || []" :key="`${doc.doc_id}-rel-${ridx}`" class="related-row">
                <div class="related-meta">
                  <span>chunk: {{ rel.chunk_id || "-" }}</span>
                  <span>order: {{ rel.chunk_order || "-" }}</span>
                  <span>page: {{ rel.page || rel.page_start || "-" }}</span>
                </div>
                <div class="related-summary">{{ rel.summary || "（无摘要）" }}</div>
                <div class="related-text">{{ rel.text || "" }}</div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </div>
    </el-card>

    <el-dialog title="上传知识文件" :visible.sync="uploadDialog.visible" width="620px">
      <el-form label-width="110px" size="small">
        <el-form-item label="选择文件">
          <input
            ref="uploadInput"
            class="hidden-upload-input"
            type="file"
            multiple
            accept=".pdf,.doc,.docx,.txt,.md,.zip"
            @change="onNativeFileChange"
          />
          <div class="upload-picker-box">
            <div class="upload-picker-text">支持 PDF、Word、TXT、Markdown、ZIP 压缩包。ZIP 会自动解压并批量导入。</div>
            <el-button size="small" @click="onPickFiles">选择文件</el-button>
          </div>
        </el-form-item>
        <el-form-item label="已选文件">
          <div v-if="uploadDialog.files.length" class="selected-file-list">
            <div v-for="file in uploadDialog.files" :key="`${file.name}-${file.size}`" class="selected-file-item">{{ file.name }}</div>
          </div>
          <div v-else class="placeholder">尚未选择文件</div>
        </el-form-item>
        <el-form-item v-if="showUploadField('affect_range')" :label="rangeLabel">
          <el-select v-model="uploadForm.affect_range" placeholder="请选择" style="width: 100%"><el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" /></el-select>
        </el-form-item>
        <el-form-item v-if="showUploadField('profession_classification')" label="专业分类">
          <el-select v-model="uploadForm.profession_classification" placeholder="请选择" style="width: 100%"><el-option v-for="item in professionOptions" :key="item" :label="item" :value="item" /></el-select>
        </el-form-item>
        <el-form-item v-if="showUploadField('registration_scope')" label="注册大类">
          <el-select v-model="uploadForm.registration_scope" placeholder="请选择" style="width: 100%" @change="onUploadRegistrationScopeChange"><el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" /></el-select>
        </el-form-item>
        <el-form-item v-if="showUploadField('registration_path')" label="注册分类路径">
          <el-cascader v-model="uploadRegistrationPath" :options="uploadRegistrationOptions" :props="registrationCascaderProps" clearable filterable style="width: 100%" @change="onUploadRegistrationPathChange" />
        </el-form-item>
        <el-form-item v-if="showUploadField('experience_type')" label="经验类型">
          <el-select v-model="uploadForm.experience_type" placeholder="请选择" style="width: 100%"><el-option v-for="item in experienceTypeOptions" :key="item" :label="item" :value="item" /></el-select>
        </el-form-item>
      </el-form>
      <span slot="footer"><el-button @click="closeUploadDialog">取消</el-button><el-button type="primary" :loading="uploading" @click="confirmUpload">开始上传</el-button></span>
    </el-dialog>

    <el-dialog title="编辑知识文件" :visible.sync="edit.visible" width="560px">
      <el-form label-width="110px" size="small">
        <el-form-item label="文件名"><el-input v-model="edit.file_name" /></el-form-item>
        <el-form-item v-if="showUploadField('affect_range')" :label="rangeLabel"><el-select v-model="edit.affect_range" style="width: 100%"><el-option label="other" value="other" /><el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" /></el-select></el-form-item>
        <el-form-item v-if="showUploadField('profession_classification')" label="专业分类"><el-select v-model="edit.profession_classification" style="width: 100%"><el-option label="other" value="other" /><el-option v-for="item in professionOptions" :key="item" :label="item" :value="item" /></el-select></el-form-item>
        <el-form-item v-if="showUploadField('registration_scope')" label="注册大类"><el-select v-model="edit.registration_scope" style="width: 100%" @change="onEditRegistrationScopeChange"><el-option label="other" value="other" /><el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" /></el-select></el-form-item>
        <el-form-item v-if="showUploadField('registration_path')" label="注册分类路径"><el-cascader v-model="editRegistrationPath" :options="editRegistrationOptions" :props="registrationCascaderProps" clearable filterable style="width: 100%" @change="onEditRegistrationPathChange" /></el-form-item>
        <el-form-item v-if="showUploadField('experience_type')" label="经验类型"><el-select v-model="edit.experience_type" style="width: 100%"><el-option label="other" value="other" /><el-option v-for="item in experienceTypeOptions" :key="item" :label="item" :value="item" /></el-select></el-form-item>
      </el-form>
      <span slot="footer"><el-button @click="edit.visible = false">取消</el-button><el-button type="primary" @click="saveEdit">保存</el-button></span>
    </el-dialog>

    <!-- CDE 指导原则同步弹窗 -->
    <el-dialog title="从 CDE 同步指导原则" :visible.sync="sync.cde.dialogVisible" width="900px">
      <div class="cde-sync-content">
        <!-- 筛选条件 -->
        <el-form :inline="true" size="small" class="cde-sync-filters">
          <el-form-item label="适用范围">
            <el-select v-model="sync.cde.classify1" clearable style="width: 140px">
              <el-option label="全部" value="all" />
              <el-option label="中药" value="traditional_chinese_medicine" />
              <el-option label="化学药" value="chemical_drug" />
              <el-option label="生物制品" value="biological_product" />
            </el-select>
          </el-form-item>
          <el-form-item label="专业分类">
            <el-select v-model="sync.cde.classify2" clearable style="width: 140px">
              <el-option label="全部" value="all" />
              <el-option label="药学" value="pharmacy" />
              <el-option label="临床" value="clinical" />
              <el-option label="非临床" value="non_clinical" />
              <el-option label="临床药理" value="clinical_pharmacology" />
              <el-option label="生物统计" value="biostatistics" />
              <el-option label="多学科" value="multidisciplinary" />
            </el-select>
          </el-form-item>
          <el-form-item label="发布日期">
            <el-date-picker
              v-model="sync.cde.dateRange"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="yyyy-MM-dd"
              style="width: 260px"
            />
          </el-form-item>
          <el-form-item label="关键词">
            <el-input v-model="sync.cde.keyword" placeholder="搜索指导原则" clearable style="width: 200px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="sync.cde.loading" @click="fetchCDEList">获取列表</el-button>
          </el-form-item>
        </el-form>

        <!-- 对比结果表格 -->
        <div v-if="sync.cde.guidelines.length > 0" class="cde-sync-results">
          <el-divider>对比结果</el-divider>
          <el-table ref="cdeTable" :data="sync.cde.guidelines" border style="width: 100%" max-height="400" @selection-change="onCDEListSelectionChange">
            <el-table-column type="selection" width="50" :selectable="canSelect" />
            <el-table-column prop="title" label="指导原则名称" min-width="300" show-overflow-tooltip />
            <el-table-column prop="issueDate" label="发布日期" width="120" />
            <el-table-column label="状态" width="100">
              <template slot-scope="scope">
                <el-tag v-if="scope.row._status === 'existing'" type="info" size="small">已存在</el-tag>
                <el-tag v-else-if="scope.row._status === 'new'" type="success" size="small">新增</el-tag>
                <el-tag v-else-if="scope.row._status === 'updated'" type="warning" size="small">可更新</el-tag>
              </template>
            </el-table-column>
          </el-table>

          <div class="cde-sync-stats">
            <span>共 {{ sync.cde.guidelines.length }} 条</span>
            <span style="margin-left: 16px; color: #67c23a">新增: {{ sync.cde.stats.new }}</span>
            <span style="margin-left: 16px; color: #909399">已存在: {{ sync.cde.stats.existing }}</span>
            <span style="margin-left: 16px; color: #e6a23c">可更新: {{ sync.cde.stats.updated }}</span>
          </div>
        </div>

        <!-- 同步进度 -->
        <div v-if="sync.cde.syncing" class="cde-sync-progress">
          <el-divider>同步进度</el-divider>
          <el-progress
            :percentage="sync.cde.progress"
            :status="sync.cde.progressStatus"
          />
          <div class="progress-detail">{{ sync.cde.progressDetail }}</div>
        </div>
      </div>

      <span slot="footer">
        <el-button @click="sync.cde.dialogVisible = false">关闭</el-button>
        <el-button
          type="primary"
          :loading="sync.cde.syncing"
          :disabled="!sync.cde.selectedItems.length"
          @click="executeCESync"
        >
          开始同步 ({{ sync.cde.selectedItems.length }} 项)
        </el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import {
  batchDeleteKnowledge,
  deleteKnowledge,
  listDeletionCleanups,
  parseKnowledge,
  queryKnowledge,
  semanticQuery,
  updateKnowledge,
  uploadKnowledge,
  fetchCDEGuidelines,
  syncCDEGuidelines,
} from "@/api/knowledge";
import {
  EXPERIENCE_TYPE_OPTIONS,
  KNOWLEDGE_FORM_OPTIONS,
  getKnowledgeCategoryMeta,
  getRegistrationOptionsByScope,
} from "@/constants/knowledgeCategory";

export default {
  name: "KnowledgeManage",
  props: {
    classification: {
      type: String,
      default: "",
    },
    displayNameProp: {
      type: String,
      default: "",
    },
  },
  data() {
    return {
      uploading: false,
      querying: false,
      pendingQuery: false,
      selectedRows: [],
      deletionBusy: {},
      deletionCleanup: { visible: false, loading: false, list: [], total: 0, page: 1, token: 0 },
      progressStream: null,
      filters: {
        file_name: "",
        keyword: "",
        file_type: "",
        classification: "",
        affect_range: "",
        profession_classification: "",
        registration_scope: "",
        registration_path: "",
        experience_type: "",
      },
      filterRegistrationPath: [],
      table: { list: [], total: 0, page: 1, page_size: 10 },
      semantic: { query: "", hits: [], grouped_docs: [], loading: false },
      semanticRequestToken: 0,
      uploadDialog: { visible: false, files: [] },
      uploadForm: {
        affect_range: KNOWLEDGE_FORM_OPTIONS.affect_range[0] || "",
        profession_classification: KNOWLEDGE_FORM_OPTIONS.profession_classification[0] || "",
        registration_scope: KNOWLEDGE_FORM_OPTIONS.affect_range[0] || "",
        registration_path: "",
        experience_type: EXPERIENCE_TYPE_OPTIONS[0] || "",
      },
      uploadRegistrationPath: [],
      edit: {
        visible: false,
        doc_id: "",
        file_name: "",
        affect_range: "other",
        profession_classification: "other",
        registration_scope: "other",
        registration_path: "",
        experience_type: "other",
      },
      editRegistrationPath: [],
      affectRangeOptions: KNOWLEDGE_FORM_OPTIONS.affect_range,
      professionOptions: KNOWLEDGE_FORM_OPTIONS.profession_classification,
      experienceTypeOptions: EXPERIENCE_TYPE_OPTIONS,
      registrationCascaderProps: { value: "value", label: "label", children: "children", emitPath: true, checkStrictly: false },
      sync: {
        cde: {
          dialogVisible: false,
          loading: false,
          syncing: false,
          classify1: "all",
          classify2: "all",
          dateRange: [],
          keyword: "",
          guidelines: [],
          stats: { new: 0, existing: 0, updated: 0 },
          selectedItems: [],
          progress: 0,
          progressStatus: "",
          progressDetail: "",
        },
      },
    };
  },
  computed: {
    fixedClassification() {
      return this.classification || (this.$route.meta && this.$route.meta.classification) || "";
    },
    categoryMeta() {
      return getKnowledgeCategoryMeta(this.fixedClassification);
    },
    displayClassification() {
      return this.displayNameProp || this.categoryMeta.displayName || this.fixedClassification;
    },
    rangeLabel() {
      return this.categoryMeta.mode === "review_rule" ? "注册大类" : "作用范围";
    },
    filterRegistrationOptions() {
      return getRegistrationOptionsByScope(this.filters.registration_scope);
    },
    uploadRegistrationOptions() {
      return getRegistrationOptionsByScope(this.uploadForm.registration_scope);
    },
    editRegistrationOptions() {
      return getRegistrationOptionsByScope(this.edit.registration_scope);
    },
  },
  watch: {
    "$route.path"() {
      this.applyCategoryFromRoute();
      this.onSearch();
    },
  },
  mounted() {
    this.applyCategoryFromRoute();
    this.search();
  },
  beforeDestroy() {
    this.deletionCleanup.token += 1;
    this.semanticRequestToken += 1;
    this.closeProgressStream();
  },
  methods: {
    cleanupStepNames(steps) {
      const labels = { source: '原文件', parsed: '解析文件', vectors: '检索索引', status: '状态确认' };
      return (steps || []).map(step => labels[step] || '待核查步骤').join('、');
    },
    openDeletionCleanups() {
      this.deletionCleanup.visible = true;
      this.deletionCleanup.page = 1;
      this.loadDeletionCleanups();
    },
    async loadDeletionCleanups() {
      const token = ++this.deletionCleanup.token;
      this.deletionCleanup.loading = true;
      try {
        const res = await listDeletionCleanups({ page: this.deletionCleanup.page, page_size: 20 });
        if (token !== this.deletionCleanup.token || this._isDestroyed) return;
        const data = this.unwrapApiData(res);
        this.deletionCleanup.list = data.list || [];
        this.deletionCleanup.total = Number(data.total || 0);
        if (!this.deletionCleanup.list.length && this.deletionCleanup.page > 1) {
          this.deletionCleanup.page = Math.max(1, Math.ceil(this.deletionCleanup.total / 20));
          return this.loadDeletionCleanups();
        }
      } catch (_) {
        if (token === this.deletionCleanup.token && !this._isDestroyed) this.$message.error('清理记录读取失败，已保留列表，请重试');
      } finally {
        if (token === this.deletionCleanup.token && !this._isDestroyed) this.deletionCleanup.loading = false;
      }
    },
    showDeletionResult(data) {
      if (data.cleanup_state === 'completed') this.$message.success('资料已删除，相关文件和索引已清理');
      else this.$message.warning('资料已停用，部分资源待清理；请在“删除清理记录”中核对和重试');
    },
    async retryDeletionCleanup(row) {
      if (this.deletionBusy[row.doc_id]) return;
      this.$set(this.deletionBusy, row.doc_id, true);
      try {
        const res = await deleteKnowledge(row.doc_id);
        if (this._isDestroyed) return;
        this.showDeletionResult(this.unwrapApiData(res));
        await this.loadDeletionCleanups();
      } catch (_) {
        // HTTP层展示安全错误；保留列表供人工重试。
      } finally {
        if (!this._isDestroyed) this.$delete(this.deletionBusy, row.doc_id);
      }
    },
    unwrapApiData(res) {
      if (res && typeof res === "object" && res.data && typeof res.data === "object") {
        return res.data;
      }
      return res || {};
    },
    hasActiveParseTasks() {
      return (this.table.list || []).some((item) => ["pending", "running"].includes(String(item.parse_status || "")));
    },
    syncProgressStream() {
      if (this.hasActiveParseTasks()) {
        if (!this.progressStream) {
          this.openProgressStream();
        }
        return;
      }
      this.closeProgressStream();
    },
    openProgressStream() {
      if (this.progressStream || typeof EventSource === "undefined") {
        return;
      }
      if (typeof EventSource === "undefined") {
        return;
      }
      const source = new EventSource("/api/knowledge/parse-progress/stream");
      this.progressStream = source;
      const handlePayload = (payload) => {
        if (!payload || !Array.isArray(payload.tasks) || !payload.tasks.length) return;
        const taskMap = new Map(
          payload.tasks
            .filter((item) => item && item.doc_id)
            .map((item) => [
              item.doc_id,
              {
                parse_status: item.status || "unknown",
                parse_progress: typeof item.progress === "number" ? item.progress : Number(item.progress || 0) || 0,
                parse_message: item.message || "",
              },
            ])
        );
        if (!taskMap.size) return;
        this.table.list = (this.table.list || []).map((item) => {
          const task = taskMap.get(item.doc_id);
          return task ? { ...item, ...task } : item;
        });
        this.syncProgressStream();
      };
      source.addEventListener("progress", (event) => {
        try {
          handlePayload(JSON.parse(event.data || "{}"));
        } catch (_) {}
      });
      source.addEventListener("error", () => {
        this.closeProgressStream();
        window.setTimeout(() => {
          this.openProgressStream();
        }, 3000);
      });
    },
    closeProgressStream() {
      if (this.progressStream) {
        this.progressStream.close();
        this.progressStream = null;
      }
    },
    getRowKey(row) {
      return row && row.doc_id;
    },
    applyCategoryFromRoute() {
      this.affectRangeOptions = this.categoryMeta.affectRangeOptions || KNOWLEDGE_FORM_OPTIONS.affect_range;
      this.professionOptions = this.categoryMeta.professionOptions || KNOWLEDGE_FORM_OPTIONS.profession_classification;
      this.filters.classification = this.fixedClassification || "";
      this.filters.affect_range = "";
      this.filters.profession_classification = "";
      this.filters.registration_scope = "";
      this.filters.registration_path = "";
      this.filters.experience_type = "";
      this.filterRegistrationPath = [];
      this.uploadForm.affect_range = this.affectRangeOptions[0] || "";
      this.uploadForm.profession_classification = this.professionOptions[0] || "";
    },
    showFilter(field) {
      return this.categoryMeta.queryFields.includes(field);
    },
    showUploadField(field) {
      return this.categoryMeta.uploadFields.includes(field);
    },
    showColumn(field) {
      return this.showFilter(field) || this.showUploadField(field);
    },
    openUploadDialog() {
      this.uploadDialog.visible = true;
    },
    onPickFiles() {
      if (this.uploading || !this.$refs.uploadInput) return;
      this.$refs.uploadInput.value = "";
      this.$refs.uploadInput.click();
    },
    onNativeFileChange(event) {
      const files = Array.from((event && event.target && event.target.files) || []);
      if (!files.length) return;
      this.uploadDialog.files = files;
    },
    closeUploadDialog() {
      this.uploadDialog.visible = false;
      this.uploadDialog.files = [];
      this.uploadForm = {
        affect_range: this.affectRangeOptions[0] || "",
        profession_classification: this.professionOptions[0] || "",
        registration_scope: this.affectRangeOptions[0] || "",
        registration_path: "",
        experience_type: this.experienceTypeOptions[0] || "",
      };
      this.uploadRegistrationPath = this.showUploadField("registration_scope") ? [this.uploadForm.registration_scope] : [];
    },
    onFilterRegistrationScopeChange() {
      this.filterRegistrationPath = this.filters.registration_scope ? [this.filters.registration_scope] : [];
      this.filters.registration_path = "";
    },
    onFilterRegistrationPathChange(path) {
      this.filterRegistrationPath = Array.isArray(path) ? path : [];
      this.filters.registration_scope = this.filterRegistrationPath[0] || this.filters.registration_scope;
      this.filters.registration_path = this.filterRegistrationPath.join(" > ");
    },
    onUploadRegistrationScopeChange() {
      this.uploadRegistrationPath = this.uploadForm.registration_scope ? [this.uploadForm.registration_scope] : [];
      this.uploadForm.registration_path = "";
    },
    onUploadRegistrationPathChange(path) {
      this.uploadRegistrationPath = Array.isArray(path) ? path : [];
      this.uploadForm.registration_scope = this.uploadRegistrationPath[0] || this.uploadForm.registration_scope;
      this.uploadForm.registration_path = this.uploadRegistrationPath.join(" > ");
    },
    onEditRegistrationScopeChange() {
      this.editRegistrationPath = this.edit.registration_scope && this.edit.registration_scope !== "other" ? [this.edit.registration_scope] : [];
      this.edit.registration_path = "";
    },
    onEditRegistrationPathChange(path) {
      this.editRegistrationPath = Array.isArray(path) ? path : [];
      this.edit.registration_scope = this.editRegistrationPath[0] || this.edit.registration_scope;
      this.edit.registration_path = this.editRegistrationPath.join(" > ");
    },
    progressStatus(row) {
      if (row.parse_status === "failed") return "exception";
      if (row.parse_status === "completed" || row.is_chunked) return "success";
      return undefined;
    },
    formatScore(value) {
      const num = Number(value);
      return Number.isFinite(num) ? num.toFixed(4) : "-";
    },
    renderPageSpan(hit) {
      if (hit.page_start && hit.page_end && hit.page_start !== hit.page_end) return `${hit.page_start}-${hit.page_end}`;
      return hit.page_start || hit.page_end || "-";
    },
    onSearch() {
      this.table.page = 1;
      this.search();
    },
    async search() {
      if (this.querying) {
        this.pendingQuery = true;
        return;
      }
      this.querying = true;
      try {
        const res = await queryKnowledge({
          file_name: this.filters.file_name,
          keyword: this.filters.keyword,
          file_type: this.filters.file_type,
          classification: this.filters.classification,
          affect_range: this.filters.affect_range,
          profession_classification: this.filters.profession_classification,
          registration_scope: this.filters.registration_scope,
          registration_path: this.filters.registration_path,
          experience_type: this.filters.experience_type,
          page: this.table.page,
          page_size: this.table.page_size,
        });
        if (this.pendingQuery) {
          return;
        }
        const data = this.unwrapApiData(res);
        const list = Array.isArray(data.list) ? data.list : [];
        this.table.list = list.map((item) => ({
          ...item,
          parse_status: item.parse_status || (item.is_chunked ? "completed" : "unknown"),
          parse_progress: typeof item.parse_progress === "number" ? item.parse_progress : item.is_chunked ? 1 : 0,
          affect_range: item.affect_range || "other",
          profession_classification: item.profession_classification || "other",
          registration_scope: item.registration_scope || "other",
          registration_path: item.registration_path || "",
          experience_type: item.experience_type || "other",
        }));
        this.table.total = Number(data.total || 0);
        this.syncSelectedRows();
        this.syncProgressStream();
      } catch (error) {
        if (this.pendingQuery) {
          return;
        }
        this.table.list = [];
        this.table.total = 0;
        this.selectedRows = [];
        this.closeProgressStream();
        const msg = (error && error.message) || "加载知识库列表失败";
        this.$message.error(msg);
      } finally {
        this.querying = false;
        if (this.pendingQuery) {
          this.pendingQuery = false;
          this.search();
        }
      }
    },
    async confirmUpload() {
      if (!this.uploadDialog.files.length) {
        this.$message.warning("请先选择上传文件");
        return;
      }
      await this.uploadSelectedFiles(this.uploadDialog.files);
      this.closeUploadDialog();
    },
    async uploadSelectedFiles(files) {
      this.uploading = true;
      const classification = this.fixedClassification || this.filters.classification || "指导原则";
      try {
        const fd = new FormData();
        files.forEach((file) => fd.append("files", file));
        fd.append("classification", classification);
        fd.append("affect_range", this.showUploadField("affect_range") ? this.uploadForm.affect_range : "other");
        fd.append("profession_classification", this.showUploadField("profession_classification") ? this.uploadForm.profession_classification : "other");
        fd.append("registration_scope", this.showUploadField("registration_scope") ? this.uploadForm.registration_scope : "other");
        fd.append("registration_path", this.showUploadField("registration_path") ? this.uploadForm.registration_path : "");
        fd.append("experience_type", this.showUploadField("experience_type") ? this.uploadForm.experience_type : "other");

        const res = await uploadKnowledge(fd);
        const payload = this.unwrapApiData(res);
        const items = Array.isArray(payload.items) ? payload.items : (payload.doc_id ? [payload] : []);
        const successCount = Number(payload.success_count || items.length || 0);
        const failCount = Number(payload.fail_count || 0);
        if (successCount) {
          this.$message.success(`上传成功 ${successCount} 个文件`);
        }
        if (failCount) {
          this.$message.warning(`有 ${failCount} 个文件上传失败`);
        }
        await this.search();
      } finally {
        this.uploading = false;
      }
    },
    async doSemantic() {
      const requestToken = this.semanticRequestToken + 1;
      this.semanticRequestToken = requestToken;
      const query = String(this.semantic.query || "").trim();
      const classification = this.filters.classification || "";
      if (!query) {
        this.semantic.loading = false;
        this.$message.warning("请输入检索问题");
        return;
      }
      this.semantic.loading = true;
      try {
        const res = await semanticQuery(
          { query, top_k: 10, min_score: 0.6, classification },
          { silentError: true },
        );
        if (this.semanticRequestToken !== requestToken) return;
        const data = this.unwrapApiData(res);
        const list = Array.isArray(data.list) ? data.list : Array.isArray(data.hits) ? data.hits : [];
        this.semantic.hits = list;
        this.semantic.grouped_docs = Array.isArray(data.grouped_docs) ? data.grouped_docs : this.buildGroupedFromHits(list);
      } catch (error) {
        if (this.semanticRequestToken !== requestToken) return;
        this.semantic.hits = [];
        this.semantic.grouped_docs = [];
        const msg = (error && error.userMessage) || (error && error.message) || "语义检索失败";
        this.$message.error(msg);
      } finally {
        if (this.semanticRequestToken === requestToken) {
          this.semantic.loading = false;
        }
      }
    },
    editRow(row) {
      this.edit = { visible: true, doc_id: row.doc_id, file_name: row.file_name, affect_range: row.affect_range || "other", profession_classification: row.profession_classification || "other", registration_scope: row.registration_scope || "other", registration_path: row.registration_path || "", experience_type: row.experience_type || "other" };
      this.editRegistrationPath = row.registration_path ? String(row.registration_path).split(" > ") : [];
    },
    async saveEdit() {
      try {
        await updateKnowledge(this.edit.doc_id, { file_name: this.edit.file_name, affect_range: this.edit.affect_range, profession_classification: this.edit.profession_classification, registration_scope: this.edit.registration_scope, registration_path: this.edit.registration_path, experience_type: this.edit.experience_type });
        this.$message.success("保存成功");
        this.edit.visible = false;
        this.search();
      } catch (_) {}
    },
    async removeRow(row) {
      if (this.deletionBusy[row.doc_id]) return;
      this.$set(this.deletionBusy, row.doc_id, true);
      try {
        await this.$confirm(`确认删除 ${row.file_name} ?`, "提示", { type: "warning" });
        const res = await deleteKnowledge(row.doc_id);
        if (this._isDestroyed) return;
        this.showDeletionResult(this.unwrapApiData(res));
        this.search();
      } catch (_) {} finally {
        if (!this._isDestroyed) this.$delete(this.deletionBusy, row.doc_id);
      }
    },
    onSelectionChange(rows) {
      this.selectedRows = Array.isArray(rows) ? rows : [];
    },
    onCDEListSelectionChange(rows) {
      this.sync.cde.selectedItems = Array.isArray(rows) ? rows : [];
    },
    clearSelected() {
      if (this.$refs.knowledgeTable) {
        this.$refs.knowledgeTable.clearSelection();
      }
      this.selectedRows = [];
    },
    syncSelectedRows() {
      const selectedIds = new Set((this.selectedRows || []).map((item) => item.doc_id));
      this.selectedRows = this.table.list.filter((item) => selectedIds.has(item.doc_id));
    },
    async removeSelectedRows() {
      if (!this.selectedRows.length) return;
      const docIds = this.selectedRows.map((item) => item.doc_id).filter(Boolean);
      const fileNames = this.selectedRows.map((item) => item.file_name).filter(Boolean);
      try {
        await this.$confirm(`确认批量删除 ${docIds.length} 个文件？\n${fileNames.slice(0, 5).join("\n")}${fileNames.length > 5 ? "\n..." : ""}`, "提示", { type: "warning" });
        const res = await batchDeleteKnowledge(docIds);
        const data = this.unwrapApiData(res);
        const deletedCount = Number(data.deleted_count || 0);
        const failedCount = Number(data.failed_count || 0);
        const pendingCount = Number(data.cleanup_pending_count || 0);
        if (pendingCount) {
          this.$message.warning(`已停用 ${deletedCount} 份资料，其中 ${pendingCount} 份资源待清理，请查看删除清理记录`);
        } else if (deletedCount) {
          this.$message.success(`已删除并清理 ${deletedCount} 份资料`);
        }
        if (failedCount) {
          this.$message.warning(`有 ${failedCount} 个文件删除失败`);
        }
        this.clearSelected();
        this.search();
      } catch (_) {}
    },
    async triggerParse(row) {
      if (!row || !row.doc_id) return;
      try {
        await parseKnowledge(row.doc_id);
        this.$message.success("已提交解析任务");
        this.table.list = this.table.list.map((item) => item.doc_id === row.doc_id ? { ...item, parse_status: "pending", parse_progress: 0 } : item);
        this.syncProgressStream();
      } catch (error) {
        const msg = (error && error.response && error.response.data && error.response.data.message) || error.message || "提交解析失败";
        this.$message.error(msg);
      }
    },
    // CDE 同步相关方法
    openCDESyncDialog() {
      this.sync.cde.dialogVisible = true;
      this.sync.cde.guidelines = [];
      this.sync.cde.stats = { new: 0, existing: 0, updated: 0 };
      this.sync.cde.selectedItems = [];
    },
    canSelect(row) {
      return row._status === "new" || row._status === "updated";
    },
    // 生成所有分类组合
    generateAllCombinations() {
      const affectRanges = ["traditional_chinese_medicine", "chemical_drug", "biological_product"];
      const professionClassifications = ["pharmacy", "clinical", "non_clinical", "clinical_pharmacology", "biostatistics", "multidisciplinary"];
      const combinations = [];
      // 遍历所有组合
      for (const affectRange of affectRanges) {
        for (const profession of professionClassifications) {
          combinations.push({ classify1: affectRange, classify2: profession });
        }
      }
      return combinations;
    },
    // 判断是否需要全遍历
    isAllMode() {
      return this.sync.cde.classify1 === "all" || this.sync.cde.classify2 === "all";
    },
    async fetchCDEList() {
      this.sync.cde.loading = true;
      this.sync.cde.guidelines = [];
      this.sync.cde.selectedItems = [];

      // 如果是"全部"模式，需要遍历所有组合
      if (this.isAllMode()) {
        try {
          await this.fetchAllClassifications();
        } catch (error) {
          const msg = (error && error.message) || "获取 CDE 列表失败";
          this.$message.error(msg);
        } finally {
          this.sync.cde.loading = false;
        }
        return;
      }

      // 单个分类直接获取
      try {
        const params = {
          classify1: this.sync.cde.classify1,
          classify2: this.sync.cde.classify2,
          keyword: this.sync.cde.keyword,
        };
        if (this.sync.cde.dateRange && this.sync.cde.dateRange.length === 2) {
          params.start_date = this.sync.cde.dateRange[0];
          params.end_date = this.sync.cde.dateRange[1];
        }
        const res = await fetchCDEGuidelines(params);
        const data = this.unwrapApiData(res);
        if (data && data.success) {
          const guidelines = data.guidelines || [];
          const comparison = data.comparison || {};
          const newItems = comparison.new || [];
          const existingItems = comparison.existing || [];
          const updatedItems = comparison.updated || [];
          // 标记状态
          this.sync.cde.guidelines = guidelines.map((item) => {
            const isNew = newItems.some((g) => g.zdyzIdCODE === item.zdyzIdCODE);
            const isExisting = existingItems.some((g) => g.local_doc_id || g.title === item.title);
            const isUpdated = updatedItems.some((g) => g.title === item.title);
            return {
              ...item,
              _status: isNew ? "new" : (isUpdated ? "updated" : "existing"),
            };
          });
          this.sync.cde.stats = {
            new: newItems.length,
            existing: existingItems.length,
            updated: updatedItems.length,
          };
          // 自动选中新增的
          this.$nextTick(() => {
            this.sync.cde.guidelines.forEach((item, index) => {
              if (item._status === "new") {
                this.$refs.cdeTable && this.$refs.cdeTable.toggleRowSelection(this.sync.cde.guidelines[index], true);
              }
            });
          });
          this.$message.success(`获取 ${guidelines.length} 条指导原则`);
        } else {
          this.$message.error(data && data.error ? data.error : "获取列表失败");
        }
      } catch (error) {
        const msg = (error && error.message) || "获取 CDE 列表失败";
        this.$message.error(msg);
      } finally {
        this.sync.cde.loading = false;
      }
    },
    async fetchAllClassifications() {
      const combinations = this.generateAllCombinations();
      let totalFetched = 0;
      let totalNew = 0;
      let totalExisting = 0;
      let totalUpdated = 0;
      let allGuidelines = [];

      this.$message.info(`开始遍历 ${combinations.length} 种分类组合...`);

      for (let i = 0; i < combinations.length; i++) {
        const combo = combinations[i];
        const progressMsg = `(${i + 1}/${combinations.length}) ${combo.classify1} / ${combo.classify2}`;
        this.sync.cde.progressDetail = progressMsg;
        this.sync.cde.progress = Math.round(((i + 1) / combinations.length) * 100);

        try {
          const params = {
            classify1: combo.classify1,
            classify2: combo.classify2,
            keyword: this.sync.cde.keyword,
          };
          if (this.sync.cde.dateRange && this.sync.cde.dateRange.length === 2) {
            params.start_date = this.sync.cde.dateRange[0];
            params.end_date = this.sync.cde.dateRange[1];
          }
          const res = await fetchCDEGuidelines(params);
          const data = this.unwrapApiData(res);
          if (data && data.success) {
            const guidelines = data.guidelines || [];
            const comparison = data.comparison || {};
            const newItems = comparison.new || [];
            const existingItems = comparison.existing || [];
            const updatedItems = comparison.updated || [];
            totalFetched += guidelines.length;
            totalNew += newItems.length;
            totalExisting += existingItems.length;
            totalUpdated += updatedItems.length;
            // 添加到总列表，并标记分类信息
            guidelines.forEach((item) => {
              allGuidelines.push({
                ...item,
                _classify1: combo.classify1,
                _classify2: combo.classify2,
              });
            });
          }
        } catch (error) {
          console.error(`获取 ${combo.classify1}/${combo.classify2} 失败:`, error);
        }
      }

      // 合并所有结果并去重（基于 zdyzIdCODE）
      const uniqueMap = new Map();
      allGuidelines.forEach((item) => {
        if (!uniqueMap.has(item.zdyzIdCODE)) {
          uniqueMap.set(item.zdyzIdCODE, item);
        }
      });

      const allUnique = Array.from(uniqueMap.values());

      // 基于已收集的统计设置状态
      this.sync.cde.guidelines = allUnique.map((item) => {
        return {
          ...item,
          _status: "new", // 假设都是新的，实际对比在后续进行
        };
      });

      this.sync.cde.stats = {
        new: totalNew,
        existing: totalExisting,
        updated: totalUpdated,
      };

      this.sync.cde.progress = 100;
      this.sync.cde.progressDetail = `完成：共获取 ${totalFetched} 条，新增 ${totalNew} 条`;
      this.$message.success(`获取完成，共 ${allUnique.length} 条指导原则`);
    },
    async executeCESync() {
      if (!this.sync.cde.selectedItems.length) {
        this.$message.warning("请先选择要同步的指导原则");
        return;
      }
      this.sync.cde.syncing = true;
      this.sync.cde.progress = 0;
      this.sync.cde.progressStatus = "";
      this.sync.cde.progressDetail = "准备同步...";

      try {
        // 如果是全部模式，按分类组合批量同步
        if (this.isAllMode()) {
          await this.executeAllModeSync();
          return;
        }

        // 单个分类直接同步
        const params = {
          classify1: this.sync.cde.classify1,
          classify2: this.sync.cde.classify2,
          keyword: this.sync.cde.keyword,
          auto_download: true,
        };
        if (this.sync.cde.dateRange && this.sync.cde.dateRange.length === 2) {
          params.start_date = this.sync.cde.dateRange[0];
          params.end_date = this.sync.cde.dateRange[1];
        }
        const res = await syncCDEGuidelines(params);
        const data = this.unwrapApiData(res);
        if (data && data.success) {
          this.sync.cde.progress = 100;
          this.sync.cde.progressStatus = "success";
          this.sync.cde.progressDetail = `同步完成：下载 ${data.downloaded_count || 0} 个文件`;
          this.$message.success(`同步完成：下载 ${data.downloaded_count || 0} 个文件`);
          await this.search(); // 刷新列表
        } else {
          this.sync.cde.progressStatus = "exception";
          this.sync.cde.progressDetail = data && data.error ? data.error : "同步失败";
          this.$message.error(data && data.error ? data.error : "同步失败");
        }
      } catch (error) {
        this.sync.cde.progressStatus = "exception";
        const msg = (error && error.message) || "同步失败";
        this.sync.cde.progressDetail = msg;
        this.$message.error(msg);
      } finally {
        this.sync.cde.syncing = false;
      }
    },
    async executeAllModeSync() {
      // 按分类组合批量同步（仅同步新增的）
      const combinations = this.generateAllCombinations();
      let totalDownloaded = 0;
      let totalFailed = 0;

      // 过滤出新增的项目
      const newItems = this.sync.cde.guidelines.filter(item => item._status === "new");
      if (newItems.length === 0) {
        this.$message.info("没有新增的指导原则需要同步");
        this.sync.cde.progressStatus = "success";
        return;
      }

      this.$message.info(`开始批量同步 ${newItems.length} 条新增指导原则...`);

      // 按分类组合分组
      const grouped = {};
      newItems.forEach(item => {
        const key = `${item._classify1 || this.sync.cde.classify1}_${item._classify2 || this.sync.cde.classify2}`;
        if (!grouped[key]) {
          grouped[key] = {
            classify1: item._classify1 || this.sync.cde.classify1,
            classify2: item._classify2 || this.sync.cde.classify2,
            items: [],
          };
        }
        grouped[key].items.push(item);
      });

      const groupKeys = Object.keys(grouped);
      for (let i = 0; i < groupKeys.length; i++) {
        const group = grouped[groupKeys[i]];
        this.sync.cde.progressDetail = `同步中 (${i + 1}/${groupKeys.length}): ${group.classify1} / ${group.classify2}`;
        this.sync.cde.progress = Math.round(((i + 1) / groupKeys.length) * 100);

        try {
          const params = {
            classify1: group.classify1,
            classify2: group.classify2,
            keyword: this.sync.cde.keyword,
            auto_download: true,
          };
          if (this.sync.cde.dateRange && this.sync.cde.dateRange.length === 2) {
            params.start_date = this.sync.cde.dateRange[0];
            params.end_date = this.sync.cde.dateRange[1];
          }
          const res = await syncCDEGuidelines(params);
          const data = this.unwrapApiData(res);
          if (data && data.success) {
            totalDownloaded += data.downloaded_count || 0;
            totalFailed += data.failed_count || 0;
          }
        } catch (error) {
          console.error(`同步 ${group.classify1}/${group.classify2} 失败:`, error);
          totalFailed++;
        }
      }

      this.sync.cde.progress = 100;
      this.sync.cde.progressStatus = "success";
      this.sync.cde.progressDetail = `批量同步完成：下载 ${totalDownloaded} 个文件，${totalFailed} 失败`;
      this.$message.success(`批量同步完成：下载 ${totalDownloaded} 个文件`);
      await this.search(); // 刷新列表
    },
  },
};
</script>

<style scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.page-title { margin: 0; font-size: 20px; font-weight: 700; color: #1f2d3d; }
.page-desc { margin-top: 6px; color: #5f6f81; font-size: 13px; }
.header-actions { display: flex; align-items: center; }
.hidden-upload-input { display: none; }
.upload-picker-box { display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px dashed #c7d8ee; border-radius: 10px; padding: 12px; background: #f8fbff; }
.upload-picker-text { color: #5f6f81; line-height: 1.6; }
.selected-file-list { border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 12px; background: #fafafa; }
.selected-file-item + .selected-file-item { margin-top: 6px; }
.doc-card-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 12px; }
.compact-table ::v-deep .cell { word-break: break-word; }
.progress-cell { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; }
.progress-number { font-size: 12px; color: #5f6f81; line-height: 1; }
.doc-card { border-radius: 10px; }
.doc-card-header { display: flex; align-items: center; justify-content: space-between; }
.doc-id { font-weight: 600; color: #303133; }
.doc-summary, .doc-keywords { margin-bottom: 8px; line-height: 1.6; }
.label { color: #606266; font-weight: 600; }
.kw-tag { margin-right: 6px; margin-bottom: 6px; }
.block-title { margin-top: 8px; margin-bottom: 6px; color: #303133; font-weight: 600; }
.hit-row, .related-row { border: 1px solid #ebeef5; border-radius: 6px; padding: 8px; margin-bottom: 8px; background: #fafafa; }
.hit-meta, .related-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; color: #606266; font-size: 12px; }
.hit-summary, .related-summary, .related-text { font-size: 13px; color: #303133; line-height: 1.6; }
.related-text { color: #606266; margin-top: 4px; }
.placeholder { color: #909399; font-size: 13px; }
.related-collapse { margin-top: 10px; }
.cde-sync-filters { margin-bottom: 16px; padding: 12px; background: #f5f7fa; border-radius: 8px; }
.cde-sync-results { margin-top: 16px; }
.cde-sync-stats { margin-top: 12px; padding: 8px 12px; background: #f5f7fa; border-radius: 4px; font-size: 14px; }
.cde-sync-progress { margin-top: 16px; }
.progress-detail { margin-top: 8px; color: #909399; font-size: 13px; text-align: center; }
</style>
