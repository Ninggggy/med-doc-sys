ARG FRONTEND_BASE_IMAGE=deploy-agent-frontend:v12
FROM node:20-alpine AS build

WORKDIR /frontend

COPY agent_fronted/package*.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci

COPY agent_fronted/ ./
RUN node node_modules/@vue/cli-service/bin/vue-cli-service.js build

FROM ${FRONTEND_BASE_IMAGE}

# 基础镜像中含有上一版带 hash 的静态资源。Docker COPY 不会自动删除它们，
# 因此先清空固定的 Nginx 站点目录，防止旧 index 缓存继续加载旧代码。
RUN find /usr/share/nginx/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
COPY deploy/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html

ARG RELEASE_VERSION=development
ARG SOURCE_DIGEST=unknown
LABEL org.opencontainers.image.version="${RELEASE_VERSION}" \
      com.ai4med.delivery.role="agent-frontend" \
      com.ai4med.delivery.source-digest="${SOURCE_DIGEST}"

CMD ["nginx", "-g", "daemon off;"]
