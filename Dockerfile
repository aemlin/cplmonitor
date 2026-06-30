FROM debian:12-slim AS pla-build

RUN apt-get update && apt-get install -y --no-install-recommends \
    git gprbuild gnat libpcap-dev ca-certificates make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone https://github.com/serock/pla-util.git

WORKDIR /src/pla-util
RUN gprbuild -p -P pla_util.gpr

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpcap0.8 iproute2 tcpdump ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pla-build /src/pla-util/pla/pla-util /usr/local/bin/pla-util

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor.py /app/

CMD ["python", "-u", "/app/monitor.py"]
