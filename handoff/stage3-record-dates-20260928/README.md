# 阶段三：两个混合填写日期记录框交接包

**状态：未完成；42项／26对象，完整完成0项。** 目标是可靠完成两个原对象，达到40项／24对象（无新增风险时）。本次提交上传现有系统相关完整源码、证据及独立专家审查prompt，不发布生产修复。

入口：

- [独立专家分析 Prompt](EXPERT_REVIEW_PROMPT.md)：要求其他代码专家自行核查原因并提出修改方案，不预设结论。
- [真实试验结果](RESULTS.md)：旧新候选、失败方法、最终API和成本边界。
- [全局经验](LESSONS.md)：历史工作记录，供专家核实，不作为预设根因。
- `source/ocr_service/paddle_runtime/`：从当前工作目录复制的运行模块，包括model_bridge、bounded_review、cell_adjudication、sequence_adjudication和product_consumer及其本地依赖。
- `source/agent_backend/services/`：目标编辑、修订和outcome消费参考。它们不是独立可启动后端，其他应用依赖沿用本机项目。
- `evidence/doc2-record.json.gz`、`doc3-record.json.gz`：两个原记录框的原始/候选结果、最终API单元格及目标问题。
- `evidence/assets/doc2-record-native.png`、`doc3-record-native.png`：仅相关记录框，从原PDF可见合成渲染，保留到原页的变换。未导出完整申报PDF或其他页面。
- `evidence/final-complete-score.json.gz`：日值最后一次完整竞争评分。
- `historical-experiments/`：脱去本机路径的历史试验资料，不能直接运行或当生产代码。

## 离线复核

环境已有Python、numpy、Pillow时：

```sh
python verify_evidence.py
```

脚本只读取包内原色图像、已存CTC评分并调用源码快照重算日值裁决，不启动模型、不联网、不写API。输出应仍未通过，且两个原问题身份保留。该检查不是产品验收、正常上传或13页OCR重跑。没有提供模型权重、安装环境或秘密配置。

本包内JSON图像路径相对于 `evidence/`；`[LOCAL_ONLY]`表示有意不导出的原页/配置/其他本机资源，不能当有效文件。不要执行历史脚本中的占位路径。原页PDF只保留相关记录框可见合成像素，无法从本包还原完整PDF/全部13页。

## 源码与仓库版本

本分支的 `agent_backend/`、`agent_fronted/`、`ocr_service/`、`deploy/` 和根目录回归测试已同步本地当前源文件，具体范围见 `source-sync-inventory.json`。不是声称等于旧Git HEAD。包内 `source/` 仅保留相关运行模块的交接快照；专家应优先检查仓库实际源码路径，不能只修改实验目录。默认分支和生产配置未改变。

## 上传范围

公开仓库只携带任务相关记录框图像和必要文字证据；无完整PDF、模型缓存、运行环境配置、数据库、Cookie、访问令牌或连接串。包保留两个文档ID、问题ID、原目标ID和来源ID以便对应。绝对本机路径已替换为包内资源或明确的未导出引用。

同步共522个源文件/测试/资产。`deploy/docker-compose-1.yaml` 的上传副本将明文连接凭据替换为环境变量；本机文件未改。源码一致性与语法检查见 `upload-validation.json`。这不是业务回归或阶段三完成证明。
