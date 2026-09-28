# 阶段三：两个混合填写日期记录框交接包

**状态：未完成；42项／26对象，完整完成0项。** 目标是可靠完成两个原对象，达到40项／24对象（无新增风险时）。本次提交只上传相关源码快照、证据及下一步执行prompt，不发布生产修复。

入口：

- [执行prompt](IMPLEMENTATION_PROMPT.md)：交给后续编码任务的完整指令。
- [原因分析](ANALYSIS.md)：事实、推断与未完成能力分开。
- [真实试验结果](RESULTS.md)：旧新候选、失败方法、最终API和成本边界。
- [全局经验](LESSONS.md)：历史安全反例及对象完成要求。
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

这是上传时本地未提交工作目录的**相关文件快照**，不是声称等于旧Git HEAD。GitHub本次提交记录这份快照。文件所在实际相对路径在source下保持，不覆盖仓库默认分支或生产配置；后续实现须追踪真实运行加载位置，不能只修改实验目录。

## 上传范围

公开仓库只携带任务相关记录框图像和必要文字证据；无完整PDF、模型缓存、运行环境配置、数据库、Cookie、访问令牌或连接串。包保留两个文档ID、问题ID、原目标ID和来源ID以便对应。绝对本机路径已替换为包内资源或明确的未导出引用。
