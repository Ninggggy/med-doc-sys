<template>
  <div class="pane-content">
    <div class="pane-head">
      <div>
        <div class="title">章节原文</div>
        <div class="muted">
          <span>{{ currentContentSectionLabel }}</span>
          <span v-if="contentCharCount" class="content-stat">Chars: {{ contentCharCount }}</span>
        </div>
      </div>
      <div class="pane-actions">
        <el-radio-group :value="contentViewMode" size="mini" :disabled="loading" @input="$emit('change-view-mode', $event)">
          <el-radio-button label="cleaned">Markdown</el-radio-button>
          <el-radio-button label="raw">原始</el-radio-button>
        </el-radio-group>
        <template v-if="editing">
          <el-button size="mini" @click="$emit('cancel-edit')">取消</el-button>
          <el-button type="primary" size="mini" :loading="saving" :disabled="!dirty" @click="$emit('save')">保存</el-button>
        </template>
        <el-button v-else size="mini" type="primary" :disabled="loading" @click="$emit('start-edit')">编辑</el-button>
      </div>
    </div>

    <div v-if="errorMessage" class="content-load-error" role="alert">
      <span>{{ errorMessage }}</span>
      <el-button type="text" size="mini" :loading="loading" @click="$emit('retry')">重新加载</el-button>
    </div>

    <div v-if="editing" v-loading="loading" class="content-scroll-shell">
      <div class="editor-wrap">
        <el-input
          :value="editorText"
          type="textarea"
          :rows="28"
          resize="none"
          @input="$emit('editor-input', $event)"
        />
      </div>
    </div>
    <div v-else-if="contentViewMode === 'raw'" v-loading="loading" class="content-scroll-shell">
      <pre class="raw-preview">{{ editorText }}</pre>
    </div>
    <div v-else v-loading="loading" class="content-scroll-shell">
      <div class="markdown-preview" v-html="renderedMarkdownContent" />
    </div>
  </div>
</template>

<script>
export default {
  name: "PreReviewContentPane",
  props: {
    currentContentSectionLabel: { type: String, default: "" },
    renderedMarkdownContent: { type: String, default: "" },
    editorText: { type: String, default: "" },
    contentViewMode: { type: String, default: "cleaned" },
    editing: { type: Boolean, default: false },
    saving: { type: Boolean, default: false },
    dirty: { type: Boolean, default: false },
    contentCharCount: { type: Number, default: 0 },
    loading: { type: Boolean, default: false },
    errorMessage: { type: String, default: "" },
  },
};
</script>

<style scoped>
.pane-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}

.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pane-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.muted {
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}

.content-stat {
  margin-left: 10px;
  color: #94a3b8;
}

.content-load-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  border: 1px solid #fbc4c4;
  border-radius: 6px;
  background: #fef0f0;
  color: #c45656;
  font-size: 13px;
  line-height: 1.5;
}

.content-scroll-shell {
  flex: 1;
  min-height: 0;
  max-height: calc(100vh - 280px);
  border: 1px solid #eef1f6;
  border-radius: 8px;
  overflow: auto;
  background: #fafcff;
  scrollbar-gutter: stable;
}

.editor-wrap,
.markdown-preview,
.raw-preview {
  min-height: 100%;
  padding: 12px;
  box-sizing: border-box;
}

.raw-preview {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.75;
  color: #334155;
  font-size: 13px;
  font-family: "Consolas", "SFMono-Regular", "Liberation Mono", monospace;
}

.editor-wrap :deep(.el-textarea),
.editor-wrap :deep(.el-textarea__inner) {
  min-height: 100%;
}

.markdown-preview :deep(.empty-markdown) {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  color: #94a3b8;
  font-size: 13px;
}

.markdown-preview :deep(h1),
.markdown-preview :deep(h2),
.markdown-preview :deep(h3),
.markdown-preview :deep(h4) {
  margin: 8px 0;
}

.markdown-preview :deep(p) {
  margin: 0 0 8px;
}

.markdown-preview :deep(ul),
.markdown-preview :deep(ol) {
  margin: 0 0 8px 20px;
}

.markdown-preview :deep(.md-table) {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 12px;
}

.markdown-preview :deep(.md-table th),
.markdown-preview :deep(.md-table td) {
  border: 1px solid #dfe6f0;
  padding: 6px 8px;
  vertical-align: top;
}

.markdown-preview :deep(.md-label) {
  font-weight: 600;
  color: #334155;
  margin: 10px 0 6px;
}

.markdown-preview :deep(pre) {
  margin: 0 0 12px;
  padding: 12px;
  overflow: auto;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
}

.markdown-preview :deep(pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
  font-size: 12px;
  line-height: 1.7;
}

.markdown-preview :deep(code) {
  padding: 0 4px;
  border-radius: 4px;
  background: #eef2ff;
  color: #1e293b;
  font-size: 12px;
}

.markdown-preview :deep(blockquote) {
  margin: 0 0 12px;
  padding: 8px 12px;
  border-left: 4px solid #cbd5e1;
  background: #f8fafc;
  color: #475569;
}

.markdown-preview :deep(hr) {
  margin: 16px 0;
  border: 0;
  border-top: 1px solid #e2e8f0;
}

</style>

