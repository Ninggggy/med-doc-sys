# Docker 三文件交付包检查清单

本清单适用于以下三个交付文件：

- `agent-backend-vN.tar`
- `agent-frontend-vN.tar`
- `docker-compose.yaml`

任何一项没有通过，都不得把交付包发给测试人员，也不得只凭“构建成功”或“开发机能运行”宣称交付包可用。

## 已发生过的问题及根因

### 1. OCR 镜像拉取失败

现象是 Docker 尝试拉取 `ocr-service-tesseract-ocr`，随后报 `pull access denied`。该名称只是开发机 Compose 构建时生成的本地镜像名，公共仓库中不存在，交付 tar 中也没有同名 RepoTag。

长期规则：Compose 中的每个镜像名必须与交付 tar 的 `manifest.json` 中 RepoTag 逐字一致，或者被明确列为目标机已安装的基础镜像。OCR 随后端 tar 交付时固定使用 `local/agent-ocr:linux-amd64`，并必须验证该标签确实存在于后端 tar。仅修正镜像名或封装方式时，要明确告诉测试人员“OCR 功能没有更换”。

### 2. 后端入口脚本无执行权限

现象是容器在业务代码运行前报 `/usr/local/bin/backend-entrypoint.sh: permission denied`。v11 最终镜像里的脚本模式实际为 `0666`，直接执行必然失败。

长期规则：Dockerfile 必须用 `COPY --chmod=0755` 或 `RUN chmod 0755` 固化权限；Compose 同时使用 `/bin/sh /usr/local/bin/backend-entrypoint.sh` 作为防御。必须检查“最终导出的 tar 再次 `docker load` 后”的脚本权限，并按默认启动方式真正启动容器，不能只检查宿主机源码或导出前镜像。

### 3. MySQL 镜像来源被擅自改变

测试环境原来使用 `local/agent-mysql:latest`，交付 Compose 却改成远程镜像，导致测试人员需要猜测该用哪个镜像，也可能触发无谓下载或改变数据库运行环境。

长期规则：增量升级必须保留目标环境现有的基础服务镜像标签、主版本和数据卷，不得擅自切换本地镜像、镜像仓库或数据库版本。新的全量包禁止使用 `latest`；历史环境为保持兼容必须沿用 `latest` 时，只能作为书面声明的例外，同时记录该环境中实际镜像 ID 或 digest。

### 4. 源码修复没有进入交付镜像

v12 前端镜像与 v11 的 image ID 完全相同，最终 JavaScript 中也搜不到已完成的章节加载修复，说明只修改了工作区或只给旧镜像换了新标签。

长期规则：禁止将旧镜像仅 retag 成新版本。每次构建必须向前后端镜像写入发布版本、镜像角色和实际源码 SHA-256，自动门禁从最终 tar 的 image config 中核对。前端必须再检查最终编译资产确实包含本次修复的特征文本；如果新旧 image ID 完全相同，立即停止交付。

### 5. 增量前端镜像残留旧静态资源

如果新镜像以上一版前端镜像为基础，直接 `COPY dist` 不会删除旧的 `app.<hash>.js/css`。浏览器一旦缓存旧 `index.html`，仍可继续运行旧缺陷代码，或在旧文件被清理后直接白屏。

长期规则：增量前端 Dockerfile 在 `COPY` 前必须清空固定的 `/usr/share/nginx/html`；`index.html` 必须返回 `Cache-Control: no-store, no-cache, must-revalidate`。最终 tar 中同一类 `app/chunk-vendors` 只允许一个 hash 代际。

### 6. 调试重载和容器内端口可被 `.env` 意外改写

交付 Compose 曾默认 `DEBUG=true`，会启用 Flask reloader。对挂载目录的变化进行重载时，运行中的内存任务可被中断。同时，`APP_PORT` 如被改为非 5002，会与 Nginx、健康检查及容器端口契约不一致。

长期规则：交付 Compose 固定 `DEBUG=false`、`APP_HOST=0.0.0.0`、`APP_PORT=5002`，仅允许用 `BACKEND_PORT` 调整宿主机暴露端口；前端应等待后端 `service_healthy` 再启动。

## 第一步：先固定交付类型

构建上下文注意事项（v17候选验证经验）：在macOS上手工将tar上下文传入构建器时，使用`COPYFILE_DISABLE=1 tar --format=ustar ...`，避免生成`._` AppleDouble附带文件。从最终镜像取出业务源码逐文件核对，不把宿主机元数据当作业务文件交付。大运行时镜像优先使用可用的BuildKit；不能因为旧构建器慢而省去重新构建、导出、加载或校验。

每次生成文件前必须明确选择一种模式：

