# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install dependencies and fix ImageMagick policy
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git imagemagick && \
    echo '<policymap></policymap>' > /etc/ImageMagick-7/policy.xml && \
    pip install --no-cache-dir -r requirements.txt && \
    pip cache purge && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the rest of the application's code to the working directory
COPY . .

# Run bot.py when the container launches
CMD ["python3", "bot.py"]
