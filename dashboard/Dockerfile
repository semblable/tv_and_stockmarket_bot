# 1. Use an appropriate official Python base image
FROM python:3.10-slim

# 2. Set a working directory
WORKDIR /app

# 3. Copy the requirements.txt file and install dependencies
COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# 4. Copy all necessary application files and directories
COPY ./__init__.py .
COPY ./app.py .
COPY ./config.py .
COPY ./internal_api_client.py .
COPY ./static ./static
COPY ./templates ./templates

# 5. Expose the port Gunicorn will listen on
EXPOSE 8000

# 6. Set the command to run the Flask app using Gunicorn
# Assuming the Flask app instance is named 'app' in app.py
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]