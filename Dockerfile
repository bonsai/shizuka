FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV GCP_PROJECT=yok-ai-2026
ENV BQ_DATASET=model_status
ENV PORT=8080

EXPOSE 8080

CMD exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT}
