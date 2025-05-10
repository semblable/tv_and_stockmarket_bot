# 1. Use an appropriate official Python base image
FROM python:3.10-slim

# 2. Set a working directory
WORKDIR /app

# 3. Copy the requirements.txt file and install dependencies
COPY ./dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# 4. Copy all necessary application files and directories
COPY ./dashboard/__init__.py .
COPY ./dashboard/app.py .
COPY ./dashboard/config.py .
COPY ./dashboard/internal_api_client.py .
COPY ./dashboard/static ./static
COPY ./dashboard/templates ./templates

# 5. Expose the port Gunicorn will listen on
EXPOSE 8000

# 6. Set the command to run the Flask app using Gunicorn
# Assuming the Flask app instance is named 'app' in app.py
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]
# TEMPORARY CMD to debug startup
# CMD ["python", "/app/app.py"]