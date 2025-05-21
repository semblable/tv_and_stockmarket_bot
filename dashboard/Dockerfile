# Use python:3.10-slim as the base image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file from the dashboard directory (relative to project root context)
COPY dashboard/requirements.txt /app/requirements.txt

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the contents of the dashboard directory (relative to project root context)
COPY dashboard/ /app/

# Copy the api_clients directory (relative to project root context)
COPY api_clients/ /app/api_clients/

# Add /app to PYTHONPATH to ensure modules in this directory are discoverable
ENV PYTHONPATH="/app"

# Run gunicorn when the container launches
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]