FROM node:20-slim AS builder
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
ARG VITE_API_URL
RUN npm run build

FROM node:20-slim
RUN npm install -g serve
COPY --from=builder /app/dist /app/dist
EXPOSE 3000
CMD ["sh", "-c", "serve -s /app/dist -l ${PORT:-3000}"]
