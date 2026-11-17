# Frontend Dockerfile for Document Chatbot (Vite + TypeScript)
# Multi-stage build for optimized production image

# Build stage
FROM node:22-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install all dependencies (dev dependencies needed for build)
RUN npm ci

# Copy source code
COPY . .

# API URL is baked in at build time (Vite reads VITE_* variables during build)
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Build the application
RUN npm run build

# Production stage
FROM nginx:alpine

ENV PORT=8080 \
    NGINX_ENVSUBST_FILTER=^PORT$

# Copy built files from builder stage (Vite outputs to dist, not build)
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD-SHELL wget --quiet --tries=1 --spider "http://localhost:${PORT:-8080}/health" || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
