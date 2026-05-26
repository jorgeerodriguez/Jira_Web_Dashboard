FROM python:3.14-slim AS publish

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1 \
	STREAMLIT_SERVER_PORT=8080 \
	STREAMLIT_SERVER_ADDRESS=0.0.0.0

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
	python -m pip install -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
