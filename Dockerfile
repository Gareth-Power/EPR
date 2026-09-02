# EPR - code only. All patient data lives in the /data volume, never the image.
FROM python:3.12-slim

# tini = correct signal handling; gosu = optional drop to a host UID (see entrypoint)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code only. .dockerignore keeps data/, Patients/, epr.db, legacy/ etc. out of the build.
COPY db.py server.py ./
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV EPR_DATA_DIR=/data \
    EPR_HOST=0.0.0.0 \
    EPR_PORT=8080
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/meta', timeout=2).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["python", "server.py"]
