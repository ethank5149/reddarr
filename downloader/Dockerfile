FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg
WORKDIR /app
COPY downloader/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY downloader/app.py .
COPY downloader/db.py .
COPY downloader/media_utils.py .
COPY downloader/providers.py .
ENV PYTHONPATH=/app
CMD ["python","app.py"]