FROM python:3.12-slim

WORKDIR /app

# System dependencies for psycopg2 and FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . .

# Install Python dependencies and package in editable mode
RUN pip install --no-cache-dir -e ".[dev]"

# Ensure entrypoint is executable
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]

