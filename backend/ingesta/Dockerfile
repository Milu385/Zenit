FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY servidor.py .
EXPOSE 4317 8080
CMD ["python", "-u", "servidor.py"]
