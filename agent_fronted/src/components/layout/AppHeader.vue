<template>
  <header class="app-header">
    <div class="left">
      <h1 class="title">药品智能辅助审评系统</h1>
      <p class="desc">知识库管理 · 申报资料解析 · 按章节辅助审评</p>
    </div>
    <div class="right">
      <!-- <el-tag size="small" effect="dark" type="success">Agent Backend</el-tag> -->
      <span class="time">{{ now }}</span>
    </div>
  </header>
</template>

<script>
export default {
  name: "AppHeader",
  data() {
    return { now: "" };
  },
  mounted() {
    this.tick();
    this.timer = setInterval(this.tick, 1000);
  },
  beforeDestroy() {
    clearInterval(this.timer);
  },
  methods: {
    tick() {
      const d = new Date();
      const p = (v) => String(v).padStart(2, "0");
      this.now = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    },
  },
};
</script>

<style scoped>
.app-header {
  height: 68px;
  background: linear-gradient(120deg, #0d47a1, #1565c0 60%, #1e88e5);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  box-shadow: 0 8px 18px rgba(13, 71, 161, 0.28);
}

.left {
  min-width: 0;
}

.title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.4px;
}

.desc {
  margin: 4px 0 0;
  font-size: 12px;
  opacity: 0.88;
}

.right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.time {
  font-size: 12px;
  opacity: 0.9;
}
</style>
