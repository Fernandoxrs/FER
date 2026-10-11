FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates templates
COPY static static

ENV PORTAL_DATA_DIR=/datos \
    PORTAL_HOST=0.0.0.0 \
    PORTAL_PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME /datos
EXPOSE 8000

CMD ["python", "app.py"]
