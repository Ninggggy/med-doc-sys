ARG PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.10-slim
FROM ${PYTHON_BASE_IMAGE}

ARG INSTALL_APT_RUNTIME=false
ARG APT_PRIMARY_MIRROR=https://deb.debian.org/debian
ARG APT_SECURITY_MIRROR=https://security.debian.org/debian-security
ARG APT_FALLBACK_PRIMARY_MIRROR=https://deb.debian.org/debian
ARG APT_FALLBACK_SECURITY_MIRROR=https://security.debian.org/debian-security
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_INDEX_URL=
ARG MODELSCOPE_CACHE=/app/agent/agent_backend/data/modelscope_cache
ARG MODELSCOPE_CACHE_SEED=/opt/modelscope_cache_seed
ARG EMBEDDING_LOCAL_MODEL_ID=iic/nlp_gte_sentence-embedding_chinese-large
ARG PRELOAD_MODELSCOPE_EMBED_MODEL=true
ARG ENABLE_CDE_SELENIUM_RUNTIME=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONPATH=/app \
    MODELSCOPE_CACHE=${MODELSCOPE_CACHE} \
    MODELSCOPE_CACHE_SEED=${MODELSCOPE_CACHE_SEED} \
    EMBEDDING_LOCAL_MODEL_ID=${EMBEDDING_LOCAL_MODEL_ID} \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.bak; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
      cp /etc/apt/sources.list /tmp/sources.list.bak; \
    fi; \
    rewrite_apt_sources() { \
      primary="$1"; \
      security="$2"; \
      if [ -f /tmp/debian.sources.bak ]; then \
        cp /tmp/debian.sources.bak /etc/apt/sources.list.d/debian.sources; \
      fi; \
      if [ -f /tmp/sources.list.bak ]; then \
        cp /tmp/sources.list.bak /etc/apt/sources.list; \
      fi; \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org/debian|${primary}|g; s|https://deb.debian.org/debian|${primary}|g; s|http://security.debian.org/debian-security|${security}|g; s|https://security.debian.org/debian-security|${security}|g" /etc/apt/sources.list.d/debian.sources; \
      fi; \
      if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org/debian|${primary}|g; s|https://deb.debian.org/debian|${primary}|g; s|http://security.debian.org/debian-security|${security}|g; s|https://security.debian.org/debian-security|${security}|g" /etc/apt/sources.list; \
      fi; \
    }; \
    apt_index_ready() { \
      apt-cache show libglib2.0-0 >/dev/null 2>&1; \
    }; \
    rewrite_apt_sources "${APT_PRIMARY_MIRROR}" "${APT_SECURITY_MIRROR}"; \
    printf 'Acquire::Retries "20";\nAcquire::ForceIPv4 "true";\nAcquire::https::Timeout "120";\nAcquire::http::Timeout "120";\n' > /etc/apt/apt.conf.d/99custom-retries; \
    apt-get update || true; \
    if ! apt_index_ready; then \
      echo "Primary APT mirror failed, fallback to official Debian mirrors"; \
      rewrite_apt_sources "${APT_FALLBACK_PRIMARY_MIRROR}" "${APT_FALLBACK_SECURITY_MIRROR}"; \
      apt-get update; \
    fi; \
    apt-get install -y --fix-missing --no-install-recommends ca-certificates; \
    if [ "${INSTALL_APT_RUNTIME}" = "true" ] || [ "${PRELOAD_MODELSCOPE_EMBED_MODEL}" = "true" ]; then \
      apt-get install -y --fix-missing --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
        libgl1 \
        libxcb1 \
        libx11-6 \
        libxext6 \
        libxrender1 \
        libsm6 \
        libice6; \
    fi; \
    if [ "${ENABLE_CDE_SELENIUM_RUNTIME}" = "true" ]; then \
      apt-get install -y --fix-missing --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libu2f-udev \
        libvulkan1 \
        xdg-utils; \
    fi; \
    rm -f /tmp/debian.sources.bak /tmp/sources.list.bak; \
    rm -f /etc/apt/apt.conf.d/99custom-retries; \
    rm -rf /var/lib/apt/lists/*

COPY agent_backend/env.yml /tmp/environment.yml
COPY deploy/export_env_pip_requirements.py /tmp/export_env_pip_requirements.py

RUN python -m pip install --upgrade pip wheel setuptools==70.3.0 && \
    python /tmp/export_env_pip_requirements.py /tmp/environment.yml /tmp/requirements.txt && \
    if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then \
      python -m pip install --timeout=600 --retries=20 --prefer-binary --no-compile \
        --index-url "${PIP_INDEX_URL}" \
        --extra-index-url "${PIP_EXTRA_INDEX_URL}" \
        -r /tmp/requirements.txt; \
    else \
      python -m pip install --timeout=600 --retries=20 --prefer-binary --no-compile \
        --index-url "${PIP_INDEX_URL}" \
        -r /tmp/requirements.txt; \
    fi && \
    python -m pip check && \
    rm -f /tmp/environment.yml /tmp/export_env_pip_requirements.py /tmp/requirements.txt

RUN set -eux; \
    mkdir -p "${MODELSCOPE_CACHE_SEED}"; \
    if [ "${PRELOAD_MODELSCOPE_EMBED_MODEL}" = "true" ]; then \
      python -c "import os; from modelscope import snapshot_download; print(snapshot_download(os.environ['EMBEDDING_LOCAL_MODEL_ID'], cache_dir=os.environ['MODELSCOPE_CACHE_SEED']))"; \
    fi

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
