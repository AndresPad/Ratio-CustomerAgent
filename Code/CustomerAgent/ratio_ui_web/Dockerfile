# ============================================
# Stage 1: Build the React application
# ============================================
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files and install dependencies
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts

# Copy source code and build
COPY . .
RUN npm run build

# ============================================
# Stage 2: Serve with nginx (smaller, secure)
# ============================================
FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy nginx template (uses envsubst for runtime configuration)
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Also copy the static nginx.conf as fallback for local docker-compose
COPY nginx.conf /etc/nginx/conf.d/default.conf.bak

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Default backend URLs (overridden via env vars in Azure)
ENV BACKEND_AGENTS_URL=http://ratio-agents:8000 \
    BACKEND_SR_INSIGHTS_URL=http://ratio-sr-insights:8006 \
    BACKEND_FUSE_URL=http://ratio-fuse:8008 \
    BACKEND_CUSTOMERAGENT_URL=http://ratio-customeragent:8503

# Use non-root user
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chown -R nginx:nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid

# Create entrypoint script that renders the template then starts nginx
RUN printf '#!/bin/sh\nenvsubst "\$BACKEND_AGENTS_URL \$BACKEND_SR_INSIGHTS_URL \$BACKEND_FUSE_URL \$BACKEND_CUSTOMERAGENT_URL" < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf\nexec nginx -g "daemon off;"\n' > /docker-entrypoint.sh && \
    chmod +x /docker-entrypoint.sh

USER nginx

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD wget -q --spider http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
