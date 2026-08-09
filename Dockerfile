FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY main_v15.py /app/main_v15.py

CMD ["python", "-u", "main_v15.py"]