1. **增量升级**：复用测试方现有 `.env`、密钥、数据目录、数据卷和基础服务镜像，只替换本次变化的镜像。Compose 不得无故改依赖标签。
2. **全量离线**：目标机无需联网。只加载交付 tar 后，Compose 引用的每一个镜像都必须已经存在；依赖镜像可合并到后端 tar，但 RepoTag 必须完全一致。
3. **全量在线**：明确列出需要联网拉取的每个镜像、仓库及固定版本，并在与测试方相同的网络条件下验证可以拉取。

如果三个文件仍依赖目标机已有镜像、`.env`、密钥或数据目录，它就是增量升级包，不能描述成“全新机器仅靠三个文件即可运行”。

## 发布前一票否决检查

- [ ] 三个文件均存在、可完整读取，且版本号一致。
- [ ] Compose 中后端和前端标签分别与两个 tar 内 RepoTag 完全一致，没有混用 `vN` 和 `vN-1`。
- [ ] `docker compose config --images` 的每个镜像都注明来源：位于哪个 tar、目标机预装，或在线拉取。
- [ ] 不存在来源不明的镜像，也不把开发机缓存误认为已经交付。
- [ ] 新全量包不使用 `latest`，不使用开发机构建自动生成的镜像名，不使用未经验证的私有仓库。
- [ ] 交付 Compose 不包含 `build:`；测试人员没有源码时不得触发本地构建。
- [ ] tar 中全部交付镜像均为 `linux/amd64`。
- [ ] tar 内前后端镜像的内部版本、角色和源码 SHA-256 标签均正确，不是旧镜像 retag。
- [ ] 使用 `deploy/source_digest.py` 计算构建摘要，门禁已将最终镜像标签与当前冻结源码精确比较，不是只检查 64 位格式。
- [ ] 前端最终静态资产中能检索到本次修复的特征文本，且新旧版本 image ID 不同。
- [ ] 前端最终镜像只有一代 `app/chunk-vendors` 带 hash 资源，`index.html` 实际响应包含 `no-store/no-cache`。
- [ ] 交付 Compose 固定 `DEBUG=false`、`APP_HOST=0.0.0.0`、`APP_PORT=5002`，前端依赖后端 `service_healthy`。
- [ ] 后端最终镜像内入口脚本可读、可执行、shebang 正确，Compose 保留 `/bin/sh` 防御入口。
- [ ] 对最终 tar 执行过 `docker load`，而非只验证构建缓存中的镜像。
- [ ] 在干净的 Linux AMD64 Docker 环境实际执行 Compose；全量离线包必须使用 `--pull never`，证明没有隐藏拉取。
- [ ] `docker compose ps` 中 MySQL、OCR、后端健康，前端和 Milvus 正常运行，没有长期停留在 `Created`、`Starting` 或 `Restarting`。
- [ ] 已完成最小端到端冒烟：前端可访问、后端可访问、OCR `/health` 成功、后端可连接 MySQL/Milvus/OCR。
- [ ] `.env`、密钥、初始化 SQL、bind mount 目录等非三文件条件均已逐项说明。
- [ ] 最终定稿后才计算 SHA-256；计算后没有再修改 Compose 或重新打标签。
- [ ] 已运行仓库内自动校验脚本且退出码为 0。

静态校验示例（现有测试环境增量升级）：

```bash
# 先输出构建时必须传入两个 Dockerfile 的 SOURCE_DIGEST。
python3 deploy/source_digest.py

python3 deploy/validate_delivery_bundle.py ../agent-v12 \
  --version v12 \
  --mode upgrade \
  --external-image local/agent-mysql:latest \
  --external-image local/agent-etcd:v3.5.5 \
  --external-image minio/minio:RELEASE.2023-03-20T20-16-18Z \
  --external-image local/agent-milvus:v2.6.11 \
  --allow-legacy-latest local/agent-mysql:latest
```

## 每次交付必须附带的说明

- 交付类型：增量、全量离线或全量在线。
- 两个 tar 各自包含的精确镜像标签。
- Compose 中没有随 tar 提供的镜像清单及其来源。
- MySQL 是复用还是更换、精确版本是什么、数据卷是否保留。
- OCR 功能是否更换；如果只是修正标签或随包封装，要明确写“功能未更换”。
- 必需的 `.env`、密钥和目录。
- 三个文件的大小和 SHA-256。
- 最终 tar 的验证环境、架构和冒烟结果。

## 测试人员操作保护

升级时不得删除 `.env`、密钥、业务数据目录或数据卷，不得要求测试人员执行 `docker compose down -v`。若目标机只有旧版 `docker-compose`，交付方必须给出并验证对应的 v1 命令，不能让测试人员自行猜测参数。
