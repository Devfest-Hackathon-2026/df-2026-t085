FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY unsaid_ai.py stt.py main.py clinical.py ./
COPY prompts/ prompts/
COPY schemas/ schemas/
# Cloud Run が PORT を渡す（既定 8080）
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
