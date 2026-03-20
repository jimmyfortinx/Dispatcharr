# Define the upstream base image before any FROM.
ARG BASE_IMAGE=ghcr.io/dispatcharr/dispatcharr:base

# --- Build frontend ---

FROM node:24 AS frontend-builder

WORKDIR /app/frontend
COPY ./frontend /app/frontend
# remove any node_modules that may have been copied from the host (x86)
RUN rm -rf node_modules || true; \
    npm install --no-audit --progress=false;
RUN    npm run build; \
    rm -rf node_modules .cache

# --- Redeclare build arguments for the next stage ---
ARG BASE_IMAGE

# --- Final image based on the dynamic base ---
FROM ${BASE_IMAGE} AS final
ENV VIRTUAL_ENV=/dispatcharrpy
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /app

# Copy application code
COPY . /app
# Copy nginx configuration
COPY ./docker/nginx.conf /etc/nginx/sites-enabled/default
# Fix line endings and make entrypoint scripts executable
RUN for f in /app/docker/entrypoint*.sh; do \
        if [ -f "$f" ]; then \
            sed -i 's/\r$//' "$f" && chmod +x "$f"; \
        fi; \
    done
# Clean out existing frontend folder
RUN rm -rf /app/frontend
# Copy built frontend assets
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Add timestamp argument
ARG TIMESTAMP

# Update version.py with build timestamp if provided
RUN if [ -n "$TIMESTAMP" ]; then \
    echo "Updating timestamp to ${TIMESTAMP} in version.py" && \
    sed -i "s|__timestamp__ = None.*|__timestamp__ = '${TIMESTAMP}'    # Set during CI/CD build process|" /app/version.py && \
    cat /app/version.py; \
    fi

ENTRYPOINT ["/app/docker/entrypoint.sh"]
