<template>
  <el-dialog :title="dialogTitle" :visible.sync="localVisible" width="760px" @close="emitClose">
    <el-form label-width="110px" size="small">
      <el-form-item v-if="showBranchSelector" :label="branchSelectorLabel">
        <el-radio-group :value="branch" @input="$emit('update:branch', $event)">
          <el-radio-button v-for="item in branchOptions" :key="item.value" :label="item.value">
            {{ item.label }}
          </el-radio-button>
        </el-radio-group>
        <div class="muted" style="margin-top: 8px">
          选择上传模块后，ZIP、单文件或按章节上传都会先在对应目录范围内定位与挂载。模块 3 的完整 Word 会自动按各级章节树拆分到目标章节。
        </div>
      </el-form-item>

      <el-form-item label="上传方式">
        <el-radio-group :value="mode" @input="$emit('update:mode', $event)">
          <el-radio-button v-for="item in uploadModeOptions" :key="item.value" :label="item.value" :disabled="item.disabled">
            {{ item.label }}
          </el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item v-if="showParseModeSelector" label="解析模式">
        <el-radio-group :value="parseMode" @input="$emit('update:parse-mode', $event)">
          <el-radio-button v-for="item in parseModeOptions" :key="item.value" :label="item.value">
            {{ item.label }}
          </el-radio-button>
        </el-radio-group>
        <div class="muted" style="margin-top: 8px">
          快速解析沿用原链路，详细解析走 DeepSeek OCR。按章节上传时，解析结果会直接挂到当前选择的章节。
        </div>
      </el-form-item>

      <el-form-item v-if="mode === 'section'" label="目标章节">
        <el-tree
          :data="uploadTreeData"
          node-key="section_id"
          :props="treeProps"
          highlight-current
          :default-expand-all="false"
          @node-click="$emit('select-section', $event)"
        />
        <div class="muted" style="margin-top: 8px">
          当前目标章节：{{ sectionName || "未选择" }}
        </div>
      </el-form-item>

      <el-form-item label="上传文件">
        <el-upload
          action="#"
          :auto-upload="false"
          :file-list="fileList"
          :on-change="onFileChange"
          :on-remove="onFileRemove"
          :multiple="mode !== 'zip'"
          :limit="mode === 'zip' ? 1 : 20"
        >
          <el-button size="small">选择文件</el-button>
          <div slot="tip" class="el-upload__tip">
            ZIP 模式只接受 `.zip`；单文件解析优先支持 PDF、Word。模块 3 的完整 Word 会按内部章节树自动拆分挂载。
          </div>
        </el-upload>
      </el-form-item>
    </el-form>
    <span slot="footer">
      <el-button @click="emitClose">取消</el-button>
      <el-button type="primary" :loading="uploading" @click="$emit('submit')">上传</el-button>
    </span>
  </el-dialog>
</template>

<script>
import { UPLOAD_MODE_OPTIONS } from "@/constants/preReviewEnums";

const PARSE_MODE_OPTIONS = [
  { value: "quick", label: "快速解析" },
  { value: "detailed", label: "详细解析" },
];

export default {
  name: "PreReviewUploadDialog",
  props: {
    visible: { type: Boolean, default: false },
    dialogTitle: { type: String, default: "上传申报资料" },
    branch: { type: String, default: "" },
    branchOptions: { type: Array, default: () => [] },
    mode: { type: String, default: "single" },
    parseMode: { type: String, default: "quick" },
    sectionName: { type: String, default: "" },
    fileList: { type: Array, default: () => [] },
    treeProps: {
      type: Object,
      default: () => ({ children: "children_sections", label: "label" }),
    },
    uploadTreeData: { type: Array, default: () => [] },
    uploading: { type: Boolean, default: false },
    branchSelectorLabel: { type: String, default: "上传模块" },
  },
  computed: {
    localVisible: {
      get() {
        return this.visible;
      },
      set(value) {
        this.$emit("update:visible", value);
      },
    },
    showBranchSelector() {
      return this.branchOptions.length > 0;
    },
    showParseModeSelector() {
      return this.mode === "single" || this.mode === "section";
    },
    uploadModeOptions() {
      return UPLOAD_MODE_OPTIONS.map((item) => ({
        ...item,
        disabled: item.value === "section" && !this.showBranchSelector,
      }));
    },
    parseModeOptions() {
      return PARSE_MODE_OPTIONS;
    },
  },
  methods: {
    emitClose() {
      this.$emit("close");
      this.$emit("update:visible", false);
    },
    onFileChange(file, fileList) {
      this.$emit("file-change", file, fileList);
    },
    onFileRemove(file, fileList) {
      this.$emit("file-remove", file, fileList);
    },
  },
};
</script>

<style scoped>
.muted {
  color: #5a667a;
  font-size: 12px;
  line-height: 1.6;
}
</style>
