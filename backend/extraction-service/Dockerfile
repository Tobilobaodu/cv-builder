# Extraction service — hosts Example's step 2 (routes/extract.ts) unchanged.
#
# extract.ts shells out to two external binaries; both must exist on PATH or
# every PDF/DOCX upload fails at runtime rather than at build time:
#   pdftotext  (poppler-utils) — PDF text extraction, invoked with -layout
#   unzip                      — DOCX, reads word/document.xml out of the zip
FROM node:24-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY package.json ./
# --legacy-peer-deps mirrors Example's .npmrc (strict-peer-dependencies=false).
# esbuild-plugin-pino declares a peer range of esbuild <=0.25.8 while Example
# pins esbuild 0.27.3; pnpm tolerates that, npm refuses without this flag.
# Both versions are Example's own — neither was changed.
RUN npm install --legacy-peer-deps

COPY tsconfig.json build.mjs ./
COPY src ./src
RUN npm run build

# index.ts requires PORT to be set and throws if it is missing.
ENV PORT=5050
EXPOSE 5050

CMD ["npm", "run", "start"]
