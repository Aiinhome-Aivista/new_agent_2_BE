FROM python:3.12-slim

WORKDIR /app

# Install system dependencies if any are needed for Python packages
# RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install PyTorch CPU wheels first (keeps Docker image lightweight, prevents ~4GB CUDA download)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install remaining requirements
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port 3012
EXPOSE 3012

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3012"]
