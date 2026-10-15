# Construccion de la interfaz y servidor estatico.
FROM node:22-alpine AS construccion
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src/ ./src/
COPY simulado/ ./simulado/
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx-web.conf /etc/nginx/conf.d/default.conf
COPY --from=construccion /app/dist /usr/share/nginx/html
EXPOSE 80
