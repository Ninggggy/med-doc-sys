# agent_backend 重构说明

本目录为基于 `knowledge_database-2` 重新组织后的新后端骨架，按新目录结构从头实现。

## 已实现模块
- config/settings.py: 配置加载（含 .env）
- database/mysql|redis|es|vector: 数据访问层
- services/file_service.py: 文件增删查、上传落库、分块状态更新
- common_utils/parser: parser_manager/pdf/docx/material 解析链路
- memory: 记忆体系（working/episodic/semantic/perceptual/long_term）
- memory/rag: 文档处理与检索管道
- common_tools/builtin: memory/rag/note/terminal 工具
- context/builder.py: GSSC 上下文构建
- agents/pre_review_agent: 预审智能体状态、节点、图
- agentic_rl: 数据加载、奖励函数、训练入口、评测
- evaluate: 人工和模型评估
- app.py: Flask API (upload/list/delete/parse)

## 主要接口
- GET /health
- POST /files/upload
- GET /files
- DELETE /files/{doc_id}
- POST /files/{doc_id}/parse

