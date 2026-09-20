<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">知识库管理 - 药典数据</h2>
        <div class="page-desc">药典数据按结构化字段入库，支持录入、修改、删除和 JSON 批量导入。</div>
      </div>
      <div class="header-actions">
        <el-button @click="openImportDialog">导入 JSON</el-button>
        <el-button type="primary" @click="openEditDialog()">新增药典条目</el-button>
      </div>
    </div>

    <el-card>
      <div slot="header">查询</div>
      <el-form :inline="true" size="small">
        <el-form-item label="药品名称">
          <el-input v-model="filters.keyword" clearable />
        </el-form-item>
        <el-form-item label="作用范围">
          <el-select v-model="filters.affect_range" clearable style="width: 140px">
            <el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">查询</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="table.list" border style="margin-top: 12px">
        <el-table-column type="expand">
          <template slot-scope="scope">
            <div class="detail-grid">
              <div v-for="field in detailFields" :key="field.key" class="detail-item">
                <div class="detail-label">{{ field.label }}</div>
                <div class="detail-value">{{ scope.row[field.key] || '-' }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="drug_name" label="药品名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="affect_range" label="作用范围" width="100" />
        <el-table-column prop="category" label="类别" min-width="140" show-overflow-tooltip />
        <el-table-column prop="specification" label="规格" min-width="140" show-overflow-tooltip />
        <el-table-column prop="index_status" label="索引状态" width="110" />
        <el-table-column prop="indexed_count" label="Indexed" width="100" />
        <el-table-column prop="storage" label="贮藏" min-width="160" show-overflow-tooltip />
        <el-table-column prop="update_time" label="更新时间" width="165" />
        <el-table-column label="操作" width="180">
          <template slot-scope="scope">
            <el-button type="text" @click="openEditDialog(scope.row)">编辑</el-button>
            <el-button type="text" style="color: #d03050" @click="removeRow(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        style="margin-top: 12px; text-align: right"
        layout="total, prev, pager, next"
        :total="table.total"
        :page-size="table.page_size"
        :current-page.sync="table.page"
        @current-change="search"
      />
    </el-card>

    <el-dialog :title="editForm.entry_id ? '编辑药典条目' : '新增药典条目'" :visible.sync="editDialogVisible" width="860px">
      <el-form label-width="110px" size="small">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="药品名称"><el-input v-model="editForm.drug_name" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="作用范围">
              <el-select v-model="editForm.affect_range" style="width: 100%">
                <el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col v-for="field in detailFields" :key="field.key" :span="12">
            <el-form-item :label="field.label">
              <el-input v-model="editForm[field.key]" type="textarea" :rows="3" resize="vertical" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <span slot="footer">
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEntry">保存</el-button>
      </span>
    </el-dialog>

    <el-dialog title="导入药典 JSON" :visible.sync="importDialogVisible" width="560px">
      <el-form label-width="110px" size="small">
        <el-form-item label="作用范围">
          <el-select v-model="importForm.affect_range" style="width: 100%">
            <el-option v-for="item in affectRangeOptions" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="JSON 文件">
          <input ref="jsonInput" class="hidden-input" type="file" accept=".json,application/json" @change="onJsonFileChange" />
          <div class="upload-box">
            <div>{{ importForm.file ? importForm.file.name : '请选择 JSON 文件' }}</div>
            <el-button size="small" @click="pickJsonFile">选择文件</el-button>
          </div>
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button @click="importDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="submitImport">开始导入</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import {
  createPharmacopeiaEntry,
  deletePharmacopeiaEntry,
  importPharmacopeiaJson,
  listPharmacopeiaEntries,
  updatePharmacopeiaEntry,
} from "@/api/pharmacopeia";

const affectRangeOptions = ["中药", "化学药"];
const detailFields = [
  { key: "prescription", label: "处方" },
  { key: "character", label: "性状" },
  { key: "identification", label: "鉴别" },
  { key: "inspection", label: "检查" },
  { key: "inspection_variant", label: "检査" },
  { key: "assay", label: "含量测定" },
  { key: "category", label: "类别" },
  { key: "storage", label: "贮藏" },
  { key: "specification", label: "规格" },
  { key: "related_substances", label: "有关物质" },
  { key: "potency_assay", label: "效价测定" },
  { key: "active_ingredient_content", label: "活性成分含量" },
  { key: "preparation_method", label: "制法" },
  { key: "preparation_requirement", label: "制法要求" },
  { key: "dosage_form", label: "制剂" },
  { key: "prepared_into", label: "制成" },
  { key: "production_requirement", label: "生产要求" },
  { key: "labeling", label: "标注" },
  { key: "other", label: "其他" },
];

