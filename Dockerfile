FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN DJANGO_SECRET_KEY=build-only PII_HMAC_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 app && chown -R app /app
# The release workflow passes the version and commit; they show up in the page footer.
ARG APP_VERSION=""
ARG APP_COMMIT=""
ENV APP_VERSION=$APP_VERSION APP_COMMIT=$APP_COMMIT

USER app

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --preload --worker-class gthread --threads ${GUNICORN_THREADS:-4} --access-logfile - --access-logformat '%({x-forwarded-for}i)s \"%(r)s\" %(s)s %(b)s %(L)ss'"]
