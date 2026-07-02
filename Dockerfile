FROM python:3.11-slim

# poppler-utils is required by pdf2image to rasterize PDF pages
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render provides $PORT at runtime; gunicorn binds to it.
# 1 worker / 120s timeout keeps memory use low and gives PDF rendering enough time
# on the free tier's shared CPU.
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 app:app
