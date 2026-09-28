ARG OCR_BASE_IMAGE=local/agent-ocr:linux-amd64
FROM ${OCR_BASE_IMAGE}

# 复用已验证的Tesseract及语言包，只更新服务代码；构建上下文为ocr_service。
WORKDIR /app/ocr
COPY app.py /app/ocr/app.py

ARG RELEASE_VERSION=development
LABEL org.opencontainers.image.version="${RELEASE_VERSION}" \
      com.ai4med.delivery.role="agent-ocr"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
