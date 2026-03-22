FROM node:20-alpine AS build

WORKDIR /app/apps/frontend

COPY apps/frontend/package*.json ./
RUN npm install --no-audit --no-fund

COPY apps/frontend/index.html ./
COPY apps/frontend/vite.config.js ./
COPY apps/frontend/src ./src

ARG VITE_API_BASE_URL=/api
ARG VITE_LANGFUSE_SOCKET_URL=
ARG VITE_GITHUB_APP_INSTALL_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_LANGFUSE_SOCKET_URL=${VITE_LANGFUSE_SOCKET_URL}
ENV VITE_GITHUB_APP_INSTALL_URL=${VITE_GITHUB_APP_INSTALL_URL}

RUN npm run build

FROM nginx:1.27-alpine

COPY infra/docker/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/frontend/dist /usr/share/nginx/html

EXPOSE 80
