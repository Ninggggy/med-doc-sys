FROM node:20-alpine AS build

WORKDIR /frontend

COPY agent_fronted/package*.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci

COPY agent_fronted/ ./
RUN node node_modules/@vue/cli-service/bin/vue-cli-service.js build

FROM nginx:1.27-alpine

ARG RELEASE_VERSION=development
ARG SOURCE_DIGEST=unknown
LABEL org.opencontainers.image.version="${RELEASE_VERSION}" \
      com.ai4med.delivery.role="agent-frontend" \
      com.ai4med.delivery.source-digest="${SOURCE_DIGEST}"

COPY deploy/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
