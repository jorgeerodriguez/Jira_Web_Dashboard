FROM python:3.14-slim AS publish

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1 \
	STREAMLIT_SERVER_PORT=8080 \
	STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
	HOME=/tmp \
	MPLCONFIGDIR=/tmp

# pe-reports (Streamlit) — system site-packages, unchanged from the standalone image so the
# Streamlit runtime is byte-for-byte what it was before darkstar shared the image.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
	python -m pip install -r requirements.txt

# darkstar (FastAPI v2) rides the same image in an isolated virtualenv. The two dependency sets
# can't be merged (pe-reports pins starlette==1.0.0 while darkstar's fastapi needs starlette<0.42),
# so the venv keeps them from colliding while pe-reports' environment stays untouched.
COPY darkstar/requirements.txt ./darkstar-requirements.txt
RUN python -m venv /opt/darkstar-venv && \
	/opt/darkstar-venv/bin/pip install --upgrade pip && \
	/opt/darkstar-venv/bin/pip install -r darkstar-requirements.txt

COPY . .
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8080

# One image, two apps. The entrypoint runs Streamlit by default — the a2 chart launches the
# image's default command, so pe-reports is unaffected — and uvicorn/darkstar when
# APP_ENTRYPOINT=darkstar. darkstar deploys as a StatefulSet, whose a2 template has no command
# override, so the process is chosen by that env var (set in darkstar's HelmRelease envVars).
CMD ["/app/docker-entrypoint.sh"]
