FROM python:3.12-slim AS builder

WORKDIR /app

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.4

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-dev --no-interaction --no-ansi

# Build stage
FROM python:3.12-slim AS runtime

WORKDIR /opt/taurus-executor

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ ./src/
COPY proto/ ./proto/
COPY scripts/ ./scripts/
COPY manage/ ./manage/
COPY Makefile ./

# Generate gRPC code
RUN make build-all

# Create non-root user
RUN groupadd -r taurus && useradd -r -g taurus -d /opt/taurus-executor -s /sbin/nologin taurus
RUN chown -R taurus:taurus /opt/taurus-executor

# Switch to non-root user
USER taurus

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    GRPC_HOST=0.0.0.0 \
    GRPC_PORT=50051 \
    LOG_LEVEL=INFO \
    LOG_FORMAT=json

# Expose gRPC port
EXPOSE 50051

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:50051/ || exit 1

# Run the executor
CMD ["python", "-m", "executor_core.main"]