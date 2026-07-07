FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/root/.cache/huggingface

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN python -c "from langchain_community.embeddings import HuggingFaceEmbeddings; HugFaceEmbeddings(model_name='all-MiniLM-L6-v2')"

EXPOSE 11435
ENTRYPOINT ["./entrypoint.sh"]
