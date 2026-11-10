FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-production.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt
RUN mkdir -p /app/certs && curl -fsSL \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    -o /app/certs/global-bundle.pem
COPY backend ./backend
COPY scripts ./scripts
COPY docs ./docs
RUN useradd --uid 10001 --create-home app \
    && mkdir -p /app/staticfiles && chown app:app /app/staticfiles
USER app
WORKDIR /app/backend
ENV DJANGO_SETTINGS_MODULE=config.settings_production
EXPOSE 8000
CMD ["sh", "-c", "python manage.py collectstatic --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --worker-class gthread --threads 4 --timeout 120 --graceful-timeout 120 --access-logfile - --error-logfile -"]
