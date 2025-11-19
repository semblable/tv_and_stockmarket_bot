# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Install curl for debugging network issues
# Also install ca-certificates for HTTPS connections and dnsutils for DNS debugging
# Added build-essential for compiling python packages if wheels are missing
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    dnsutils \
    iputils-ping \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# Upgrade pip first to ensure we can install newer wheels
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the environment file
COPY .env .

# Copy the rest of the application code into the container at /app
COPY bot.py .
COPY config.py .
COPY logger.py .
COPY data_manager.py .
COPY cogs/ ./cogs/
COPY api_clients/ ./api_clients/
COPY utils/ ./utils/

# Expose port 5000 for the embedded Flask server
EXPOSE 5000

# Define the command to run the application
CMD ["python", "bot.py"]