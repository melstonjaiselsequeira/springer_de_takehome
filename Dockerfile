FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY your_script.py .
RUN mkdir -p /app/data /app/output
ENTRYPOINT ["python", "your_script.py"]
CMD ["--input-dir", "/app/data", "--output-dir", "/app/output"]
