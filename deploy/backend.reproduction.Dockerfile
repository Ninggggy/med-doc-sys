ARG PYTHON_BASE_IMAGE=python:3.10-slim
FROM ${PYTHON_BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONPATH=/app \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

# 覆盖后端实际已实现的 PDF/图像、CDE Selenium 和旧 .doc 转换链路。
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      chromium \
      chromium-driver \
      fonts-liberation \
      fonts-noto-cjk \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libcairo2 \
      libcups2 \
      libdbus-1-3 \
      libgbm1 \
      libglib2.0-0 \
      libgl1 \
      libgomp1 \
      libgtk-3-0 \
      libice6 \
      libnspr4 \
      libnss3 \
      libsm6 \
      libu2f-udev \
      libvulkan1 \
      libx11-6 \
      libxcb1 \
      libxext6 \
      libxrender1 \
      libreoffice-writer \
      xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# env.yml 含 Linux x86 CUDA 包，无法在 Apple Silicon 原生构建。
# requirements_linux.txt 是仓库提供的跨机器 Linux 运行依赖清单。
ARG PIP_INSTALL_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COPY agent_backend/requirements_linux.txt /tmp/requirements.txt
COPY deploy/requirements.reproduction.constraints.txt /tmp/constraints.txt
RUN python -m pip install --upgrade pip wheel setuptools==70.3.0 \
    && python -m pip install --timeout=600 --retries=20 --prefer-binary \
         --index-url "${PIP_INSTALL_INDEX_URL}" -c /tmp/constraints.txt -r /tmp/requirements.txt \
    && python -m pip check \
    && rm -f /tmp/requirements.txt /tmp/constraints.txt

# requirements_linux.txt 漏列、但源码顶层直接导入且 env.yml 有版本记录的依赖。
RUN python -m pip install --index-url "${PIP_INSTALL_INDEX_URL}" \
      cachetools==5.5.0 \
      ruamel.yaml==0.18.6 \
      simplejson==3.20.1 \
      strenum==0.4.15 \
      valkey==6.0.2 \
    && python -m pip check

RUN mkdir -p /app/agent
COPY __init__.py /app/agent/__init__.py
COPY agent_backend /app/agent/agent_backend
COPY deploy/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh

ARG RELEASE_VERSION=development
ARG SOURCE_DIGEST=unknown
LABEL org.opencontainers.image.version="${RELEASE_VERSION}" \
      com.ai4med.delivery.role="agent-backend" \
      com.ai4med.delivery.source-digest="${SOURCE_DIGEST}"

EXPOSE 5002 8024
CMD ["/usr/local/bin/backend-entrypoint.sh"]
