# Hosted deployment of ETAP Lens (Cloud Run or any container host).
#
# Serves the study-result half of the app: .SA1S/.SA2S/.LF1S/.UL1S/.TU1S are
# plain SQLite and read directly. Project models (.MDF/.BAK) are not supported
# here - they need SQL Server LocalDB, which is Windows-only.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ETAP_LENS_MODE=hosted

WORKDIR /app

COPY requirements-hosted.txt ./
RUN pip install --no-cache-dir -r requirements-hosted.txt

COPY app.py ./
COPY etap_reader/ ./etap_reader/
COPY web/ ./web/

# Cloud Run injects PORT; 8080 is its default and a sane fallback elsewhere.
ENV PORT=8080
EXPOSE 8080

# One worker on purpose. Load jobs are tracked in an in-memory dict, so a
# second worker would answer status polls for jobs it has never heard of.
# Concurrency comes from threads instead. Scaling out means moving that state
# somewhere shared first.
#
# --timeout 0 because deriving a year of hourly results takes ~25s, well past
# gunicorn's 30s default once the file is large; the platform enforces its own
# request deadline.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
