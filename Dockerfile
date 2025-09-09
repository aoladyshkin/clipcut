# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# We also need ffmpeg for moviepy and git for installing packages from git
RUN apt-get update && apt-get install -y ffmpeg git && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code to the working directory
COPY . .

# Run bot.py when the container launches
CMD ["python3", "bot.py"]
