<template>
  <div class="block">
    <div class="pane-head">
      <div>
        <div class="title">{{ title }}</div>
        <div class="muted">当前状态：{{ statusLabel || "-" }}</div>
      </div>
      <el-button
        size="mini"
        :disabled="running || runningSection || !runStreamLogs.length"
        @click="$emit('clear-logs')"
      >
        清空日志
      </el-button>
    </div>

    <div v-if="runStreamLogGroups.length" class="stream-log-list">
      <el-collapse :value="expandedSections" @input="$emit('update:expanded-sections', $event)">
        <el-collapse-item
          v-for="group in runStreamLogGroups"
          :key="group.key"
          :name="group.key"
        >
          <template slot="title">
            <span class="stream-group-title">{{ group.label }}</span>
            <span class="stream-group-count">({{ group.items.length }})</span>
          </template>
          <div v-for="item in group.items" :key="item.id" class="stream-log-item">
            <span class="stream-log-time">{{ item.time }}</span>
            <span class="stream-log-text">{{ item.text }}</span>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
    <div v-else class="empty">当前还没有执行日志。</div>
  </div>
</template>

<script>
export default {
  name: "PreReviewExecutionPanel",
  props: {
    title: { type: String, default: "审评进度" },
    statusLabel: { type: String, default: "-" },
    running: { type: Boolean, default: false },
    runningSection: { type: Boolean, default: false },
    runStreamLogs: { type: Array, default: () => [] },
    runStreamLogGroups: { type: Array, default: () => [] },
    expandedSections: { type: Array, default: () => [] },
  },
};
</script>

<style scoped>
.block {
  border: 1px solid #eef1f6;
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}

.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.muted,
.empty {
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}

.stream-log-list {
  max-height: 280px;
  overflow: auto;
  border: 1px solid #eef1f6;
  border-radius: 8px;
  padding: 8px;
  background: #fafcff;
  margin-top: 12px;
}

.stream-log-item {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
  line-height: 1.6;
  color: #475569;
}

.stream-log-time {
  color: #94a3b8;
  flex: 0 0 64px;
}

.stream-log-text {
  flex: 1;
  white-space: pre-wrap;
  word-break: break-word;
}

.stream-group-title {
  font-size: 12px;
  color: #1e293b;
  font-weight: 600;
}

.stream-group-count {
  margin-left: 6px;
  font-size: 12px;
  color: #94a3b8;
}
</style>
