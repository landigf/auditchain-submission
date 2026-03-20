FROM python:3.11-slim

WORKDIR /app

# System deps for scikit-fuzzy + psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Init DB schema, seed data, then start server
# Card-path bridge: Metaflow card reader resolves <ds-root>/mf.cards/<FlowName>/…
# but the writer stores under <ds-root>/<FlowName>/… — an entrypoint script
# creates the symlink dynamically.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
