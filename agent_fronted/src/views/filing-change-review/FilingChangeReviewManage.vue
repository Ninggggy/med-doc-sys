<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">药品备案变更类审评</h2>
        <div class="page-desc">首期任务：延长药品有效期（独立链路）</div>
      </div>
      <el-button type="primary" @click="dialogVisible = true">新增项目</el-button>
    </div>

    <el-card class="k-card">
      <div slot="header">项目列表</div>
      <div class="filter-row">
        <el-input v-model="filters.project_name" placeholder="项目名称" clearable style="width: 280px" />
        <el-select v-model="filters.review_status" clearable placeholder="审评状态" style="width: 180px">
          <el-option label="未审评" value="not_started" />
          <el-option label="审评中" value="running" />
          <el-option label="已审评" value="completed" />
          <el-option label="审评失败" value="failed" />
        </el-select>
        <el-button type="primary" @click="loadProjects">查询</el-button>
      </div>

      <el-table :data="projects" border class="k-table compact-table" style="margin-top: 12px">
        <el-table-column prop="project_name" label="项目名称" min-width="220" />
        <el-table-column prop="registration_category" label="注册大类" width="120" />
        <el-table-column prop="registration_classification" label="注册分类" min-width="180" />
        <el-table-column prop="task_type" label="任务类型" width="180" />
        <el-table-column prop="review_status" label="审评状态" width="120" />
        <el-table-column prop="application_form_status" label="申请表状态" width="120" />
        <el-table-column prop="submission_count" label="申报资料数" width="110" />
        <el-table-column prop="report_status" label="报告状态" width="110" />
        <el-table-column prop="updated_at" label="更新时间" width="170" />
        <el-table-column label="操作" width="260">
          <template slot-scope="scope">
            <el-button type="text" @click="openSession(scope.row)">进入审评</el-button>
            <el-button type="text" @click="openReport(scope.row)">查看报告</el-button>
            <el-button type="text" style="color: #d03050" @click="removeProject(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog title="新增备案变更审评项目" :visible.sync="dialogVisible" width="640px">
      <el-form label-width="110px" size="small">
        <el-form-item label="项目名称">
          <el-input v-model="form.project_name" />
        </el-form-item>
        <el-form-item label="注册大类">
          <el-select v-model="form.registration_category" style="width: 100%">
            <el-option label="化学药品" value="化学药品" />
            <el-option label="中药" value="中药" />
            <el-option label="生物制品" value="生物制品" />
            <el-option label="原料药" value="原料药" />
          </el-select>
        </el-form-item>
        <el-form-item label="注册分类">
          <el-input v-model="form.registration_classification" />
        </el-form-item>
        <el-form-item label="任务类型">
          <el-select v-model="form.task_type" style="width: 100%">
            <el-option label="延长药品有效期" value="extend_validity_period" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import {
  createFilingChangeProject,
  deleteFilingChangeProject,
  listFilingChangeProjects,
} from "@/api/filingChangeReview";

export default {
  name: "FilingChangeReviewManage",
  data() {
    return {
      projects: [],
      filters: { project_name: "", review_status: "" },
      dialogVisible: false,
      creating: false,
      projectListRequestToken: 0,
      form: {
        project_name: "",
        registration_category: "化学药品",
        registration_classification: "",
        task_type: "extend_validity_period",
        remark: "",
      },
    };
  },
  mounted() {
    this.loadProjects();
  },
  beforeDestroy() {
    this.projectListRequestToken += 1;
  },
  methods: {
    async loadProjects() {
      const requestToken = this.projectListRequestToken + 1;
      this.projectListRequestToken = requestToken;
      const params = {
        page: 1,
        page_size: 100,
        project_name: this.filters.project_name,
        review_status: this.filters.review_status,
      };
      try {
        const res = await listFilingChangeProjects(params, { silentError: true });
        if (this.projectListRequestToken !== requestToken) return;
        const data = (res && res.data) || {};
        this.projects = Array.isArray(data.list) ? data.list : [];
      } catch (error) {
        if (this.projectListRequestToken !== requestToken) return;
        this.$message.error((error && error.userMessage) || "项目列表加载失败");
      }
    },
    async submitCreate() {
      if (!this.form.project_name) {
        this.$message.warning("请填写项目名称");
        return;
      }
      this.creating = true;
      try {
        await createFilingChangeProject(this.form);
        this.$message.success("创建成功");
        this.dialogVisible = false;
        this.form.project_name = "";
        this.form.registration_classification = "";
        this.form.remark = "";
        await this.loadProjects();
      } finally {
        this.creating = false;
      }
    },
    openSession(row) {
      this.$router.push({ name: "filing-change-review-session", params: { projectId: row.project_id } });
    },
    openReport(row) {
      this.$router.push({ name: "filing-change-review-report", params: { projectId: row.project_id } });
    },
    async removeProject(row) {
      try {
        await this.$confirm(`确认删除项目 ${row.project_name}？`, "提示", { type: "warning" });
        await deleteFilingChangeProject(row.project_id);
        this.$message.success("删除成功");
        await this.loadProjects();
      } catch (_) {}
    },
  },
};
</script>
