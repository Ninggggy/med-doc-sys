FROM docker.m.daocloud.io/library/node:16-alpine AS build

WORKDIR /frontend

COPY agent_fronted/package*.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci

COPY agent_fronted/ ./
RUN npm run build

FROM docker.m.daocloud.io/nginx:1.27-alpine

ARG RELEASE_VERSION=development
ARG SOURCE_DIGEST=unknown
LABEL org.opencontainers.image.version="${RELEASE_VERSION}" \
      com.ai4med.delivery.role="agent-frontend" \
      com.ai4med.delivery.source-digest="${SOURCE_DIGEST}"

COPY deploy/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
