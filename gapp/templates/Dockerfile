FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

COPY . /app

ARG RUNTIME_REF
RUN if [ -f pyproject.toml ] || [ -f setup.py ]; then \
      uv pip install --system -e .; \
    else \
      find /app -name pyproject.toml -maxdepth 2 -exec dirname {} \; | \
        xargs -I {} uv pip install --system -e {}; \
    fi \
    && if [ -n "$RUNTIME_REF" ]; then uv pip install --system "gapp-run @ git+https://github.com/krisrowe/gapp.git@${RUNTIME_REF}#subdirectory=run"; fi

ARG ENTRYPOINT
ENV ENTRYPOINT=${ENTRYPOINT}

EXPOSE 8080

CMD ["sh", "-c", "case \"$ENTRYPOINT\" in __mcp_app_serve__) mcp-app serve ;; __cmd__:*) eval \"${ENTRYPOINT#__cmd__:}\" ;; *) uvicorn ${ENTRYPOINT} --host 0.0.0.0 --port 8080 ;; esac"]
