# Multi-stage Dockerfile for Mathematically Correct Builds Flask Application
# Optimized for Azure Container Apps with minimal image size

# Stage 1: Builder
FROM python:3.14-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies (kept in builder, not in final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies to virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install -r requirements.txt

# Stage 2: Runtime
FROM python:3.14-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    FLASK_APP=main.py \
    FLASK_ENV=production \
    PORT=5000

# Set working directory
WORKDIR /app

# Create non-root user for security
RUN useradd -m -u 1000 flask && \
    chown -R flask:flask /app

# Copy virtual environment from builder
COPY --from=builder --chown=flask:flask /opt/venv /opt/venv

# Copy application code
COPY --chown=flask:flask . .

# Switch to non-root user
USER flask

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/health', timeout=5)" || exit 1

# Run the application with Waitress WSGI server
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--threads=4", "main:app"]
