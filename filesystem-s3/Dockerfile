ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/filesystem-s3
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY fs3 ./fs3
RUN pip install --no-cache-dir --no-deps . && mkdir -p /state && chown 10001:10001 /state
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["uvicorn", "fs3.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
