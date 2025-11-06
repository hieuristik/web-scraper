FROM python:3.11-slim

# Make apt non-interactive
ENV DEBIAN_FRONTEND=noninteractive

# install system deps needed for headless Chromium + fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    gnupg \
    unzip \
    xvfb \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-cjk \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libgtk-3-0 \
    libxrender1 \
    libdbus-glib-1-2 \
    libgl1 \
    libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

# Install Chromium browser (Debian package name may vary across distros)
RUN apt-get update && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Use pip no-cache for smaller layers
RUN pip install --no-cache-dir -r requirements.txt

# Ensure debug/processed dirs exist and are writable
RUN mkdir -p /app/data/processed /app/data/debug \
    && chmod -R a+rw /app/data

# Default CHROME_PATH; tests/configs may override via env
ENV CHROME_PATH=/usr/bin/chromium

# Demo tuning defaults (can be overridden at runtime)
ENV DEMO_MAX_CPP=5.0
ENV DEMO_CARRIER=""

# Entrypoint runs the CLI by default; callers (docker run) can override entrypoint/command
ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]