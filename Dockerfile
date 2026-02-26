FROM python:3.12-slim

# Install system dependencies including Chromium for browser automation + Node.js 20 for bgutil PO token server
RUN apt-get update && apt-get install -y \
    ffmpeg \
    ca-certificates \
    chromium \
    chromium-driver \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    curl \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Build bgutil PO token server (provides PO tokens for yt-dlp tv_embedded client)
# This resolves YouTube bot detection for datacenter IPs
RUN git clone --single-branch --branch 1.2.2 \
       https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server \
    && npm ci \
    && npx tsc

# Set Chromium path for nodriver
ENV CHROME_BIN=/usr/bin/chromium

# bgutil HTTP server port (yt-dlp plugin connects to this)
ENV BGUTIL_HTTP_API_PORT=4416

# Set working directory
WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create downloads directory
RUN mkdir -p /tmp/downloads

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/v1/health')"

# Start bgutil PO token server in background, then run the FastAPI app
CMD node /opt/bgutil/server/build/main.js & uvicorn app.main:app --host 0.0.0.0 --port 8000
