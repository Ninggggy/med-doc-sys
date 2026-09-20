<template>
  <aside class="side" :class="{ 'side--collapsed': collapsed }">
    <div class="logo-wrap">
      <div class="logo">药</div>
      <div v-if="!collapsed" class="brand-copy">
        <div class="name">药品智能辅助审评系统</div>
        <div class="sub">智能审评工作台</div>
      </div>
      <el-button
        class="collapse-btn"
        type="text"
        :icon="collapsed ? 'el-icon-s-unfold' : 'el-icon-s-fold'"
        @click="$emit('toggle')"
      />
    </div>

    <el-menu
      :default-active="$route.path"
      :default-openeds="defaultOpeneds"
      :unique-opened="false"
      router
      class="menu"
    >
      <el-menu-item index="/dashboard">
        <i class="el-icon-data-analysis"></i>
        <span>概览</span>
      </el-menu-item>

      <el-submenu index="knowledge-group">
        <template slot="title">
          <i class="el-icon-collection"></i>
          <span>知识库</span>
        </template>
        <el-menu-item v-for="item in knowledgeMenus" :key="item.path" :index="item.path">
          {{ item.label }}
        </el-menu-item>
      </el-submenu>

      <el-submenu index="review-group">
        <template slot="title">
          <i class="el-icon-document-checked"></i>
          <span>辅助审评任务</span>
        </template>
        <el-menu-item index="/pre-review/registration">药品上市注册类审评</el-menu-item>
        <el-menu-item index="/filing-change-review">药品备案变更类审评</el-menu-item>
      </el-submenu>
    </el-menu>
  </aside>
</template>

<script>
export default {
  name: "AppSidebar",
  props: {
    collapsed: {
      type: Boolean,
      default: false,
    },
  },
  data() {
    return {
      knowledgeMenus: [
        { path: "/knowledge/guideline", label: "指导原则" },
        { path: "/knowledge/policy", label: "制度规范" },
        { path: "/knowledge/law", label: "法律法规" },
        { path: "/knowledge/ich", label: "ICH" },
        { path: "/knowledge/pharmacopeia", label: "药典数据" },
        { path: "/knowledge/common-issue", label: "共性问题" },
        { path: "/knowledge/review-rule", label: "审评规则" },
        { path: "/knowledge/experience", label: "历史经验" },
      ],
    };
  },
  computed: {
    defaultOpeneds() {
      const path = String((this.$route && this.$route.path) || "").trim();
      if (path.startsWith("/knowledge/")) {
        return ["knowledge-group"];
      }
      if (path.startsWith("/pre-review") || path.startsWith("/filing-change-review")) {
        return ["review-group"];
      }
      return [];
    },
  },
};
</script>

<style scoped>
.side {
  width: 208px;
  height: 100%;
  background: linear-gradient(180deg, #f8fbff 0%, #eef3fa 100%);
  color: #2f3b4a;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #dbe5f0;
  transition: width 0.2s ease;
}

.side--collapsed {
  width: 68px;
}

.logo-wrap {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 14px;
  border-bottom: 1px solid #e3ebf5;
}

.side--collapsed .logo-wrap {
  padding: 0 10px;
  justify-content: center;
}

.logo {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: linear-gradient(135deg, #4fc3f7, #1565c0);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
}

.brand-copy {
  min-width: 0;
}

.collapse-btn {
  margin-left: auto;
  color: #5f6f81;
}

.side--collapsed .collapse-btn {
  margin-left: 0;
}

.name {
  font-size: 13px;
  color: #1f2d3d;
  font-weight: 700;
  line-height: 1.2;
}

.sub {
  font-size: 11px;
  color: #5f6f81;
  line-height: 1.2;
  margin-top: 2px;
}

.menu {
  border-right: 0;
  background: transparent;
  padding: 10px 8px;
}

.side--collapsed .menu {
  padding: 10px 6px;
}

.side--collapsed ::v-deep .el-menu-item span,
.side--collapsed ::v-deep .el-submenu__title span,
.side--collapsed ::v-deep .el-submenu__icon-arrow {
  display: none;
}

.side--collapsed ::v-deep .el-menu-item,
.side--collapsed ::v-deep .el-submenu__title {
  padding: 0 !important;
  text-align: center;
}

.menu .el-menu-item,
.menu ::v-deep .el-submenu__title {
  color: #2f3b4a;
  border-radius: 8px;
  margin-bottom: 4px;
  height: 42px;
  line-height: 42px;
}

.menu .el-menu-item.is-active {
  background: #dfefff;
  color: #1f5fae;
  font-weight: 600;
}

.menu .el-menu-item:hover,
.menu ::v-deep .el-submenu__title:hover {
  background: #eaf3ff;
}

.menu ::v-deep .el-submenu .el-menu-item {
  min-width: 0;
}
</style>
