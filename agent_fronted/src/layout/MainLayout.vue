<template>
  <div class="frame">
    <AppSidebar :collapsed="sidebarCollapsed" @toggle="toggleSidebar" />
    <div class="main" :class="{ 'main--sidebar-collapsed': sidebarCollapsed }">
      <AppHeader />
      <section class="content">
        <router-view :key="$route.path" />
      </section>
    </div>
  </div>
</template>

<script>
import AppHeader from "@/components/layout/AppHeader.vue";
import AppSidebar from "@/components/layout/AppSidebar.vue";

export default {
  name: "MainLayout",
  components: { AppHeader, AppSidebar },
  data() {
    return {
      sidebarCollapsed: false,
    };
  },
  mounted() {
    this.sidebarCollapsed = window.localStorage.getItem("agent-sidebar-collapsed") === "1";
  },
  methods: {
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed;
      window.localStorage.setItem("agent-sidebar-collapsed", this.sidebarCollapsed ? "1" : "0");
    },
  },
};
</script>

<style scoped>
.frame {
  height: 100%;
  display: flex;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  transition: margin-left 0.2s ease;
}

.content {
  flex: 1;
  padding: 18px;
  overflow: auto;
}
</style>
