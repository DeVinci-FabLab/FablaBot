# Use the official Python image from the Docker Hub, specifying the version and base image
FROM python:3.11.4-alpine3.18

# Copy the requirements file to the /tmp directory in the container
COPY ./requirements.txt /tmp

# Copy the .env file to the /home directory in the container
COPY .env /home/.env

# Set the working directory to /tmp
WORKDIR /tmp

# Install the Python dependencies listed in the requirements file
RUN pip install -r requirements.txt

# Upgrade pip to the latest version, ignoring root user warnings
RUN pip install --upgrade pip --root-user-action=ignore

# Set the working directory to /home
WORKDIR /home