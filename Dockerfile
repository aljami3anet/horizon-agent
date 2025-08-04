# Multi-stage build for production
FROM python:3.11-slim as builder

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user
RUN useradd --create-home --shell /bin/bash app && \
    mkdir -p /app/chats /app/workspace /app/knowledge /app/backups /app/logs && \
    chown -R app:app /app

# Set working directory
WORKDIR /app

# Copy application code
COPY app.py .
COPY static/ static/
COPY tests/ tests/

# Create necessary directories
RUN mkdir -p chats workspace knowledge backups logs

# Switch to non-root user
USER app

# Expose port
EXPOSE 5051

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5051/health || exit 1

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:5051", "--workers", "4", "--timeout", "120", "app:app"]