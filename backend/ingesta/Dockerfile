# Contexto de construccion: backend/ (ver deploy/compose.yml), para poder
# copiar el paquete compartido zenit/ junto al punto de entrada.
FROM python:3.12-slim
WORKDIR /app
COPY ingesta/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY zenit/ ./zenit/
COPY ingesta/servidor.py ingesta/extraccion.py ingesta/destinos.py ./
EXPOSE 4317 8080
CMD ["python", "-u", "servidor.py"]
