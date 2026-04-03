FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg
WORKDIR /app
RUN pip install requests psycopg2-binary redis yt-dlp
COPY app.py .
CMD ["python","app.py"]
