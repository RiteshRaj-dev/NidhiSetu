FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV NIDHISETU_DATA=/app/data
EXPOSE 8000
ENV PORT=8000
CMD ["sh", "-c", "python scripts/seed_demo.py 48 || true; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
