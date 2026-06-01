FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watch_and_process.py .
COPY report_generator.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "watch_and_process.py"]