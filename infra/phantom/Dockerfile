FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake \
    && CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_CUDA=OFF" pip install --no-cache-dir llama-cpp-python==0.3.16 \
    && apt-get purge -y build-essential cmake && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
COPY worker.py /worker.py
USER 65534:65534
ENTRYPOINT ["python", "-I", "/worker.py"]
