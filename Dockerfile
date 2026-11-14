FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY flats ./flats
COPY research/house-passports ./research/house-passports
RUN pip install --no-cache-dir . gunicorn==23.0.0
RUN useradd --uid 10001 --create-home bereg
ENV FLATS_DB=/data/flats.db PYTHONUNBUFFERED=1
USER bereg
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "60", "flats.web:create_app()"]
