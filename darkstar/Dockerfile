# darkstar image — built by the `publish:darkstar` CI job (PROJECT_SUB_DIR=darkstar).
# The kaniko build context is the repo root, so COPY paths are repo-relative.
FROM python:3.13-slim AS publish

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

COPY darkstar/requirements.txt ./
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY darkstar/ ./darkstar/

EXPOSE 8080

CMD ["uvicorn", "darkstar.app:app", "--host", "0.0.0.0", "--port", "8080"]

# release target: re-base on the already-published dev image so the semver build re-stamps it
# (mirrors the a2 publish→release flow; APP_IMAGE is supplied by the .create_semver CI job).
ARG APP_IMAGE
FROM ${APP_IMAGE} AS release
