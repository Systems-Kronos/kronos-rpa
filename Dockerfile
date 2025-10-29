FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/local/bin/chromedriver

WORKDIR /app
COPY requirements.txt .

RUN apt-get update && apt-get install -y \
    chromium chromium-driver \
    wget unzip fonts-liberation libnss3 libx11-xcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxi6 libxtst6 libxrandr2 libasound2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 \
    libdrm2 libxss1 libgbm1 --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
EXPOSE 8080
CMD ["uvicorn", "WEB-Raspagem.rpaNoticias:app", "--host", "0.0.0.0", "--port", "8080"]
