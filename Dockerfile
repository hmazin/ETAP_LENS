# Hosted deployment of ETAP Lens (Cloud Run or any container host).
#
# Built on SQL Server rather than a bare Python image, because reading an ETAP
# project model means attaching its .MDF to a SQL Server - and the Linux build
# attaches the files ETAP's Windows LocalDB writes without complaint. Study
# results (.SA1S/.SA2S/.LF1S/.UL1S/.TU1S) are plain SQLite and never touch it.
#
# The engine is Express: the edition licensed to run in production. The image
# defaults to Developer, which is dev/test only. Express caps a database at
# 10 GB, comfortably above any ETAP model seen so far.
FROM mcr.microsoft.com/mssql/server:2022-latest

# Package installation needs root; the engine refuses to run as root, so this
# drops back to the image's own user before starting anything.
USER root

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ETAP_LENS_MODE=hosted \
    ACCEPT_EULA=Y \
    MSSQL_PID=Express \
    PATH="/opt/mssql-tools18/bin:${PATH}"

# msodbcsql18 is already present (mssql-tools18 depends on it); unixodbc-dev
# and a compiler are only needed to build pyodbc, so they go in the same layer
# and come straight back out.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-dev unixodbc-dev gcc g++ \
 && pip3 install --no-cache-dir pyodbc \
 && apt-get purge -y --auto-remove python3-dev unixodbc-dev gcc g++ \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-hosted.txt ./
RUN pip3 install --no-cache-dir -r requirements-hosted.txt

COPY app.py ./
COPY etap_reader/ ./etap_reader/
COPY web/ ./web/
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh

# The app writes derived caches under /app/cache and hands the engine database
# files to attach, so both run as the same user and that user owns the tree.
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /app/cache \
 && chown -R 10001:0 /app \
 && chmod -R g+rwX /app

USER mssql

# Cloud Run injects PORT; 8080 is its default and a sane fallback elsewhere.
ENV PORT=8080
EXPOSE 8080

CMD ["/usr/local/bin/entrypoint.sh"]
