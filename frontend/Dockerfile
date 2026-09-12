FROM node:20-alpine

WORKDIR /app

# Install exactly the dependency graph reviewed in package-lock.json.
COPY package.json package-lock.json ./
RUN npm ci

# Copy source
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
