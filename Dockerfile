FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/root/.cache/huggingface

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Gasmoneky/DOCS_RAG_SYSTEM.git
# Here place the git url of the documentation in like here
RUN git clone https://github.com/Gasmoneky/drogonmd_files.git

COPY requirements.txt .

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt



EXPOSE 11435
CMD ["python", "DOCS_RAG_SYSTEM/fastapi_rag_system.py"]
