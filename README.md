# STAC API Server

Custom fork of stac-fastapi server, with edited Dockerfile and few
additional features.

# Deployment

## Build Jobs

Two build jobs set up, for building both prod and dev docker image
from Dockerfile_dev and Dockerfile_prod.

## Environment variables

| Var name | Used for |
| --- | --- |
|APP_HOST| IP To bind the stac-fastapi server to (0.0.0.0) |
|APP_PORT| Port on which to run the stac-fastapi |
|POSTGRES_USER| Postgres username|
|POSTGRES_PASS| Postgres password|
|POSTGRES_DBNAME| Postgres database name (must have postgis and btree plugin enabled)|
|POSTGRES_HOST_READER| Hostname of the database for read connections|
|POSTGRES_HOST_WRITER| Hostname of the database for write connections|
|POSTGRES_PORT| Postgress port|

Check out docker-compose.yml for other variables
## Setting up the database

Apply ./scripts/setup_pgstac_schema.sql on your database specified with POSTGRES_DBNAME env var.

