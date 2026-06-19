# The Vigil Next.js frontend — multi-stage PRODUCTION build (next build → standalone runtime).
# PACKAGING ONLY: the UI is a static-served HTTP server. The BROWSER (on the host) makes the API /
# Guide calls, so the NEXT_PUBLIC_* base URLs are inlined at BUILD time and default to the
# HOST-published localhost ports (the app API :8000, the Guide :8080) — NOT compose service names.
# Override via build args for another host. Node 22 + npm matches CI (npm ci against package-lock.json).
# Build context is ./frontend (see frontend/.dockerignore).

# 1) deps — install from the committed lockfile (reproducible, no dev/build steps yet).
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# 2) build — next build with the public URLs inlined into the client bundle.
FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined HERE (build time) and the browser (on the host) calls them — so they are
# the HOST-published ports, never compose service names. Defaults match the frontend's own code
# defaults (lib/api.ts, lib/guide.ts); overridable per environment.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_GUIDE_URL=http://localhost:8080
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_GUIDE_URL=$NEXT_PUBLIC_GUIDE_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# 3) runtime — slim standalone server: the traced server.js + only the deps it needs (no source,
#    no dev deps, no full node_modules). Runs as the image's built-in non-root `node` user.
FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# public assets + the standalone server + the static chunks (standalone does not copy these itself).
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
# HOSTNAME=0.0.0.0 makes the standalone server reachable from outside the container (it binds
# localhost otherwise). server.js is emitted at the standalone root by `output: "standalone"`.
CMD ["node", "server.js"]
