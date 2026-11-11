FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

COPY . /app

RUN if [ -f pyproject.toml ] || [ -f setup.py ]; then \
      uv pip install --system -e .; \
    else \
      find /app -name pyproject.toml -maxdepth 2 -exec dirname {} \; | \
        xargs -I {} uv pip install --system -e {}; \
    fi

EXPOSE 8080

# gapp appends the CMD: the app's Procfile web: command, run by the shell
# so $PORT expands when the container starts.
