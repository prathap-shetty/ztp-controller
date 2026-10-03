FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/ztp
COPY --chmod=644 requirements.lock ./
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --require-hashes -r requirements.lock \
    && useradd --create-home --uid 10001 ztp
COPY --chmod=755 app ./app
COPY --chmod=755 migrations ./migrations
COPY --chmod=644 alembic.ini ./
COPY --chmod=755 catalog ./catalog
COPY --chmod=755 templates ./templates
COPY --chmod=755 poap ./poap
USER ztp
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
