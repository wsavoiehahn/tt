# FROM python:3.11-slim
FROM python:3.9.13-slim
WORKDIR /app

# # Install ffmpeg for pydub compatibility
# RUN apt-get update && apt-get install -y ffmpeg

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 80
# Expose a port for debugpy (commonly 5678)
EXPOSE 5678

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Run Uvicorn with debugpy, waiting for the debugger to attach
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
# CMD ["python", "-m", "debugpy", "--listen", "0.0.0.0:5678", "--wait-for-client", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]