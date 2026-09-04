DSN ?= postgresql://postgres:postgres@localhost:5432/contractors
SNAPSHOT ?= ../contractors_audit.snapshot.json

.PHONY: up down schema load api demo test

up:            ## поднять PostgreSQL
	docker compose up -d db

down:
	docker compose down

schema:        ## накатить схему и справочник
	psql "$(DSN)" -f db/schema.sql -f db/seed_dictionary.sql

load:          ## загрузить снапшот в базу
	python scripts/load_snapshot.py --file $(SNAPSHOT) --dsn "$(DSN)" --create-schema

api:           ## запустить API, Swagger на http://localhost:8000/docs
	uvicorn app.main:app --reload --port 8000

demo:          ## прогон без базы, прямо по файлу выгрузки
	python scripts/demo_offline.py --file $(SNAPSHOT) --inn $(INN)

test:
	pytest -q
