<template>
  <KnowledgeManage :classification="'\u6cd5\u5f8b\u6cd5\u89c4'" :display-name-prop="'\u6cd5\u5f8b\u6cd5\u89c4'" />
</template>

<script>
import KnowledgeManage from "./KnowledgeManage.vue";

export default {
  name: "LawManage",
  components: { KnowledgeManage },
};
</script>
