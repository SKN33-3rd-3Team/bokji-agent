FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-graph.txt requirements-embedding.txt requirements-vector.txt requirements-auth.txt ./
COPY backend/requirements-backend.txt ./backend/

RUN python -m pip install --no-cache-dir torch \
        --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir -r backend/requirements-backend.txt \
    && python -m pip check \
    && python -c "import torch; assert torch.version.cuda is None"

COPY backend/ ./backend/
COPY src/ ./src/
COPY rag_design/ ./rag_design/
COPY streamlit_ui/__init__.py streamlit_ui/constants.py ./streamlit_ui/

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home app \
    && mkdir -p /app/.runtime /app/logs \
    && chown app:app /app/.runtime /app/logs

USER app

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
