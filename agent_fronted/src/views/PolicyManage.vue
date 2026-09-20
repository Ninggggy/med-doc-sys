<template>
  <KnowledgeManage :classification="'\u5236\u5ea6\u89c4\u8303'" :display-name-prop="'\u5236\u5ea6\u89c4\u8303'" />
</template>

<script>
import KnowledgeManage from "./KnowledgeManage.vue";

export default {
  name: "PolicyManage",
  components: { KnowledgeManage },
};
</script>
