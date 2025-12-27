# Build stage
FROM ubuntu:24.04 AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install UV
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock .python-version ./
COPY app ./app

# Install Python dependencies using UV
RUN uv sync --frozen --no-cache --no-dev

# Runtime stage
FROM ubuntu:24.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libmagickwand-dev \
    nginx \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /.venv /.venv
ENV PATH="/.venv/bin:$PATH"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY ImageMagick-6/policy.xml /etc/ImageMagick-6/policy.xml

COPY classifier_model.onnx /root/.NudeNet/classifier_model.onnx

RUN mkdir /images
RUN mkdir /cache

EXPOSE 5000

COPY app /app

WORKDIR /app

CMD bash entrypoint.sh