function emptyForm() {
  const base = { entry_id: "", drug_name: "", affect_range: affectRangeOptions[1] };
  detailFields.forEach((field) => {
    base[field.key] = "";
  });
  return base;
}

export default {
  name: "PharmacopeiaManage",
  data() {
    return {
      filters: { keyword: "", affect_range: "" },
      table: { list: [], total: 0, page: 1, page_size: 20 },
      affectRangeOptions,
      detailFields,
      editDialogVisible: false,
      importDialogVisible: false,
      editForm: emptyForm(),
      importForm: { affect_range: affectRangeOptions[1], file: null },
      saving: false,
      importing: false,
      listRequestToken: 0,
    };
  },
  mounted() {
    this.search();
  },
  beforeDestroy() {
    this.listRequestToken += 1;
  },
  methods: {
    onSearch() {
      this.table.page = 1;
      this.search();
    },
    async search() {
      const requestToken = this.listRequestToken + 1;
      this.listRequestToken = requestToken;
      const params = {
        keyword: this.filters.keyword,
        affect_range: this.filters.affect_range,
        page: this.table.page,
        page_size: this.table.page_size,
      };
      try {
        const res = await listPharmacopeiaEntries(params, { silentError: true });
        if (this.listRequestToken !== requestToken) return;
        const data = (res && res.data) || {};
        this.table.list = Array.isArray(data.list) ? data.list : [];
        this.table.total = Number(data.total || 0);
      } catch (error) {
        if (this.listRequestToken !== requestToken) return;
        this.$message.error((error && error.userMessage) || "药典数据加载失败");
      }
    },
    openEditDialog(row) {
      this.editForm = row ? { ...emptyForm(), ...row } : emptyForm();
      this.editDialogVisible = true;
    },
    async saveEntry() {
      if (!this.editForm.drug_name) {
        this.$message.warning("请填写药品名称");
        return;
      }
      this.saving = true;
      try {
        if (this.editForm.entry_id) {
          await updatePharmacopeiaEntry(this.editForm.entry_id, this.editForm);
        } else {
          await createPharmacopeiaEntry(this.editForm);
        }
        this.$message.success("保存成功");
        this.editDialogVisible = false;
        await this.search();
      } finally {
        this.saving = false;
      }
    },
    async removeRow(row) {
      try {
        await this.$confirm(`确认删除 ${row.drug_name} ?`, "提示", { type: "warning" });
        await deletePharmacopeiaEntry(row.entry_id);
        this.$message.success("删除成功");
        await this.search();
      } catch (_) {}
    },
    openImportDialog() {
      this.importDialogVisible = true;
      this.importForm = { affect_range: affectRangeOptions[1], file: null };
    },
    pickJsonFile() {
      if (this.$refs.jsonInput) {
        this.$refs.jsonInput.value = "";
        this.$refs.jsonInput.click();
      }
    },
    onJsonFileChange(event) {
      const files = (event && event.target && event.target.files) || [];
      this.importForm.file = files[0] || null;
    },
    async submitImport() {
      if (!this.importForm.file) {
        this.$message.warning("请先选择 JSON 文件");
        return;
      }
      this.importing = true;
      try {
        const fd = new FormData();
        fd.append("file", this.importForm.file);
        fd.append("affect_range", this.importForm.affect_range);
        const res = await importPharmacopeiaJson(fd);
        const data = (res && res.data) || {};
        this.$message.success(`导入完成，新增 ${data.created_count || 0} 条，更新 ${data.updated_count || 0} 条`);
        this.importDialogVisible = false;
        await this.search();
      } finally {
        this.importing = false;
      }
    },
  },
};
</script>

<style scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.page-title { margin: 0; font-size: 20px; font-weight: 700; color: #1f2d3d; }
.page-desc { margin-top: 6px; color: #5f6f81; font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.detail-item { border: 1px solid #ebeef5; border-radius: 6px; padding: 10px; background: #fafafa; }
.detail-label { font-weight: 600; color: #303133; margin-bottom: 6px; }
.detail-value { color: #606266; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.hidden-input { display: none; }
.upload-box { display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px dashed #c7d8ee; border-radius: 8px; padding: 12px; background: #f8fbff; }
</style>

