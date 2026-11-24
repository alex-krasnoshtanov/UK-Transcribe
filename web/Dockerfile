# syntax=docker/dockerfile:1
#
# The dashboard: Next.js standalone server. No web fonts or outside assets, so
# neither the build output nor the browser needs the internet. The cluster runs
# it as uid 10002, so everything here is root-owned and world-readable.
#
#   docker build -t uk-transcribe-web:local web

FROM node:24.22.3-alpine3.24 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24.22.3-alpine3.24 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24.22.3-alpine3.24 AS run
LABEL org.opencontainers.image.source="https://github.com/alex-krasnoshtanov/UK-Transcribe" \
      org.opencontainers.image.description="UK-Transcribe dashboard"
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8000 \
    HOSTNAME=0.0.0.0 \
    NODE_OPTIONS=--max-old-space-size=256
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||8000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
