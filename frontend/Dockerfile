# ---------------------------------------------------------------- build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Dependencies are installed from the lockfile alone so this layer is reused
# whenever only application source has changed.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

# -------------------------------------------------------------- runtime stage
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# nginx binds IPv4 only, and BusyBox wget would otherwise try ::1 first.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
