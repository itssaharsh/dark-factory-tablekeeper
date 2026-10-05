# Stage 1: fetch the font files from the @fontsource packages (network at
# build time only; the final image links no remote host for anything).
FROM node:20-slim AS fonts
WORKDIR /fonts
RUN npm install @fontsource/marcellus @fontsource/figtree @fontsource/spline-sans-mono --no-save
RUN mkdir -p out && \
    cp node_modules/@fontsource/marcellus/files/marcellus-latin-400-normal.woff2 out/ && \
    cp node_modules/@fontsource/figtree/files/figtree-latin-400-normal.woff2 out/ && \
    cp node_modules/@fontsource/figtree/files/figtree-latin-500-normal.woff2 out/ && \
    cp node_modules/@fontsource/figtree/files/figtree-latin-600-normal.woff2 out/ && \
    cp node_modules/@fontsource/figtree/files/figtree-latin-700-normal.woff2 out/ && \
    cp node_modules/@fontsource/spline-sans-mono/files/spline-sans-mono-latin-400-normal.woff2 out/ && \
    cp node_modules/@fontsource/spline-sans-mono/files/spline-sans-mono-latin-500-normal.woff2 out/

# Stage 2: the service itself -- Python 3.12 standard library only.
FROM python:3.12-slim

WORKDIR /srv

COPY app ./app
COPY web ./web
COPY --from=fonts /fonts/out/ ./web/static/fonts/

ENV PORT=8080
EXPOSE 8080

CMD ["python3", "-m", "app.server"]
