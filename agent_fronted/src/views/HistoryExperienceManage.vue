<template>
  <KnowledgeManage :classification="'\u5386\u53f2\u7ecf\u9a8c'" :display-name-prop="'\u5386\u53f2\u7ecf\u9a8c'" />
</template>

<script>
import KnowledgeManage from "./KnowledgeManage.vue";

export default {
  name: "HistoryExperienceManage",
  components: { KnowledgeManage },
};
</script>
