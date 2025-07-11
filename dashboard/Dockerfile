# Use python:3.10-slim as the base image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file from the dashboard directory (relative to project root context)
COPY dashboard/requirements.txt /app/requirements.txt

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the environment file from project root
COPY .env /app/.env

# Copy the contents of the dashboard directory (relative to project root context)
COPY dashboard/ /app/

# Copy the api_clients directory (relative to project root context)
COPY api_clients/ /app/api_clients/

# Add /app to PYTHONPATH to ensure modules in this directory are discoverable
ENV PYTHONPATH="/app"

# Expose port 8000 (to match docker run port mapping)
EXPOSE 8000

# Run gunicorn when the container launches (bind to port 8000)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]