FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY agents ./agents
RUN pip install --no-cache-dir . "psycopg[binary]"
COPY install.yaml ./install.yaml
