FROM python:3.12-slim
WORKDIR /app
COPY server/pyproject.toml /app/pyproject.toml
COPY server/app /app/app
RUN pip install --no-cache-dir .
ENV DEBUG=false
ENV OPENAPI_ENABLED=false
EXPOSE 8767
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8767"]
