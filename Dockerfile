# «Онлайн-деканат»: OCR + API + веб-интерфейс одной командой, никаких внешних API.
#
#   docker build -t umir-dean .
#   docker run --rm -p 8000:8000 umir-dean                 # интерфейс: http://localhost:8000
#
# Пакетный прогон (results.json) по архиву кейса — архив монтируем в /data:
#   docker run --rm -v "/путь/к/архиву:/data" umir-dean \
#     python -m backend.scripts.run_batch --documents /data/documents.json \
#       --questions /data/questions.json --root /data --output /data/results.json

# --- 1. собираем интерфейс --------------------------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# --- 2. backend + локальный OCR ----------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_THREAD_LIMIT=1 \
    UMIR_HOST=0.0.0.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY data/ data/
COPY pytest.ini README.md rules.md ./
# пример четвёртого типа документа (UMIR_DOCUMENT_TYPES / UMIR_RULES, подробнее в README)
COPY examples/ examples/
COPY --from=web /web/dist frontend/dist
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
