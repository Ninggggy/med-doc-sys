<template>
  <KnowledgeManage :classification="'\u6307\u5bfc\u539f\u5219'" :display-name-prop="'\u6307\u5bfc\u539f\u5219'" />
</template>

<script>
import KnowledgeManage from "./KnowledgeManage.vue";

export default {
  name: "GuidelineManage",
  components: { KnowledgeManage },
};
</script>
