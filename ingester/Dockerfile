FROM python:3.11-slim

WORKDIR /app

COPY ingester/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared

COPY ingester/app.py .
COPY ingester/db.py .
COPY ingester/media.py .
COPY ingester/reddit.py .
COPY ingester/targets.py .
COPY ingester/requeue_gifs.py ./

ENV PYTHONPATH=/app

CMD ["python3", "app.py"]