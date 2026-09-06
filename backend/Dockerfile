# Контекст сборки — корень репозитория: в образ нужны и backend/, и frontend/.
# Собирать так: docker build -f backend/Dockerfile .
FROM python:3.11-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FRONTEND_DIR=/srv/frontend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN python -m playwright install --with-deps --only-shell chromium \
    && apt-get install -y --no-install-recommends fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ /srv/
COPY frontend/ /srv/frontend/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
