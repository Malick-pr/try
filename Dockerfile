FROM python:3.11-slim

# Installer Chrome et dépendances
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Installer chromedriver
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d'.' -f1) \
    && wget -q "https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/${CHROME_VERSION}.0.6778.87/linux64/chromedriver-linux64.zip" -O /tmp/chromedriver.zip || \
    wget -q "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_${CHROME_VERSION}" -O /tmp/version && \
    wget -q "https://chromedriver.storage.googleapis.com/$(cat /tmp/version)/chromedriver_linux64.zip" -O /tmp/chromedriver.zip || true \
    && unzip -q /tmp/chromedriver.zip -d /tmp/ || true \
    && mv /tmp/chromedriver*/chromedriver /usr/local/bin/ 2>/dev/null || mv /tmp/chromedriver /usr/local/bin/ 2>/dev/null || true \
    && chmod +x /usr/local/bin/chromedriver 2>/dev/null || true \
    && rm -rf /tmp/*

WORKDIR /app

# Copier requirements et installer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier l'application
COPY . .

# Variables d'environnement
ENV CHROME_BIN=/usr/bin/google-chrome
ENV CHROMEDRIVER_PATH=/usr/local/bin/chromedriver

# Port
EXPOSE 5000

# Démarrer
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120"]
