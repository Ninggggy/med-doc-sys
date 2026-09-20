<template>
  <KnowledgeManage :classification="'ICH'" :display-name-prop="'ICH'" />
</template>

<script>
import KnowledgeManage from "./KnowledgeManage.vue";

export default {
  name: "ICHManage",
  components: { KnowledgeManage },
};
</script>
