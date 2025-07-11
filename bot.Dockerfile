# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install curl for debugging network issues and for yfinance dependency (curl_cffi)
# Also install ca-certificates for HTTPS connections and dnsutils for DNS debugging
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    dnsutils \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the environment file
COPY .env .

# Copy the rest of the application code into the container at /app
COPY bot.py .
COPY config.py .
COPY data_manager.py .
COPY cogs/ ./cogs/
COPY api_clients/ ./api_clients/
COPY utils/ ./utils/

# Expose port 5000 for the embedded Flask server
EXPOSE 5000

# Define the command to run the application
CMD ["python", "bot.py"]