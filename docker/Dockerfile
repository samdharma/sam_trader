# SAM Trader V3 — Nautilus TradingNode
# Production stage only (dev stage added later if needed)
FROM ghcr.io/nautechsystems/nautilus_trader:latest

LABEL maintainer="SAM Trader Team"
LABEL description="SAM Trader V3 — Production-grade autonomous trading platform built on NautilusTrader"

# Install system dependencies
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for runtime
RUN groupadd -r sam && useradd -r -g sam -d /opt/sam_trader -s /bin/bash sam

# Set working directory
WORKDIR /opt/sam_trader

# Copy source code and install package in editable mode
COPY --chown=sam:sam src/ ./src/
COPY --chown=sam:sam pyproject.toml ./
COPY --chown=sam:sam README.md ./
COPY --chown=sam:sam docker/requirements.txt ./docker-requirements.txt

# Install extra Python dependencies and package
RUN pip install --no-cache-dir -r docker-requirements.txt \
    && pip install --no-cache-dir -e .

# Ensure PYTHONPATH includes the package source
ENV PYTHONPATH="/opt/sam_trader/src"

# Switch to non-root user
USER sam

# Copy and set up entrypoint
COPY --chown=sam:sam docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python3", "-m", "sam_trader.main"]
