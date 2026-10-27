FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENV PORT=8000 HOST=0.0.0.0
EXPOSE 8000
USER nobody
CMD ["python", "server.py"]
