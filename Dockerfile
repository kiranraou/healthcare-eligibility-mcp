FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY server.py .

RUN useradd --create-home --uid 10001 mcp \
    && mkdir -p data \
    && python -m src.seed \
    && chown -R mcp:mcp /app/data
USER mcp

EXPOSE 8000

# MCP endpoint: http://localhost:8000/mcp
CMD ["python", "server.py"]
