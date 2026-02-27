FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY grip/ grip/
RUN pip install --no-cache-dir .

VOLUME /data

EXPOSE 8000

CMD ["uvicorn", "grip.web:app", "--host", "0.0.0.0", "--port", "8000"]
