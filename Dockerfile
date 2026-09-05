FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OAF_DATA_DIR=/var/lib/openautopsyflow
RUN groupadd --gid 10001 oaf && useradd --uid 10001 --gid oaf --create-home oaf \
    && mkdir -p /var/lib/openautopsyflow && chown oaf:oaf /var/lib/openautopsyflow
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY openautopsyflow ./openautopsyflow
RUN python -m pip install --no-cache-dir --no-deps .
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["python", "-m", "uvicorn", "openautopsyflow.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
