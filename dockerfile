# FROM python:3.11-slim
FROM python:3.9.13-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 80
# Expose a port for debugpy (commonly 5678)
EXPOSE 5678

# Run Uvicorn with debugpy, waiting for the debugger to attach
CMD ["python", "-m", "debugpy", "--listen", "0.0.0.0:5678", "--wait-for-client", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]