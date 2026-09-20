# Agent 项目快速启动指南

这是一个用于药品审评/CTD 预审的前后端分离项目。

## 1. 克隆项目

```bash
git clone <你的仓库地址>
cd agent
```

## 2. 推荐方式：Docker 一键启动（最省事）

### 前置要求

- 已安装 Docker Desktop（或 Docker Engine + Docker Compose）
- 本机可用端口：`8081`（前端）、`5002`（后端）、`3306`（MySQL）

### 启动

```bash
docker compose -f deploy/docker-compose.yaml up --build
```

首次启动会自动构建镜像、安装容器内依赖并拉起以下服务：MySQL、OCR、Milvus、后端、前端等。

### 访问

- 前端：http://localhost:8081
- 后端：http://localhost:5002

## 3. 本地开发方式（可选）

### 3.1 安装后端依赖

```bash
pip install -r agent_backend/requirements.txt
```

### 3.2 启动后端

```bash
python agent_backend/app.py
```

### 3.3 安装并启动前端

如果未安装 Node.js，可按你的系统选择一种方式安装：

- Windows（推荐）：
```bash
winget install OpenJS.NodeJS.LTS
```
- macOS（Homebrew）：
```bash
brew install node
```
- Ubuntu/Debian：
```bash
sudo apt update
sudo apt install -y nodejs npm
```

安装完 Node.js 后（会自带 npm），再安装 Yarn：

```bash
npm install -g yarn
```

确认可用：

```bash
node -v
npm -v
yarn -v
```

```bash
cd agent_fronted
yarn install
yarn serve
```

前端默认会通过 `vue.config.js` 将 `/api` 代理到后端。

## 4. 常见问题

- 前端目录名是 `agent_fronted`（项目原始命名），不要改名。
- 如果端口冲突，请先停止占用进程，或在 `deploy/docker-compose.yaml` 中调整端口映射。
- 首次启动较慢属于正常现象（镜像构建与模型/依赖初始化耗时较长）。
