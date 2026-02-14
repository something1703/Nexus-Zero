FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY common/ /app/common/
COPY detective-agent/requirements.txt /app/requirements.txt
COPY detective-agent/main.py /app/main.py

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

ENV PORT=8080
ENV HOST=0.0.0.0

CMD ["python", "main.py"]
