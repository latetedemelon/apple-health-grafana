FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy Python files
COPY *.py ./

# Set entrypoint
ENTRYPOINT ["python3", "-u", "app.py"]
