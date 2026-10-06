FROM python:3.12-slim

WORKDIR /app

# ffmpeg: audio decode/trim/loudness for the documentary voice pipeline
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-documentary.txt ./
RUN pip install --no-cache-dir -r requirements-documentary.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
