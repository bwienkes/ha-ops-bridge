FROM python:3.11-slim

ARG UID=65532
ARG GID=65532

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY entrypoint.sh ./

RUN chmod 0755 /app/entrypoint.sh
RUN useradd -u ${UID} -U -r bridge || exit 0
RUN mkdir -p /data && chown -R ${UID}:${GID} /data

USER ${UID}:${GID}

EXPOSE 8947/tcp

ENTRYPOINT ["/app/entrypoint.sh"]
