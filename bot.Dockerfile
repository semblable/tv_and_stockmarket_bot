# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Install curl for debugging network issues and container healthcheck
# ca-certificates for HTTPS connections, dnsutils and build-essential for packages if needed
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    dnsutils \
    iputils-ping \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and group
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

# Set the working directory in the container
WORKDIR /app

# Ensure data directory exists with correct permissions
RUN mkdir -p /app/data && chown -R appuser:appuser /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install packages specified in requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY bot.py .
COPY config.py .
COPY logger.py .
COPY data_manager.py .
COPY data_manager_impl/ ./data_manager_impl/
COPY cogs/ ./cogs/
COPY api_clients/ ./api_clients/
COPY utils/ ./utils/

# Ensure application files are owned by appuser
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port 5000 for the embedded Flask server
EXPOSE 5000

# Healthcheck definition
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:5000/ || exit 1

# Define the command to run the application
CMD ["python", "bot.py"]