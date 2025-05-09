# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

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