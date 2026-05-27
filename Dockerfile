FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir flask aiohttp

COPY app.py .
COPY static/ ./static/

EXPOSE 5000

CMD ["python", "app.py"]
