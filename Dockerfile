FROM python:3.12.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/src
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY web/requirements.txt web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt

COPY web/ web/
COPY src/ src/
COPY analysis/config/ analysis/config/
COPY analysis/etl/ analysis/etl/
COPY demo/ demo/
RUN python -m demo.local prepare

CMD ["python", "-m", "demo.container"]
