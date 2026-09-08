FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    sollya=8.0+ds-2build3 && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["sollya", "--flush", "--warnonstderr"]
