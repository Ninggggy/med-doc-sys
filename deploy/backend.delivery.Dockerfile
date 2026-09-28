ARG BACKEND_BASE_IMAGE=deploy-agent-backend:v12
FROM ${BACKEND_BASE_IMAGE}

WORKDIR /app

# 增量交付镜像复用上一个已验证的 Linux AMD64 运行时，
# 但必须先移除基础镜像的旧源码，再 COPY 当前完整后端源码。
# 否则上一版已删除的 Python 模块仍会残留在新镜像中。
RUN rm -rf /app/agent/agent_backend
COPY __init__.py /app/agent/__init__.py
COPY agent_backend /app/agent/agent_backend
COPY prototype/ocr_local_trial /app/agent/prototype/ocr_local_trial
COPY model_weights /opt/private/models/official_models
COPY task/change_review/审评规则_变更有效期和贮藏条件_技术审评要点版.json /app/agent/task/change_review/审评规则_变更有效期和贮藏条件_技术审评要点版.json
COPY deploy/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod 0755 /usr/local/bin/backend-entrypoint.sh

# The model host is a single long-lived process. Its private Python environment
# keeps the existing backend runtime unchanged while supplying local Paddle OCR.
RUN python -m venv --system-site-packages /opt/private/venv \
    && /opt/private/venv/bin/pip install --no-cache-dir \
       paddlepaddle==3.3.1 paddleocr==3.7.0 paddlex==3.7.2 PyMuPDF==1.28.2
ENV FILING_PADDLE_CODE=/app/agent/prototype/ocr_local_trial \
    FILING_PADDLE_PRIVATE=/opt/private \
    FILING_PADDLE_RUNTIME=/app/agent/agent_backend/data/model-runs \
    HF_HUB_OFFLINE=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

ARG RELEASE_VERSION=development
ARG SOURCE_DIGEST=unknown
LABEL org.opencontainers.image.version="${RELEASE_VERSION}" \
      com.ai4med.delivery.role="agent-backend" \
      com.ai4med.delivery.source-digest="${SOURCE_DIGEST}"

CMD ["/usr/local/bin/backend-entrypoint.sh"]
