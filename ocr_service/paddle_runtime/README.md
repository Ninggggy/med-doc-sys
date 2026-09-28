# 备案解析运行模块

版本：`2026.09.24.1`。这是产品实际执行实现，不是外部目录接入壳。

来源：2026-09-24 从已运行测试进程的 `FILING_PADDLE_CODE` 指向的
`/Users/ning/.cache/med-doc-review-product/release/agent/prototype/ocr_local_trial/`
复制其 16 个 Python 调用模块，再在本目录修改。旧目录保持原状。
本轮完整模块纳入 Git 索引供审查，遵照用户要求不创建提交或推送。

入口：`filing_paddle_backend.LocalPaddle → model_bridge.py → model_host.py → experimental_parser.py → pipeline.py`。
`adapters.py` 显式保留 `PP-OCRv6_medium_det/rec` 权重，`imaging.py` 复用现有网格，
`content_recovery.py/text_roles.py` 保留格内和叠印证据；`product_consumer.py` 做产品投影。
`layout.py` 只创建官方 `PP-DocLayoutV3`，不启动 PP-Structure 默认公式、图表或 Markdown 过滤。
`assembly.py` 是产品解析、主动修订、诊断修订共用的无模型组装器。

本地验证版本：PaddleOCR 3.7.0、PaddleX 3.7.2、PaddlePaddle 3.3.1、NumPy 2.3.5、
OpenCV-contrib 4.10.0.84、Pillow 12.3.0、PyMuPDF 1.28.2、psutil 7.2.2。
这是 macOS CPU 实测环境记录，不代表 Linux AMD64 交付验证，也不要求改现有生产依赖。

`FILING_PADDLE_CODE` 未设置时使用本目录；显式设置时沿用该路径，并在 `config.effective.json` 记录路径与版本。
`FILING_LAYOUT_MODEL_DIR` 指定已下载的官方版面模型目录；不设置时查找
`FILING_PADDLE_PRIVATE/models/official_models/PP-DocLayoutV3`。
启动生成 `runtime-source.json`，记录真实运行目录、包版本和模型名。仓库不存模型权重。
本轮未修改任何既有服务环境或生产配置。

官方说明：<https://paddlepaddle.github.io/PaddleX/latest/en/module_usage/tutorials/ocr_modules/layout_analysis.html>。
官方权重：<https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocLayoutV3_infer.tar>。

重放命令见 `test/replay_layout_product.py --help`。输入是旧运行目录、已补推理的版面目录和原运行模块目录；
旧原始输出只读，证据写入独立 `--output`。`layout.py --help` 提供仅版面推理入口。
