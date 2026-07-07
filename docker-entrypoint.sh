#!/bin/sh
# This image bundles two apps (see Dockerfile): pe-reports (Streamlit, the default) and darkstar
# (FastAPI v2, installed in /opt/darkstar-venv). darkstar's workload sets APP_ENTRYPOINT=darkstar
# to run the FastAPI app; anything else runs Streamlit exactly as the standalone image did.
set -e
if [ "$APP_ENTRYPOINT" = "darkstar" ]; then
	exec /opt/darkstar-venv/bin/uvicorn darkstar.app:app --host 0.0.0.0 --port 8080
fi
exec streamlit run app.py --server.port=8080 --server.address=0.0.0.0
