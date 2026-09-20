import Vue from "vue";
import Router from "vue-router";

import MainLayout from "@/layout/MainLayout.vue";
import Dashboard from "@/views/Dashboard.vue";
import GuidelineManage from "@/views/GuidelineManage.vue";
import PolicyManage from "@/views/PolicyManage.vue";
import LawManage from "@/views/LawManage.vue";
import ICHManage from "@/views/ICHManage.vue";
import PharmacopeiaManage from "@/views/PharmacopeiaManage.vue";
import HistoryExperienceManage from "@/views/HistoryExperienceManage.vue";
import CommonIssueManage from "@/views/CommonIssueManage.vue";
import ReviewCriteriaManage from "@/views/ReviewCriteriaManage.vue";
import PreReviewManage from "@/views/PreReviewManage.vue";
import PreReviewSession from "@/views/PreReviewSession.vue";
import FilingChangeReviewManage from "@/views/filing-change-review/FilingChangeReviewManage.vue";
import FilingChangeReviewSession from "@/views/filing-change-review/FilingChangeReviewSession.vue";
import FilingChangeReviewReport from "@/views/filing-change-review/FilingChangeReviewReport.vue";

Vue.use(Router);

export default new Router({
  mode: "hash",
  routes: [
    {
      path: "/",
      component: MainLayout,
      redirect: "/dashboard",
      children: [
        {
          path: "dashboard",
          name: "dashboard",
          component: Dashboard,
          meta: { title: "仪表盘" },
        },
        {
          path: "knowledge",
          redirect: "/knowledge/guideline",
        },
        {
          path: "knowledge/guideline",
          name: "knowledge-guideline",
          component: GuidelineManage,
          meta: { title: "知识库管理 - 指导原则" },
        },
        {
          path: "knowledge/policy",
          name: "knowledge-policy",
          component: PolicyManage,
          meta: { title: "知识库管理 - 制度规范" },
        },
        {
          path: "knowledge/law",
          name: "knowledge-law",
          component: LawManage,
          meta: { title: "知识库管理 - 法律法规" },
        },
        {
          path: "knowledge/ich",
          name: "knowledge-ich",
          component: ICHManage,
          meta: { title: "知识库管理 - ICH" },
        },
        {
          path: "knowledge/pharmacopeia",
          name: "knowledge-pharmacopeia",
          component: PharmacopeiaManage,
          meta: { title: "知识库管理 - 药典数据" },
        },
        {
          path: "knowledge/experience",
          name: "knowledge-experience",
          component: HistoryExperienceManage,
          meta: { title: "知识库管理 - 历史经验" },
        },
        {
          path: "knowledge/common-issue",
          name: "knowledge-common-issue",
          component: CommonIssueManage,
          meta: { title: "知识库管理 - 共性问题" },
        },
        {
          path: "knowledge/review-rule",
          name: "knowledge-review-rule",
          component: ReviewCriteriaManage,
          meta: { title: "知识库管理 - 审评规则" },
        },
        {
          path: "pre-review",
          redirect: "/pre-review/registration",
        },
        {
          path: "pre-review/registration",
          name: "pre-review-registration",
          component: PreReviewManage,
          meta: { title: "药品上市注册类审评" },
        },
        {
          path: "filing-change-review",
          name: "filing-change-review",
          component: FilingChangeReviewManage,
          meta: { title: "药品备案变更类审评" },
        },
        {
          path: "filing-change-review/session/:projectId",
          name: "filing-change-review-session",
          component: FilingChangeReviewSession,
          meta: { title: "备案变更审评会话" },
        },
        {
          path: "filing-change-review/report/:projectId",
          name: "filing-change-review-report",
          component: FilingChangeReviewReport,
          meta: { title: "备案变更审评报告" },
        },
        {
          path: "pre-review/manage-legacy",
          name: "pre-review",
          component: PreReviewManage,
          meta: { title: "辅助审评项目管理" },
        },
        {
          path: "pre-review/session/:projectId",
          name: "pre-review-session",
          component: PreReviewSession,
          meta: { title: "辅助审评会话" },
        },
      ],
    },
  ],
});
