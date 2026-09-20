# agent_fronted
## agent_fronted (Vue2 CLI)

### Run

```bash
cd agent/agent_fronted
npm install
npm run serve
```

Default dev url: `http://localhost:8081`

### Backend proxy

- Frontend request prefix: `/api`
- Proxy target: `http://127.0.0.1:5001`
- Config file: `agent/agent_fronted/vue.config.js`

### Implemented pages

- `系统总览`
- `知识库管理`
  - 批量上传 PDF/Word/txt
  - 条件查询（文件名/关键词/类型/分类）
  - 语义查询（semantic query）
  - 重命名、分类修改、删除
- `智能预审`
  - 项目新增/删除/列表
  - 选择文档执行预审
  - 历史版本与准确率查看
  - 按章节查看结论、高亮问题、关联规则
  - 导出 Word 报告
  - 人工反馈标注（valid/false_positive/missed）
前端目录预留（可迁移 Vue 项目到此处）。
