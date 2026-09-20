/* eslint-disable no-console */
"use strict";

/**
 * 前端异步状态回归：直接加载真实 Vue SFC methods，不复制业务实现。
 * 运行：node test/test_frontend_async_state.js
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

const ROOT = path.resolve(__dirname, "..");
const FRONTEND = path.join(ROOT, "agent_fronted");
const frontendRequire = createRequire(path.join(FRONTEND, "package.json"));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function compileScript(source, fakeRequire) {
  const babel = frontendRequire("@babel/core");
  const compiled = babel.transformSync(source, {
    presets: [frontendRequire.resolve("@babel/preset-env")],
    babelrc: false,
    configFile: false,
  }).code;
  const loaded = { exports: {} };
  // eslint-disable-next-line no-new-func
  new Function("require", "module", "exports", compiled)(fakeRequire, loaded, loaded.exports);
  return loaded.exports.default || loaded.exports;
}

function loadSfc(relativePath, api) {
  const compiler = frontendRequire("vue-template-compiler");
  const descriptor = compiler.parseComponent(fs.readFileSync(path.join(FRONTEND, relativePath), "utf8"));
  assert(descriptor.script && descriptor.script.content, `${relativePath} 缺少 script`);
  class MarkdownItStub {
    constructor() { this.renderer = { rules: {} }; }
    render(value) { return String(value || ""); }
  }
  const apiProxy = new Proxy(api, {
    get(target, key) {
      return target[key] || (() => Promise.resolve({ data: {} }));
    },
  });
  return compileScript(descriptor.script.content, (moduleName) => {
    if (moduleName.includes("/api/")) return apiProxy;
    if (moduleName === "markdown-it") return MarkdownItStub;
    if (moduleName.includes("/constants/")) {
      return {
        PRE_REVIEW_REGISTRATION_OPTIONS: [],
        normalizeReviewDomain: (value) => value,
      };
    }
    if (moduleName.includes("/utils/ctdDisplay")) {
      return {
        buildSectionDisplayLabel: (id, name) => `${id || ""} ${name || ""}`.trim(),
        stripSectionDisplayName: (value) => value,
      };
    }
    if (moduleName.includes("/utils/preReviewDisplay")) {
      return { formatReviewConfidence: (value) => String(value || "") };
    }
    if (moduleName.endsWith(".vue")) return {};
    throw new Error(`未知测试模块: ${moduleName}`);
  });
}

function reactiveVm(seed = {}) {
  return {
    $set(object, key, value) { object[key] = value; },
    $delete(object, key) { delete object[key]; },
    ...seed,
  };
}

async function testRunRace(component, api) {
  const methods = component.methods;
  const aOverview = deferred();
  const aTraces = deferred();
  const aPatches = deferred();
  const bOverview = deferred();
  const bTraces = deferred();
  const bPatches = deferred();
  const pick = (runId, a, b) => (runId === "run-a" ? a.promise : b.promise);
  api.sectionOverview = (runId) => pick(runId, aOverview, bOverview);
  api.sectionTraces = (runId) => pick(runId, aTraces, bTraces);
  api.sectionPatchCandidates = (runId) => pick(runId, aPatches, bPatches);
  const vm = reactiveVm({
    sessionProjectToken: 1,
    selectedRun: null,
    feedbackForm: {},
    resultOverview: null,
    displayedRunId: "",
    runResultLoadError: "",
    runResultRequestToken: 0,
    sectionTraceMap: {},
    sectionPatchMap: {},
    sectionFeedbackHistory: null,
    sectionExecutionAuditDetail: null,
    sectionExampleDetail: null,
    sectionRunDetailLoadState: { key: "", promise: null },
    traceArtifactRequestToken: 0,
    loadingTraceArtifact: false,
    p52FeedbackPatchesRequestToken: 0,
    loadingP52FeedbackPatches: false,
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    cancelAllLongWorkflowTaskPolling() {},
    resetSectionFeedbackDraft() {},
    syncSectionAblationFromCache() {},
    ensureSectionSelectionForRun() {},
    loadSelectedSectionDetailBundle: async () => {},
    viewRun: methods.viewRun,
  });
  const oldLoad = vm.viewRun({ run_id: "run-a" }, 1);
  const newLoad = vm.viewRun({ run_id: "run-b" }, 1);
  bOverview.resolve({ data: { marker: "B" } });
  bTraces.resolve({ data: [{ section_id: "b", marker: "B" }] });
  bPatches.resolve({ data: [] });
  await newLoad;
  aOverview.resolve({ data: { marker: "A" } });
  aTraces.resolve({ data: [{ section_id: "a", marker: "A" }] });
  aPatches.resolve({ data: [] });
  await oldLoad;
  assert.strictEqual(vm.selectedRun.run_id, "run-b");
  assert.strictEqual(vm.resultOverview.marker, "B");
  assert.deepStrictEqual(Object.keys(vm.sectionTraceMap), ["b"]);
}

async function testSameRunRefreshFailurePreservesData(component, api) {
  const methods = component.methods;
  api.sectionOverview = () => Promise.reject(new Error("timeout"));
  api.sectionTraces = () => Promise.resolve({ data: [{ section_id: "s", marker: "new-trace" }] });
  api.sectionPatchCandidates = () => Promise.reject(new Error("timeout"));
  const previousOverview = { marker: "old-overview" };
  const previousPatches = { s: [{ marker: "old-patch" }] };
  const vm = reactiveVm({
    sessionProjectToken: 1,
    selectedRun: { run_id: "run" },
    displayedRunId: "run",
    runResultLoadError: "",
    runResultRequestToken: 0,
    resultOverview: previousOverview,
    sectionTraceMap: { s: { marker: "old-trace" } },
    sectionPatchMap: previousPatches,
    sectionFeedbackHistory: null,
    sectionExecutionAuditDetail: null,
    sectionExampleDetail: null,
    sectionRunDetailLoadState: { key: "", promise: null },
    feedbackForm: {},
    traceArtifactRequestToken: 0,
    loadingTraceArtifact: false,
    p52FeedbackPatchesRequestToken: 0,
    loadingP52FeedbackPatches: false,
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    cancelAllLongWorkflowTaskPolling() {},
    resetSectionFeedbackDraft() {},
    syncSectionAblationFromCache() {},
    ensureSectionSelectionForRun() {},
    loadSelectedSectionDetailBundle: async () => {},
    viewRun: methods.viewRun,
  });
  await vm.viewRun({ run_id: "run" }, 1);
  assert.strictEqual(vm.resultOverview, previousOverview);
  assert.strictEqual(vm.sectionPatchMap, previousPatches);
  assert.strictEqual(vm.sectionTraceMap.s.marker, "new-trace");
  assert.match(vm.runResultLoadError, /已保留上一次成功数据/);
}

async function testCrossRunFailureDoesNotLeakOldData(component, api) {
  const methods = component.methods;
  api.sectionOverview = () => Promise.reject(new Error("timeout"));
  api.sectionTraces = () => Promise.reject(new Error("timeout"));
  api.sectionPatchCandidates = () => Promise.reject(new Error("timeout"));
  const vm = reactiveVm({
    sessionProjectToken: 1,
    selectedRun: { run_id: "old" },
    displayedRunId: "old",
    runResultLoadError: "",
    runResultRequestToken: 0,
    resultOverview: { marker: "old" },
    sectionTraceMap: { old: { marker: "old" } },
    sectionPatchMap: { old: [{ marker: "old" }] },
    sectionFeedbackHistory: { marker: "old" },
    sectionExecutionAuditDetail: { marker: "old" },
    sectionExampleDetail: { marker: "old" },
    sectionRunDetailLoadState: { key: "old", promise: null },
    feedbackForm: {},
    traceArtifactRequestToken: 0,
    loadingTraceArtifact: false,
    p52FeedbackPatchesRequestToken: 0,
    loadingP52FeedbackPatches: false,
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    cancelAllLongWorkflowTaskPolling() {},
    resetSectionFeedbackDraft() {},
    syncSectionAblationFromCache() {},
    ensureSectionSelectionForRun() {},
    loadSelectedSectionDetailBundle: async () => {},
    viewRun: methods.viewRun,
  });
  const loading = vm.viewRun({ run_id: "new" }, 1);
  assert.strictEqual(vm.resultOverview, null);
  assert.deepStrictEqual(vm.sectionTraceMap, {});
  assert.deepStrictEqual(vm.sectionPatchMap, {});
  await loading;
  assert.strictEqual(vm.displayedRunId, "new");
  assert.strictEqual(vm.resultOverview, null);
  assert.match(vm.runResultLoadError, /重新加载/);
}

function testCrossSectionDetailsClear(component) {
  const methods = component.methods;
  const vm = {
    sectionFeedbackHistory: { section: "old" },
    sectionRulesDetail: { section: "old" },
    sectionPromptRuleDetail: { section: "old" },
    sectionExampleDetail: { section: "old" },
    sectionExperienceMemoryDetail: { section: "old" },
    sectionExecutionAuditDetail: { section: "old" },
    sectionRunDetailLoadState: { key: "old", promise: Promise.resolve() },
    sectionStaticDetailLoadState: { key: "old", promise: Promise.resolve() },
  };
  methods.clearSelectedSectionDetailState.call(vm);
  [
    "sectionFeedbackHistory",
    "sectionRulesDetail",
    "sectionPromptRuleDetail",
    "sectionExampleDetail",
    "sectionExperienceMemoryDetail",
    "sectionExecutionAuditDetail",
  ].forEach((key) => assert.strictEqual(vm[key], null));
  assert.strictEqual(vm.sectionRunDetailLoadState.key, "");
  assert.strictEqual(vm.sectionStaticDetailLoadState.key, "");
}

async function testDiagnosticsEpoch(component, api) {
  const methods = component.methods;
  const oldRequest = deferred();
  const newRequest = deferred();
  let count = 0;
  api.submissionSectionDiagnostics = () => (++count === 1 ? oldRequest.promise : newRequest.promise);
  const vm = reactiveVm({
    projectId: "p",
    sessionProjectToken: 1,
    activeDocId: "doc",
    sectionDiagnosticsMap: {},
    sectionDiagnosticsRequestTokens: {},
    loadingSectionDiagnostics: false,
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    loadSubmissionSectionDiagnostics: methods.loadSubmissionSectionDiagnostics,
  });
  const oldLoad = vm.loadSubmissionSectionDiagnostics("doc", true);
  const newLoad = vm.loadSubmissionSectionDiagnostics("doc", true);
  newRequest.resolve({ data: { marker: "new" } });
  assert.deepStrictEqual(await newLoad, { marker: "new" });
  oldRequest.reject(new Error("old failed"));
  assert.strictEqual(await oldLoad, null);
  assert.strictEqual(vm.sectionDiagnosticsMap.doc.marker, "new");
  assert.strictEqual(vm.loadingSectionDiagnostics, false);
}

async function testP52LoadingConverges(component, api) {
  const methods = component.methods;
  const request = deferred();
  api.listP52FeedbackPatches = () => request.promise;
  const vm = reactiveVm({
    projectId: "p",
    sessionProjectToken: 1,
    selectedRun: { run_id: "run" },
    currentBrowseSectionId: "3.2.p.5.2",
    p52FeedbackPatches: [],
    p52FeedbackPatchesRequestToken: 0,
    loadingP52FeedbackPatches: false,
    feedbackOptimizeResult: {},
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    createSectionDetailRequestContext: methods.createSectionDetailRequestContext,
    isSectionDetailRequestContextActive: methods.isSectionDetailRequestContextActive,
    isP52SectionId: methods.isP52SectionId,
    loadSelectedP52FeedbackPatches: methods.loadSelectedP52FeedbackPatches,
  });
  const staleLoad = vm.loadSelectedP52FeedbackPatches(true);
  assert.strictEqual(vm.loadingP52FeedbackPatches, true);
  vm.currentBrowseSectionId = "3.2.p.3";
  await vm.loadSelectedP52FeedbackPatches();
  assert.strictEqual(vm.loadingP52FeedbackPatches, false);
  request.resolve({ data: { items: [{ patch_id: "stale" }] } });
  await staleLoad;
  assert.deepStrictEqual(vm.p52FeedbackPatches, []);
  assert.strictEqual(vm.loadingP52FeedbackPatches, false);
}

async function testStoppedRunPollingCannotAppendLateLogs(component, api) {
  const methods = component.methods;
  const request = deferred();
  api.getPreReviewTaskProgress = () => request.promise;
  const appended = [];
  const context = { epoch: 1, projectToken: 1, projectId: "p", taskType: "run", sectionId: "", docId: "d" };
  const vm = {
    projectId: "p",
    sessionProjectToken: 1,
    mainRunOperationEpoch: 1,
    runPollingTaskId: "task-old",
    runPollingContext: context,
    runStreamCursor: 0,
    appendRunStreamLog: (_type, item) => appended.push(item),
    isMainRunContextCurrent: methods.isMainRunContextCurrent,
    assertMainRunTaskSnapshot: methods.assertMainRunTaskSnapshot,
    fetchRunTaskProgress: methods.fetchRunTaskProgress,
  };
  const loading = vm.fetchRunTaskProgress("task-old", context);
  vm.runPollingTaskId = "";
  request.resolve({ data: { status: "running", logs: [{ message: "stale" }], next_cursor: 9 } });
  const data = await loading;
  assert.strictEqual(data.stale, true);
  assert.deepStrictEqual(appended, []);
  assert.strictEqual(vm.runStreamCursor, 0);
}

async function testStoppedRunPollingSettlesWaiter(component) {
  const methods = component.methods;
  const request = deferred();
  const context = { epoch: 1, projectToken: 1, projectId: "p", taskType: "run", sectionId: "", docId: "d" };
  const vm = {
    projectId: "p",
    sessionProjectToken: 1,
    mainRunOperationEpoch: 1,
    runPollingTimer: null,
    runPollingTaskId: "task-old",
    runPollingCancel: null,
    runPollingContext: context,
    runStreamStatus: "running",
    fetchRunTaskProgress: () => request.promise,
    isMainRunContextCurrent: methods.isMainRunContextCurrent,
    mainRunCancellation: methods.mainRunCancellation,
    stopRunPolling: methods.stopRunPolling,
    waitForRunTask: methods.waitForRunTask,
  };
  const waiting = vm.waitForRunTask("task-old", context).catch((error) => error);
  vm.stopRunPolling("切换项目");
  const cancellation = await waiting;
  assert.strictEqual(cancellation.isRunPollingCancelled, true);
  assert.strictEqual(vm.runPollingTaskId, "");
  request.resolve({ status: "completed", result: { marker: "stale" } });
  await Promise.resolve();
  assert.strictEqual(vm.runPollingTimer, null);
}

async function testMainRunStartResponseBoundToProject(component, api) {
  const methods = component.methods;
  const start = deferred();
  api.startPreReviewTask = () => start.promise;
  let waitCount = 0;
  const messages = [];
  const vm = reactiveVm({
    projectId: "project-a",
    sessionProjectToken: 1,
    mainRunOperationEpoch: 0,
    activeDocId: "doc-a",
    running: false,
    runningSection: false,
    runningModule: false,
    runPollingTimer: null,
    runPollingTaskId: "",
    runPollingCancel: null,
    runPollingContext: null,
    runStreamLogs: [],
    runStreamCursor: 0,
    runStreamScopeSectionId: "",
    runStreamStatus: "idle",
    expandedLogSections: ["__run__"],
    $message: {
      warning: (message) => messages.push(message),
      success: (message) => messages.push(message),
      error: (message) => messages.push(message),
    },
    buildRunPayload: () => ({ project_id: "project-a", source_doc_id: "doc-a" }),
    loadRuns: async () => {},
    waitForRunTask: async () => { waitCount += 1; },
    stopRunPolling: methods.stopRunPolling,
    resetRunStream: methods.resetRunStream,
    beginMainRunOperation: methods.beginMainRunOperation,
    isMainRunContextCurrent: methods.isMainRunContextCurrent,
    mainRunCancellation: methods.mainRunCancellation,
    isRunPollingCancellation: methods.isRunPollingCancellation,
    assertMainRunTaskSnapshot: methods.assertMainRunTaskSnapshot,
    run: methods.run,
  });
  const running = vm.run();
  vm.stopRunPolling("切换项目");
  vm.mainRunOperationEpoch += 1;
  vm.projectId = "project-b";
  vm.sessionProjectToken = 2;
  vm.running = false;
  start.resolve({ data: { task_id: "old-task", project_id: "project-a", source_doc_id: "doc-a" } });
  await running;
  assert.strictEqual(waitCount, 0);
  assert.strictEqual(vm.runPollingTaskId, "");
  assert.strictEqual(vm.running, false);
  assert.deepStrictEqual(messages, []);
}

async function testMainRunTaskRecovery(component, api) {
  const methods = component.methods;
  const latest = deferred();
  api.getLatestPreReviewMainTask = () => latest.promise;
  const resumed = [];
  const terminal = [];
  const vm = reactiveVm({
    projectId: "project-a",
    sessionProjectToken: 1,
    mainRunOperationEpoch: 0,
    activeDocId: "doc-current",
    running: false,
    runningSection: false,
    runningModule: false,
    runPollingTimer: null,
    runPollingTaskId: "",
    runPollingCancel: null,
    runPollingContext: null,
    runStreamLogs: [],
    runStreamCursor: 0,
    runStreamScopeSectionId: "",
    runStreamStatus: "idle",
    expandedLogSections: ["__run__"],
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    stopRunPolling: methods.stopRunPolling,
    resetRunStream: methods.resetRunStream,
    beginMainRunOperation: methods.beginMainRunOperation,
    setMainRunOperationBusy: methods.setMainRunOperationBusy,
    createRestoredMainRunContext: methods.createRestoredMainRunContext,
    continueRestoredMainRunTask(taskId, context) { resumed.push({ taskId, context }); },
    restoreTerminalMainRunTask(taskId, status, context) { terminal.push({ taskId, status, context }); },
    restoreLatestMainRunTask: methods.restoreLatestMainRunTask,
  });
  const restoring = vm.restoreLatestMainRunTask(1);
  latest.resolve({
    data: {
      task_id: "task-section",
      task_type: "section_replay",
      project_id: "project-a",
      source_doc_id: "doc-a",
      section_id: "3.2.p.2",
      status: "running",
    },
  });
  await restoring;
  assert.strictEqual(resumed.length, 1);
  assert.strictEqual(resumed[0].taskId, "task-section");
  assert.strictEqual(resumed[0].context.projectId, "project-a");
  assert.strictEqual(resumed[0].context.sectionId, "3.2.p.2");
  assert.strictEqual(vm.runPollingTaskId, "task-section");
  assert.strictEqual(vm.runningSection, true);
  assert.strictEqual(vm.runStreamStatus, "running");
  assert.deepStrictEqual(terminal, []);

  api.getLatestPreReviewMainTask = () => Promise.resolve({
    data: {
      task_id: "feedback-task",
      task_type: "feedback_optimize",
      project_id: "project-a",
      status: "running",
    },
  });
  vm.stopRunPolling("测试结束当前恢复任务");
  vm.runningSection = false;
  await vm.restoreLatestMainRunTask(1);
  assert.strictEqual(resumed.length, 1, "反馈任务不得接入主审评状态");

  api.getLatestPreReviewMainTask = () => Promise.resolve({
    data: {
      task_id: "task-pending",
      task_type: "run",
      project_id: "project-a",
      source_doc_id: "doc-a",
      status: "pending",
    },
  });
  await vm.restoreLatestMainRunTask(1);
  assert.strictEqual(resumed.length, 2);
  assert.strictEqual(vm.running, true);
  assert.strictEqual(vm.runStreamStatus, "connecting");
  vm.stopRunPolling("测试结束等待恢复任务");
  vm.running = false;

  api.getLatestPreReviewMainTask = () => Promise.resolve({
    data: {
      task_id: "task-terminal",
      task_type: "run",
      project_id: "project-a",
      source_doc_id: "doc-a",
      status: "completed",
    },
  });
  await vm.restoreLatestMainRunTask(1);
  assert.strictEqual(terminal.length, 1);
  assert.strictEqual(vm.running, false, "终态恢复不得锁住运行按钮");
}

async function testStaleProjectCannotRestoreMainRunTask(component, api) {
  const methods = component.methods;
  const latest = deferred();
  api.getLatestPreReviewMainTask = () => latest.promise;
  let resumed = false;
  const vm = reactiveVm({
    projectId: "project-a",
    sessionProjectToken: 1,
    mainRunOperationEpoch: 0,
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    restoreLatestMainRunTask: methods.restoreLatestMainRunTask,
    continueRestoredMainRunTask() { resumed = true; },
  });
  const restoring = vm.restoreLatestMainRunTask(1);
  vm.projectId = "project-b";
  vm.sessionProjectToken = 2;
  vm.mainRunOperationEpoch = 1;
  latest.resolve({
    data: {
      task_id: "old-task",
      task_type: "run",
      project_id: "project-a",
      status: "running",
    },
  });
  await restoring;
  assert.strictEqual(resumed, false);
}

async function testTerminalMainRunRecoveryRestoresLogsWithoutBusy(component, api) {
  const methods = component.methods;
  api.getPreReviewTaskProgress = () => Promise.resolve({
    data: {
      task_id: "task-terminal",
      task_type: "run",
      project_id: "project-a",
      source_doc_id: "doc-a",
      status: "completed",
      logs: [{ stage: "run_done", message: "审评完成" }],
      next_cursor: 4,
    },
  });
  const appended = [];
  const context = {
    epoch: 2,
    projectToken: 1,
    projectId: "project-a",
    taskType: "run",
    sectionId: "",
    docId: "doc-a",
  };
  const vm = reactiveVm({
    projectId: "project-a",
    sessionProjectToken: 1,
    mainRunOperationEpoch: 2,
    running: false,
    runningSection: false,
    runningModule: false,
    runStreamLogs: [],
    runStreamCursor: 0,
    runStreamScopeSectionId: "",
    runStreamStatus: "idle",
    expandedLogSections: ["__run__"],
    isMainRunContextCurrent: methods.isMainRunContextCurrent,
    assertMainRunTaskSnapshot: methods.assertMainRunTaskSnapshot,
    appendRunStreamLog(type, item) { appended.push({ type, item }); },
    restoreTerminalMainRunTask: methods.restoreTerminalMainRunTask,
  });
  await vm.restoreTerminalMainRunTask("task-terminal", "completed", context);
  assert.strictEqual(vm.runStreamStatus, "completed");
  assert.strictEqual(vm.runStreamCursor, 4);
  assert.strictEqual(appended.length, 1);
  assert.strictEqual(appended[0].item.message, "审评完成");
  assert.strictEqual(vm.running, false);
  assert.strictEqual(vm.runningSection, false);
  assert.strictEqual(vm.runningModule, false);
}

async function testRuleSelectionRace(component, api) {
  const methods = component.methods;
  const a = deferred();
  const b = deferred();
  api.filteredGlobalSectionRules = (sectionId) => (sectionId === "a" ? a.promise : b.promise);
  const errors = [];
  const vm = reactiveVm({
    selectedSectionId: "a",
    currentRuleScopeMetadata: {},
    rulesRequestToken: 0,
    loadingRules: false,
    manualRules: [],
    effectiveRuleItems: [],
    selectedDeletableRuleKeys: [],
    $message: { error: (value) => errors.push(value) },
    loadCurrentSectionRules: methods.loadCurrentSectionRules,
  });
  const oldLoad = vm.loadCurrentSectionRules();
  vm.selectedSectionId = "b";
  const newLoad = vm.loadCurrentSectionRules();
  b.resolve({ data: { items: [{ id: "b" }], manual_rule_texts: ["B"] } });
  await newLoad;
  a.reject(new Error("stale A failed"));
  await oldLoad;
  assert.deepStrictEqual(vm.manualRules, ["B"]);
  assert.strictEqual(vm.effectiveRuleItems[0].id, "b");
  assert.strictEqual(vm.loadingRules, false);
  assert.deepStrictEqual(errors, []);
}

async function testParsedPreviewRace(component, api) {
  const methods = component.methods;
  const a = deferred();
  const b = deferred();
  api.getFilingSubmissionParsedMarkdown = (_projectId, docId) => (docId === "a" ? a.promise : b.promise);
  const vm = reactiveVm({
    projectId: "p",
    parsedMarkdownRequestToken: 0,
    parsedMarkdownTitle: "",
    parsedMarkdownContent: "",
    parsedDialogVisible: false,
    $message: { error() {} },
    viewParsedMarkdown: methods.viewParsedMarkdown,
  });
  const oldLoad = vm.viewParsedMarkdown({ doc_id: "a", file_name: "A" });
  const newLoad = vm.viewParsedMarkdown({ doc_id: "b", file_name: "B" });
  b.resolve({ data: { markdown: "new B" } });
  await newLoad;
  a.resolve({ data: { markdown: "stale A" } });
  await oldLoad;
  assert.strictEqual(vm.parsedMarkdownContent, "new B");
  assert.match(vm.parsedMarkdownTitle, /B/);
}

async function testProjectListRace(component, api) {
  const methods = component.methods;
  const a = deferred();
  const b = deferred();
  let callCount = 0;
  api.listProjects = () => (++callCount === 1 ? a.promise : b.promise);
  const errors = [];
  const vm = reactiveVm({
    filters: { project_name: "A", registration_scope: "", registration_path: [], registration_leaf: "" },
    projects: [{ project_id: "initial" }],
    selectedProject: { project_id: "" },
    projectListRequestToken: 0,
    loadingProjects: false,
    resolveRegistrationLeaf: () => "",
    syncUploadTaskStateFromProjects() {},
    $message: { error: (message) => errors.push(message) },
    loadProjects: methods.loadProjects,
  });
  const oldLoad = vm.loadProjects();
  assert.strictEqual(vm.loadingProjects, true);
  vm.filters.project_name = "B";
  const newLoad = vm.loadProjects();
  b.resolve({ data: { list: [{ project_id: "b" }] } });
  await newLoad;
  a.reject(new Error("stale A"));
  await oldLoad;
  assert.deepStrictEqual(vm.projects.map((item) => item.project_id), ["b"]);
  assert.strictEqual(vm.loadingProjects, false);
  assert.deepStrictEqual(errors, []);
}

async function testSemanticSearchRace(component, api) {
  const methods = component.methods;
  const a = deferred();
  const b = deferred();
  api.semanticQuery = ({ query }) => (query === "A" ? a.promise : b.promise);
  const errors = [];
  const vm = {
    semantic: { query: "A", hits: [], grouped_docs: [], loading: false },
    semanticRequestToken: 0,
    filters: { classification: "" },
    unwrapApiData: methods.unwrapApiData,
    buildGroupedFromHits: (items) => items,
    $message: { warning() {}, error: (message) => errors.push(message) },
    doSemantic: methods.doSemantic,
  };
  const oldLoad = vm.doSemantic();
  vm.semantic.query = "B";
  const newLoad = vm.doSemantic();
  b.resolve({ data: { grouped_docs: [{ doc_id: "b" }] } });
  await newLoad;
  a.reject(new Error("stale A"));
  await oldLoad;
  assert.deepStrictEqual(vm.semantic.grouped_docs.map((item) => item.doc_id), ["b"]);
  assert.strictEqual(vm.semantic.loading, false);
  assert.deepStrictEqual(errors, []);
}

async function testPreReviewProjectDetailRaces(component, api) {
  const methods = component.methods;
  const runA = deferred();
  const runB = deferred();
  api.runHistory = ({ project_id: projectId }) => (projectId === "a" ? runA.promise : runB.promise);
  const errors = [];
  const vm = {
    selectedProject: { project_id: "" },
    runs: [],
    runsRequestToken: 0,
    loadingRuns: false,
    $message: { error: (message) => errors.push(message) },
    viewRuns: methods.viewRuns,
  };
  const oldRuns = vm.viewRuns({ project_id: "a", project_name: "A" });
  const newRuns = vm.viewRuns({ project_id: "b", project_name: "B" });
  runB.resolve({ data: { list: [{ run_id: "run-b" }] } });
  await newRuns;
  runA.reject(new Error("stale run A"));
  await oldRuns;
  assert.strictEqual(vm.selectedProject.project_id, "b");
  assert.deepStrictEqual(vm.runs.map((item) => item.run_id), ["run-b"]);
  assert.strictEqual(vm.loadingRuns, false);

  const catalogA = deferred();
  const catalogB = deferred();
  api.ctdCatalog = (projectId) => (projectId === "a" ? catalogA.promise : catalogB.promise);
  Object.assign(vm, {
    projectUploadDialog: { visible: false, project_id: "" },
    projectUploadCatalog: [],
    uploadCatalogRequestToken: 0,
    defaultUploadBranch() { return "all"; },
    normalizeProjectUploadMode() {},
    openProjectUpload: methods.openProjectUpload,
  });
  const oldCatalog = vm.openProjectUpload({ project_id: "a", project_name: "A" });
  const newCatalog = vm.openProjectUpload({ project_id: "b", project_name: "B" });
  catalogB.resolve({ data: { chapter_structure: [{ section_id: "b" }] } });
  await newCatalog;
  catalogA.resolve({ data: { chapter_structure: [{ section_id: "a" }] } });
  await oldCatalog;
  assert.strictEqual(vm.projectUploadDialog.project_id, "b");
  assert.deepStrictEqual(vm.projectUploadCatalog.map((item) => item.section_id), ["b"]);
  assert.deepStrictEqual(errors, []);
}

async function testFilingPollingIsSequential(component, api) {
  const methods = component.methods;
  const oldRequest = deferred();
  const newRequest = deferred();
  const newRequest2 = deferred();
  const calls = [];
  api.getFilingReviewTaskProgress = (taskId) => {
    calls.push(taskId);
    if (taskId === "old-task") return oldRequest.promise;
    return calls.filter((item) => item === "new-task").length === 1 ? newRequest.promise : newRequest2.promise;
  };
  const scheduled = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const originalClearInterval = global.clearInterval;
  global.setTimeout = (fn) => {
    scheduled.push(fn);
    return scheduled.length;
  };
  global.clearTimeout = () => {};
  global.clearInterval = () => {};
  try {
    const vm = {
      projectId: "p",
      reviewPollTimer: null,
      reviewPollToken: 0,
      reviewTask: {},
      running: true,
      loadRunHistory: async () => {},
      loadReport: async () => {},
      $message: { success() {}, error() {} },
      startProgressPolling: methods.startProgressPolling,
    };
    vm.startProgressPolling("old-task", "old-run");
    vm.startProgressPolling("new-task", "new-run");
    oldRequest.resolve({ data: { task_id: "old-task", status: "pending", message: "old" } });
    await Promise.resolve();
    assert.notStrictEqual(vm.reviewTask.task_id, "old-task");
    newRequest.resolve({ data: { task_id: "new-task", status: "pending", message: "new" } });
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(vm.reviewTask.task_id, "new-task");
    assert.deepStrictEqual(calls, ["old-task", "new-task"]);
    assert.strictEqual(scheduled.length, 1);
    scheduled.shift()();
    assert.deepStrictEqual(calls, ["old-task", "new-task", "new-task"]);
    assert.strictEqual(scheduled.length, 0);
    newRequest2.resolve({ data: { task_id: "new-task", status: "pending", message: "new-2" } });
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(scheduled.length, 1);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
    global.clearInterval = originalClearInterval;
  }
}

function createFilingParseVm(component, seed = {}) {
  const vm = reactiveVm({
    projectId: "project-a",
    filingParseTaskStates: {},
    filingParseTaskEpochs: {},
    filingParseTaskPollers: {},
    ...seed,
  });
  [
    "filingParseCancellation",
    "isFilingParseCancellation",
    "filingParseErrorMessage",
    "isFilingParseTaskRunning",
    "cancelFilingParseTaskPolling",
    "cancelAllFilingParseTaskPolling",
    "beginFilingParseOperation",
    "isFilingParseOperationCurrent",
    "waitForFilingParseTask",
  ].forEach((name) => {
    vm[name] = component.methods[name];
  });
  return vm;
}

async function testFilingParsePollingIsSequentialAndBound(component, api) {
  const oldRequest = deferred();
  const newRequest = deferred();
  const newRequest2 = deferred();
  const projectRequest = deferred();
  const calls = [];
  api.getFilingParseTaskProgress = (taskId) => {
    calls.push(taskId);
    if (taskId === "task-old") return oldRequest.promise;
    if (taskId === "task-project") return projectRequest.promise;
    return calls.filter((item) => item === "task-new").length === 1
      ? newRequest.promise
      : newRequest2.promise;
  };
  const scheduled = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (fn) => {
    scheduled.push(fn);
    return scheduled.length;
  };
  global.clearTimeout = () => {};
  try {
    const vm = createFilingParseVm(component);
    const oldEpoch = vm.beginFilingParseOperation("submission:doc", "project-a");
    const oldWait = vm.waitForFilingParseTask("task-old", {
      key: "submission:doc",
      projectId: "project-a",
      epoch: oldEpoch,
    }).catch((error) => error);
    const newEpoch = vm.beginFilingParseOperation("submission:doc", "project-a");
    const newWait = vm.waitForFilingParseTask("task-new", {
      key: "submission:doc",
      projectId: "project-a",
      epoch: newEpoch,
    });
    const cancelled = await oldWait;
    assert.strictEqual(vm.isFilingParseCancellation(cancelled), true);
    oldRequest.resolve({ data: { task_id: "task-old", project_id: "project-a", status: "completed" } });
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(vm.filingParseTaskStates["submission:doc"].task_id, "task-new");
    assert.deepStrictEqual(calls, ["task-old", "task-new"]);
    assert.strictEqual(scheduled.length, 0, "进度请求未完成前不应启动下一轮询");

    newRequest.resolve({ data: { task_id: "task-new", project_id: "project-a", status: "running", next_cursor: 2 } });
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(scheduled.length, 1);
    scheduled.shift()();
    assert.deepStrictEqual(calls, ["task-old", "task-new", "task-new"]);
    assert.strictEqual(scheduled.length, 0, "同一任务同一时刻只能有一个进度请求");
    newRequest2.resolve({ data: { task_id: "task-new", project_id: "project-a", status: "completed", result: { data: {} } } });
    const completed = await newWait;
    assert.strictEqual(completed.status, "completed");
    assert.strictEqual(vm.filingParseTaskStates["submission:doc"].status, "completed");
    assert.deepStrictEqual(vm.filingParseTaskPollers, {});

    const projectEpoch = vm.beginFilingParseOperation("reference:doc", "project-a");
    const projectWait = vm.waitForFilingParseTask("task-project", {
      key: "reference:doc",
      projectId: "project-a",
      epoch: projectEpoch,
    }).catch((error) => error);
    vm.projectId = "project-b";
    projectRequest.resolve({ data: { task_id: "task-project", status: "completed" } });
    const projectCancelled = await projectWait;
    assert.strictEqual(vm.isFilingParseCancellation(projectCancelled), true);
    assert.notStrictEqual(vm.filingParseTaskStates["reference:doc"].status, "completed");
    assert.strictEqual(vm.filingParseTaskPollers["reference:doc"], undefined);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
}

async function testFilingParseCanonicalApiRoutes() {
  const calls = [];
  const http = {
    post(url, data, config) {
      calls.push({ method: "post", url, data, config });
      return Promise.resolve({});
    },
    get(url, config) {
      calls.push({ method: "get", url, config });
      return Promise.resolve({});
    },
  };
  const source = fs.readFileSync(path.join(FRONTEND, "src", "api", "filingChangeReview.js"), "utf8");
  const api = compileScript(source, (moduleName) => {
    if (moduleName === "./http") return http;
    throw new Error(moduleName);
  });
  api.startApplicationFormImport("p", { file: true }, { silentError: true });
  api.startApplicationFormParse("p", { silentError: true });
  api.startFilingSubmissionParse("p", "d", { silentError: true });
  api.startBatchFilingSubmissionsParse("p", { doc_ids: ["d"] }, { silentError: true });
  api.startFilingReferenceMaterialParse("r", { silentError: true });
  api.getFilingParseTaskProgress("t", { cursor: 7 }, { silentError: true });
  assert.deepStrictEqual(calls.map((item) => item.url), [
    "/filing-change-review/projects/p/application-form/import/start",
    "/filing-change-review/projects/p/application-form/parse/start",
    "/filing-change-review/projects/p/submissions/d/parse/start",
    "/filing-change-review/projects/p/submissions/parse-batch/start",
    "/filing-change-review/reference-materials/r/parse/start",
    "/filing-change-review/tasks/t/progress",
  ]);
  calls.forEach((item) => assert.strictEqual(item.config.silentError, true));
  assert.strictEqual(calls[5].config.params.cursor, 7);
  assert.strictEqual(calls[0].config.headers["Content-Type"], "multipart/form-data");
  const sessionSource = fs.readFileSync(
    path.join(FRONTEND, "src", "views", "filing-change-review", "FilingChangeReviewSession.vue"),
    "utf8",
  );
  assert.match(sessionSource, /startApplicationFormImport\(projectId, fd/);
  assert.doesNotMatch(sessionSource, /await importApplicationForm\(/);
}

function createLongWorkflowVm(component, seed = {}) {
  const vm = reactiveVm({
    projectId: "project-a",
    sessionProjectToken: 1,
    selectedRun: { run_id: "run-a" },
    currentBrowseSectionId: "section-a",
    longWorkflowTaskStates: {},
    longWorkflowTaskEpochs: {},
    longWorkflowTaskPollers: {},
    submittingFeedback: false,
    submittingP52FeedbackOptimize: false,
    replayingFeedbackOptimize: false,
    replayingP52FeedbackVerify: false,
    replayingMetaReflection: false,
    runningAblation: false,
    ...seed,
  });
  [
    "longWorkflowCancellation",
    "isLongWorkflowCancellation",
    "longWorkflowErrorMessage",
    "createLongWorkflowContext",
    "setLongWorkflowOperationIdle",
    "cancelLongWorkflowTaskPolling",
    "cancelAllLongWorkflowTaskPolling",
    "beginLongWorkflowOperation",
    "isLongWorkflowContextCurrent",
    "waitForLongWorkflowTask",
  ].forEach((name) => {
    vm[name] = component.methods[name];
  });
  return vm;
}

async function testLongWorkflowPollingContext(component, api) {
  const oldRequest = deferred();
  const newRequest = deferred();
  const newRequest2 = deferred();
  const staleSectionRequest = deferred();
  const calls = [];
  api.getPreReviewTaskProgress = (taskId) => {
    calls.push(taskId);
    if (taskId === "workflow-old") return oldRequest.promise;
    if (taskId === "workflow-section") return staleSectionRequest.promise;
    return calls.filter((item) => item === "workflow-new").length === 1
      ? newRequest.promise
      : newRequest2.promise;
  };
  const scheduled = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (fn) => {
    scheduled.push(fn);
    return scheduled.length;
  };
  global.clearTimeout = () => {};
  try {
    const vm = createLongWorkflowVm(component);
    const oldContext = vm.beginLongWorkflowOperation("feedback_optimize");
    const oldWait = vm.waitForLongWorkflowTask("workflow-old", oldContext).catch((error) => error);
    const newContext = vm.beginLongWorkflowOperation("feedback_optimize");
    const newWait = vm.waitForLongWorkflowTask("workflow-new", newContext);
    assert.strictEqual(vm.isLongWorkflowCancellation(await oldWait), true);
    oldRequest.resolve({ data: {
      task_id: "workflow-old", project_id: "project-a", run_id: "run-a", section_id: "section-a", status: "completed",
    } });
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(vm.longWorkflowTaskStates[newContext.key].task_id, "workflow-new");
    assert.strictEqual(scheduled.length, 0);

    newRequest.resolve({ data: {
      task_id: "workflow-new", project_id: "project-a", run_id: "run-a", section_id: "section-a", status: "running",
    } });
    await Promise.resolve();
    await Promise.resolve();
    assert.strictEqual(scheduled.length, 1);
    scheduled.shift()();
    assert.deepStrictEqual(calls, ["workflow-old", "workflow-new", "workflow-new"]);
    newRequest2.resolve({ data: {
      task_id: "workflow-new",
      project_id: "project-a",
      run_id: "run-a",
      section_id: "section-a",
      status: "completed",
      result: { ok: true, message: "done", data: { marker: "new" } },
    } });
    const completed = await newWait;
    assert.strictEqual(completed.data.marker, "new");
    assert.strictEqual(vm.longWorkflowTaskStates[newContext.key].status, "completed");

    const sectionContext = vm.beginLongWorkflowOperation("p52_feedback_verify");
    const sectionWait = vm.waitForLongWorkflowTask("workflow-section", sectionContext).catch((error) => error);
    vm.currentBrowseSectionId = "section-b";
    vm.cancelAllLongWorkflowTaskPolling("切换章节");
    assert.strictEqual(vm.isLongWorkflowCancellation(await sectionWait), true);
    staleSectionRequest.resolve({ data: {
      task_id: "workflow-section", project_id: "project-a", run_id: "run-a", section_id: "section-a", status: "completed",
    } });
    await Promise.resolve();
    assert.notStrictEqual(vm.longWorkflowTaskStates[sectionContext.key].status, "completed");
    assert.strictEqual(vm.replayingP52FeedbackVerify, false);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
}

async function testLongWorkflowCanonicalApiRoutes() {
  const calls = [];
  const http = {
    post(url, data, config) {
      calls.push({ method: "post", url, data, config });
      return Promise.resolve({});
    },
    get(url, config) {
      calls.push({ method: "get", url, config });
      return Promise.resolve({});
    },
  };
  const source = fs.readFileSync(path.join(FRONTEND, "src", "api", "prereview.js"), "utf8");
  const api = compileScript(source, (moduleName) => {
    if (moduleName === "./http") return http;
    throw new Error(moduleName);
  });
  api.optimizeFeedbackAsync("r", {}, { silentError: true });
  api.optimizeP52FeedbackAsync("r", "s", {}, { silentError: true });
  api.replayVerifyP52FeedbackAsync("r", "s", {}, { silentError: true });
  api.replayP52MetaReflectionAsync("r", "s", {}, { silentError: true });
  api.replayFeedbackOptimizeAsync("r", "s", {}, { silentError: true });
  api.replayMetaReflectionAsync("r", "s", {}, { silentError: true });
  api.replayEvaluationCasesAsync({}, { silentError: true });
  api.runAblationStudyAsync({}, { silentError: true });
  assert.deepStrictEqual(calls.map((item) => item.url), [
    "/pre-review/runs/r/feedback/optimize/async",
    "/pre-review/runs/r/sections/s/p52-feedback/optimize/async",
    "/pre-review/runs/r/sections/s/p52-feedback/replay-verify/async",
    "/pre-review/runs/r/sections/s/p52-feedback/meta-reflection/async",
    "/pre-review/runs/r/sections/s/replay/feedback-optimize/async",
    "/pre-review/runs/r/sections/s/replay/meta-reflection/async",
    "/pre-review/feedback/evaluation/replay-cases/async",
    "/pre-review/feedback/evaluation/ablation/async",
  ]);
  calls.forEach((item) => assert.strictEqual(item.config.silentError, true));
  const sessionSource = fs.readFileSync(path.join(FRONTEND, "src", "views", "PreReviewSession.vue"), "utf8");
  [
    "optimizeFeedback",
    "optimizeP52Feedback",
    "replayVerifyP52Feedback",
    "replayP52MetaReflection",
    "replayFeedbackOptimize",
    "replayMetaReflection",
  ].forEach((name) => assert.doesNotMatch(sessionSource, new RegExp(`await\\s+${name}\\(`)));
}

async function testCompactApiDefaults() {
  const calls = [];
  const http = {
    post(url, data, config) {
      calls.push({ url, data, config });
      return Promise.resolve({});
    },
  };
  const source = fs.readFileSync(path.join(FRONTEND, "src", "api", "prereview.js"), "utf8");
  const api = compileScript(source, (moduleName) => {
    if (moduleName === "./http") return http;
    throw new Error(moduleName);
  });
  api.submissionSections("p", "d");
  api.sectionOverview("r");
  api.sectionTraces("r", {});
  assert.deepStrictEqual(calls.map((item) => item.data.compact), [true, true, true]);
  calls.length = 0;
  api.sectionTraces("r", { section_id: "s", compact: false });
  assert.strictEqual(calls[0].data.compact, false);
}

async function testMainRunRecoveryApiRoute() {
  const calls = [];
  const http = {
    get(url, config) {
      calls.push({ url, config });
      return Promise.resolve({});
    },
    post() { return Promise.resolve({}); },
  };
  const source = fs.readFileSync(path.join(FRONTEND, "src", "api", "prereview.js"), "utf8");
  const api = compileScript(source, (moduleName) => {
    if (moduleName === "./http") return http;
    throw new Error(moduleName);
  });
  await api.getLatestPreReviewMainTask("project-a", { includeTerminal: true });
  assert.deepStrictEqual(calls[0], {
    url: "/pre-review/projects/project-a/runs/tasks/latest",
    config: { params: { include_terminal: 1 }, silentError: true },
  });
}

async function main() {
  const preReviewApi = {};
  const session = loadSfc("src/views/PreReviewSession.vue", preReviewApi);
  const criteriaApi = {};
  const criteria = loadSfc("src/views/ReviewCriteriaManage.vue", criteriaApi);
  const filingApi = {};
  const filing = loadSfc("src/views/filing-change-review/FilingChangeReviewSession.vue", filingApi);
  const manageApi = {};
  const manage = loadSfc("src/views/PreReviewManage.vue", manageApi);
  const knowledgeApi = {};
  const knowledge = loadSfc("src/views/KnowledgeManage.vue", knowledgeApi);
  const cases = [
    ["旧运行响应不覆盖新运行", () => testRunRace(session, preReviewApi)],
    ["同一运行刷新部分失败保留成功缓存", () => testSameRunRefreshFailurePreservesData(session, preReviewApi)],
    ["跨运行加载失败不泄漏旧运行数据", () => testCrossRunFailureDoesNotLeakOldData(session, preReviewApi)],
    ["跨章节切换立即清理旧详情", () => testCrossSectionDetailsClear(session)],
    ["同文档诊断强制刷新按代次收敛", () => testDiagnosticsEpoch(session, preReviewApi)],
    ["P52 旧请求不造成永久转圈", () => testP52LoadingConverges(session, preReviewApi)],
    ["已停止的运行轮询不回写过期日志", () => testStoppedRunPollingCannotAppendLateLogs(session, preReviewApi)],
    ["停止运行轮询会收敛等待 Promise", () => testStoppedRunPollingSettlesWaiter(session)],
    ["主审评启动响应绑定发起项目", () => testMainRunStartResponseBoundToProject(session, preReviewApi)],
    ["刷新只恢复当前项目主审评任务", () => testMainRunTaskRecovery(session, preReviewApi)],
    ["旧项目恢复响应不得回写", () => testStaleProjectCannotRestoreMainRunTask(session, preReviewApi)],
    ["终态主审评恢复日志不锁按钮", () => testTerminalMainRunRecoveryRestoresLogsWithoutBusy(session, preReviewApi)],
    ["快速切换章节不被旧规则覆盖", () => testRuleSelectionRace(criteria, criteriaApi)],
    ["快速切换文件不被旧预览覆盖", () => testParsedPreviewRace(filing, filingApi)],
    ["项目列表旧响应不覆盖新查询", () => testProjectListRace(manage, manageApi)],
    ["语义检索旧响应不覆盖新问题", () => testSemanticSearchRace(knowledge, knowledgeApi)],
    ["运行记录与上传目录绑定当前项目", () => testPreReviewProjectDetailRaces(manage, manageApi)],
    ["备案审评轮询串行且旧任务失活", () => testFilingPollingIsSequential(filing, filingApi)],
    ["备案解析轮询串行并绑定任务与项目", () => testFilingParsePollingIsSequentialAndBound(filing, filingApi)],
    ["备案解析 API 使用统一异步路由", testFilingParseCanonicalApiRoutes],
    ["长时反馈任务轮询绑定运行和章节", () => testLongWorkflowPollingContext(session, preReviewApi)],
    ["长时反馈工作流使用异步 API", testLongWorkflowCanonicalApiRoutes],
    ["重型列表 API 默认精简响应", testCompactApiDefaults],
    ["主审评刷新恢复 API 路径固定", testMainRunRecoveryApiRoute],
  ];
  for (const [name, run] of cases) {
    await run();
    console.log(`PASS  ${name}`);
  }
  console.log(`\n${cases.length}/${cases.length} 前端异步状态回归通过`);
}

main().catch((error) => {
  console.error("FAIL  前端异步状态回归失败");
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
