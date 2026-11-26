FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY playground.py ./
COPY static ./static
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=3s --start-period=3s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]
CMD ["python", "playground.py"]
