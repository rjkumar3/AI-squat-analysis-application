FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# CPU-only torch keeps the image ~1.5 GB instead of ~6 GB
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip uninstall -y opencv-python 2>/dev/null; pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless
RUN mkdir -p models && curl -fsSL -o models/yolo11s-pose.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11s-pose.pt
COPY app app
COPY skill skill
COPY web web
ENV PORT=8000 DATA_DIR=/data YOLO_CONFIG_DIR=/tmp/ultralytics
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
