# duberr: busca el audio en castellano y lo mezcla, sin recodificar, con la mejor imagen disponible.
# Imagen autocontenida: lleva el código dentro (COPY), no necesita montar nada más que los datos y la biblioteca.
FROM alpine:3.22

# Dependencias del sistema: python3 + numpy (medición), ffmpeg/ffprobe (audio y vista previa),
# mkvtoolnix (mkvmerge: mezcla y lectura de pistas), tzdata (hora local en el registro),
# ca-certificates (HTTPS hacia Sonarr/Radarr/ntfy si los pones detrás de TLS).
RUN apk add --no-cache python3 py3-numpy ffmpeg mkvtoolnix tzdata ca-certificates

# Usuario sin privilegios (uid/gid 1000). Con `user:` en docker-compose puedes usar el tuyo (el de tus *arr).
RUN adduser -D -u 1000 duberr \
 && mkdir -p /duberr /data/media \
 && chown duberr:duberr /duberr

ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY *.py /app/

USER duberr
EXPOSE 3005
VOLUME ["/duberr"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python3", "/app/healthcheck.py"]

CMD ["python3", "/app/duberr.py"]
