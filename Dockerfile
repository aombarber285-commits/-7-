FROM python:3.11-slim

WORKDIR /app

COPY main_v15.py .

CMD ["python", "-u", "main_v15.py"]
