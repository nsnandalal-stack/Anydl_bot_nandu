FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg yt-dlp curl
COPY . .
RUN pip install -r requirements.txt
ENTRYPOINT ["python", "main.py"]
