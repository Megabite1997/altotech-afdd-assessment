# Single image for every Python service. Which process it runs is decided by the
# compose command, so the API, simulator, ingestor and evaluator can never drift
# apart in dependencies or code version.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./
COPY afdd ./afdd
RUN pip install --no-cache-dir --no-deps -e .

RUN useradd --create-home --uid 10001 afdd && chown -R afdd /srv/app
USER afdd

EXPOSE 8000
CMD ["uvicorn", "afdd.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
