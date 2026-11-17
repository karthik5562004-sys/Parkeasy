# ParkEasy - Smart Parking System (Podman)
Capstone project for 21CSE216P Linux and Container Technologies.

## Containers
pe-web (Nginx, 8080), pe-app (Flask on Red Hat UBI 9), pe-db (PostgreSQL, volume pe-pgdata), pe-cache (Redis). Network: pe-net.

## Build
podman build -t pe-app:v1 .
podman build -t pe-web:v1 .\web

## Run
podman network create pe-net
podman volume create pe-pgdata
podman run -d --name pe-db --network pe-net -v pe-pgdata:/var/lib/postgresql/data -e POSTGRES_USER=parking -e POSTGRES_PASSWORD=parking123 -e POSTGRES_DB=parking docker.io/library/postgres:16-alpine
podman run -d --name pe-cache --network pe-net docker.io/library/redis:7-alpine
podman run -d --name pe-app --network pe-net -e TZ=Asia/Kolkata -e DB_HOST=pe-db -e REDIS_HOST=pe-cache -e DB_USER=parking -e DB_PASSWORD=parking123 -e DB_NAME=parking pe-app:v1
podman run -d --name pe-web --network pe-net -p 8080:80 pe-web:v1

Open http://localhost:8080 (admin login: admin / admin123)
