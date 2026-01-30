FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "mem0ai>=0.1.48" \
    "fastapi==0.115.8" \
    "uvicorn==0.34.0" \
    "psycopg[pool]>=3.2.8" \
    "python-dotenv==1.0.1"

COPY main.py /app/main.py

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
