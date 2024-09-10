FROM python:3.20-alpine

# Set environment variables
ENV VICTORIAMETRICS_URL=${VICTORIAMETRICS_URL}

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy Python files
COPY *.py ./

# Set entrypoint
ENTRYPOINT ["python3", "-u", "app.py"]
