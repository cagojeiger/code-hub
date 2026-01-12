FROM alpine:3.19

RUN apk add --no-cache \
    zstd \
    rsync \
    aws-cli \
    coreutils

COPY archive.sh /usr/local/bin/archive
COPY restore.sh /usr/local/bin/restore
RUN chmod +x /usr/local/bin/*

ENTRYPOINT ["/bin/sh"]
