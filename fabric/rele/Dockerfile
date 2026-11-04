FROM python:3.12-alpine
RUN pip install --no-cache-dir "websockets==17.4"
WORKDIR /app
COPY rele.py ./
ENV PYTHONUNBUFFERED=1 PUERTO=8095
USER nobody
EXPOSE 8095
CMD ["python", "rele.py"]
