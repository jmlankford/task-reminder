FROM python:3.12-slim

# Set timezone
ENV TZ=America/New_York
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data directory — will be overridden by the volume mount at runtime
RUN mkdir -p /data

ENV DATABASE_PATH=/data/taskreminder.db
ENV FLASK_ENV=production

EXPOSE 5000

# Single worker to keep APScheduler to one instance
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "run:app"]
