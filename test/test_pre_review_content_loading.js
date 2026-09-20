/* eslint-disable no-console */
"use strict";

/**
 * 预审会话章节内容加载回归。
 *
 * 这个脚本不引入新的 npm 依赖，而是使用前端已有的 Vue SFC 编译依赖，
 * 直接加载 PreReviewSession.vue 中的真实 methods/computed。请不要在测试中
 * 复制 loadSubmissionText 的业务实现，否则将无法阻止真实页面回归。
 *
 * 运行：
 *   node test/test_pre_review_content_loading.js
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

const AGENT_ROOT = path.resolve(__dirname, "..");
const FRONTEND_ROOT = path.join(AGENT_ROOT, "agent_fronted");
const SESSION_COMPONENT_PATH = path.join(
  FRONTEND_ROOT,
  "src",
  "views",
  "PreReviewSession.vue",
);
const frontendRequire = createRequire(path.join(FRONTEND_ROOT, "package.json"));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function timeoutError(status = 504) {
  return {
    response: { status, data: {} },
    message: `Request failed with status code ${status}`,
  };
}

function loadSessionComponent(api) {
  const compiler = frontendRequire("vue-template-compiler");
  const babel = frontendRequire("@babel/core");
  const source = fs.readFileSync(SESSION_COMPONENT_PATH, "utf8");
  const descriptor = compiler.parseComponent(source);
  assert(descriptor.script && descriptor.script.content, "PreReviewSession.vue 缺少 script 区块");

  const compiled = babel.transformSync(descriptor.script.content, {
    presets: [frontendRequire.resolve("@babel/preset-env")],
    babelrc: false,
    configFile: false,
  }).code;

  class MarkdownItStub {
    constructor() {
      this.renderer = { rules: {} };
    }

    render(value) {
      return String(value || "");
    }
  }

  const apiProxy = new Proxy(api, {
    get(target, property) {
      return target[property] || (() => Promise.resolve({ data: {} }));
    },
  });
  const fakeRequire = (moduleName) => {
    if (moduleName === "@/api/prereview") {
      return apiProxy;
    }
    if (moduleName === "markdown-it") {
      return MarkdownItStub;
    }
    if (moduleName.includes("/constants/")) {
      return { normalizeReviewDomain: (value) => value };
    }
    if (moduleName.includes("/utils/preReviewDisplay")) {
      return { formatReviewConfidence: (value) => String(value || "") };
    }
    if (moduleName.includes("/utils/ctdDisplay")) {
      return {
        buildSectionDisplayLabel: (sectionId, sectionName) => `${sectionId} ${sectionName || ""}`.trim(),
        stripSectionDisplayName: (value) => value,
      };
    }
    if (moduleName.endsWith(".vue")) {
      return {};
    }
    throw new Error(`测试加载器遇到未知模块: ${moduleName}`);
  };

  const loadedModule = { exports: {} };
  // eslint-disable-next-line no-new-func
  new Function("require", "module", "exports", compiled)(
    fakeRequire,
    loadedModule,
    loadedModule.exports,
  );
  return loadedModule.exports.default || loadedModule.exports;
}

function createSessionVm(component) {
  const methods = component.methods;
  const computed = component.computed;
  const messages = [];
  const vm = {
    projectId: "project-test",
    sessionProjectToken: 1,
    activeDocId: "doc-a",
    contentDisplayDocId: "doc-a",
    submissionContentRequestToken: 0,
    submissionContentMap: {},
    submissionContentRequestMap: {},
    loadingSubmissionContent: false,
    submissionContentError: "",
    contentSections: [],
    currentBrowseSectionId: "section-a",
    contentViewMode: "cleaned",
    editorText: "",
    $set(object, key, value) {
      object[key] = value;
    },
    $delete(object, key) {
      delete object[key];
    },
    $message: {
      error(message) {
        messages.push(message);
      },
    },
    _messages: messages,
    isProjectSessionTokenActive: methods.isProjectSessionTokenActive,
    // 这组用例只验证异步状态管理；章节树对齐有独立的后端/解析回归。
    buildProjectAlignedContentSections(sections) {
      return Array.isArray(sections) ? sections : [];
    },
    syncEditorToCurrentSection: methods.syncEditorToCurrentSection,
    submissionContentFailureMessage: methods.submissionContentFailureMessage,
    loadSubmissionText: methods.loadSubmissionText,
    retrySubmissionContent: methods.retrySubmissionContent,
  };
  Object.defineProperty(vm, "currentContentSection", {
    get() {
      return computed.currentContentSection.call(vm);
    },
  });
  return vm;
}

async function testTimeoutKeepsLastSuccessfulContent(component, api) {
  const vm = createSessionVm(component);
  vm.contentSections = [{ section_id: "section-a", content: "上一次成功内容" }];
  vm.editorText = "上一次成功内容";
  api.submissionContent = () => Promise.reject(timeoutError());

  const loaded = await vm.loadSubmissionText("doc-a", true);

  assert.strictEqual(loaded, false);
  assert.strictEqual(vm.editorText, "上一次成功内容");
  assert.strictEqual(vm.contentSections[0].content, "上一次成功内容");
  assert.match(vm.submissionContentError, /已保留上一次成功加载的内容/);
  assert.strictEqual(vm.loadingSubmissionContent, false);
  assert.deepStrictEqual(vm._messages, [vm.submissionContentError]);
}

async function testLateOldDocumentResponseCannotOverwriteNewDocument(component, api) {
  const vm = createSessionVm(component);
  const oldRequest = deferred();
  const newRequest = deferred();
  api.submissionContent = (_projectId, docId) => (
    docId === "doc-a" ? oldRequest.promise : newRequest.promise
  );

  vm.activeDocId = "doc-a";
  vm.currentBrowseSectionId = "section-a";
  const oldLoad = vm.loadSubmissionText("doc-a");

  vm.activeDocId = "doc-b";
  vm.currentBrowseSectionId = "section-b";
  const newLoad = vm.loadSubmissionText("doc-b");

  newRequest.resolve({
    data: { sections: [{ section_id: "section-b", content: "新文档内容" }] },
  });
  assert.strictEqual(await newLoad, true);

  oldRequest.resolve({
    data: { sections: [{ section_id: "section-a", content: "过期旧文档内容" }] },
  });
  assert.strictEqual(await oldLoad, false);
  assert.strictEqual(vm.contentDisplayDocId, "doc-b");
  assert.strictEqual(vm.editorText, "新文档内容");
  assert.strictEqual(vm.contentSections[0].section_id, "section-b");
  assert.strictEqual(vm.loadingSubmissionContent, false);
  assert.strictEqual(vm.submissionContentError, "");
}

async function testLateOldFailureCannotOverwriteNewSuccess(component, api) {
  const vm = createSessionVm(component);
  const oldRequest = deferred();
  const newRequest = deferred();
  api.submissionContent = (_projectId, docId) => (
    docId === "doc-a" ? oldRequest.promise : newRequest.promise
  );

  vm.activeDocId = "doc-a";
  const oldLoad = vm.loadSubmissionText("doc-a");
  vm.activeDocId = "doc-b";
  vm.currentBrowseSectionId = "section-b";
  const newLoad = vm.loadSubmissionText("doc-b");
  newRequest.resolve({
    data: { sections: [{ section_id: "section-b", content: "新请求成功" }] },
  });
  assert.strictEqual(await newLoad, true);

  oldRequest.reject(timeoutError());
  assert.strictEqual(await oldLoad, false);
  assert.strictEqual(vm.editorText, "新请求成功");
  assert.strictEqual(vm.submissionContentError, "");
  assert.strictEqual(vm.loadingSubmissionContent, false);
  assert.deepStrictEqual(vm._messages, []);
}

async function testResponseUsesLatestSelectedChapter(component, api) {
  const vm = createSessionVm(component);
  const request = deferred();
  api.submissionContent = () => request.promise;
  vm.activeDocId = "doc-c";
  vm.contentDisplayDocId = "doc-c";
  vm.currentBrowseSectionId = "section-c1";

  const loading = vm.loadSubmissionText("doc-c");
  vm.currentBrowseSectionId = "section-c2";
  request.resolve({
    data: {
      sections: [
        { section_id: "section-c1", content: "C1" },
        { section_id: "section-c2", content: "C2" },
      ],
    },
  });

  assert.strictEqual(await loading, true);
  assert.strictEqual(vm.currentBrowseSectionId, "section-c2");
  assert.strictEqual(vm.editorText, "C2");
}

async function testConcurrentSameDocumentRequestsAreDeduplicated(component, api) {
  const vm = createSessionVm(component);
  const request = deferred();
  let callCount = 0;
  api.submissionContent = () => {
    callCount += 1;
    return request.promise;
  };

  const firstLoad = vm.loadSubmissionText("doc-a");
  const secondLoad = vm.loadSubmissionText("doc-a");
  request.resolve({
    data: { sections: [{ section_id: "section-a", content: "合并请求内容" }] },
  });

  assert.deepStrictEqual(await Promise.all([firstLoad, secondLoad]), [false, true]);
  assert.strictEqual(callCount, 1);
  assert.strictEqual(vm.editorText, "合并请求内容");
  assert.deepStrictEqual(vm.submissionContentRequestMap, {});
}

async function testRetrySuccessClearsTimeoutError(component, api) {
  const vm = createSessionVm(component);
  vm.contentSections = [{ section_id: "section-a", content: "可保留内容" }];
  vm.editorText = "可保留内容";
  let callCount = 0;
  api.submissionContent = () => {
    callCount += 1;
    if (callCount === 1) {
      return Promise.reject(timeoutError());
    }
    return Promise.resolve({
      data: { sections: [{ section_id: "section-a", content: "重试后新内容" }] },
    });
  };

  await vm.loadSubmissionText("doc-a", true);
  assert.match(vm.submissionContentError, /超时/);
  await vm.retrySubmissionContent();

  assert.strictEqual(callCount, 2);
  assert.strictEqual(vm.editorText, "重试后新内容");
  assert.strictEqual(vm.submissionContentError, "");
  assert.strictEqual(vm.loadingSubmissionContent, false);
}

async function main() {
  const api = {};
  const component = loadSessionComponent(api);
  {
    const pending = deferred();
    let active = true, viewed = null;
    const context = { projectId: "p", projectToken: 1 };
    const vm = { isMainRunContextCurrent: () => active, runs: [], viewRun: async row => { viewed = row.run_id; } };
    api.runHistory = () => pending.promise;
    const request = component.methods.showFailedTaskRun.call(vm, { result: { data: { run_id: "failed" } } }, context);
    active = false;
    pending.resolve({ data: [{ run_id: "failed" }] });
    await request;
    assert.equal(viewed, null);
    assert.deepStrictEqual(vm.runs, []);
    active = true;
    await component.methods.showFailedTaskRun.call(vm, { result: { data: { run_id: "failed" } } }, context);
    assert.equal(viewed, "failed");
    console.log("PASS  失败任务选择对应轮次，旧项目响应不覆盖当前页面");
  }
  for (const summary of [{ review_complete: false }, { execution_status: "failed" }, { review_complete: true }]) {
    const vm = { selectedRun: { run_id: "r", summary_payload: summary } };
    vm.selectedRunSummary = component.computed.selectedRunSummary.call(vm);
    vm.selectedRunIncomplete = component.computed.selectedRunIncomplete.call(vm);
    assert.strictEqual(component.computed.canExportReviewConclusions.call(vm), summary.review_complete === true);
    if (vm.selectedRunIncomplete) {
      let warning = "";
      vm.$message = { warning: (message) => { warning = message; } };
      api.exportReviewConclusions = () => { throw new Error("未完成轮次不得请求导出"); };
      await component.methods.exportCurrentRunReviewConclusions.call(vm);
      assert.match(warning, /未完成/);
    }
  }
  console.log("PASS  失败轮次禁止页面导出，成功历史保持可用");
  const cases = [
    ["504 保留上一次成功内容", testTimeoutKeepsLastSuccessfulContent],
    ["过期文档成功响应不覆盖新文档", testLateOldDocumentResponseCannotOverwriteNewDocument],
    ["过期文档失败不覆盖新状态", testLateOldFailureCannotOverwriteNewSuccess],
    ["同文档响应使用最新章节选择", testResponseUsesLatestSelectedChapter],
    ["同文档并发请求去重", testConcurrentSameDocumentRequestsAreDeduplicated],
    ["重试成功清除超时错误", testRetrySuccessClearsTimeoutError],
  ];

  for (const [name, runCase] of cases) {
    await runCase(component, api);
    console.log(`PASS  ${name}`);
  }
  console.log(`\n${cases.length}/${cases.length} 章节内容加载回归通过`);
}

main().catch((error) => {
  console.error("FAIL  章节内容加载回归失败");
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
