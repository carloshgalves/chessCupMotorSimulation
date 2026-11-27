FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y git make g++ ca-certificates python3 python3-venv stockfish=17-1build1 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
RUN git clone https://github.com/nescitus/rodent-iv.git /tmp/rodent-source && cd /tmp/rodent-source && git checkout e8d84c8c8c189a1cf4eb27c578fc573af3b916d2 && mkdir -p mac /opt/rodent && make -C sources build && cp mac/rodentIV /opt/rodent/rodent-iv-plain.exe && cp -R personalities books /opt/rodent/ && rm -rf /tmp/rodent-source
RUN printf '%s  %s\n' c2ccc7a99b442dcaa2aa2dc06e020f0182fe7a198c6f080f45f555c126cdcd95 /opt/rodent/rodent-iv-plain.exe 250ff9c3548d06b06616356b01972230607469c95fb187e262f7044c4f6cfbe9 /usr/games/stockfish > /opt/engine-hashes
WORKDIR /app
COPY . /app
RUN pip install -e '.[dev]'
