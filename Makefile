SHELL := /bin/bash

.PHONY: setup

setup:
	@if [ ! -f .env ]; then \
		echo "ERROR: .env not found. Run: cp .env.example .env"; \
		exit 1; \
	fi
	@set -a; source .env; set +a; \
	if [ -z "$$PASSWORD" ]; then \
		echo "ERROR: PASSWORD is empty in .env"; \
		exit 1; \
	fi; \
	HASH=$$(openssl passwd -apr1 "$$PASSWORD"); \
	if rg -q '^TRAEFIK_HASHED_PASSWORD=' .env; then \
		sed -i.bak "s|^TRAEFIK_HASHED_PASSWORD=.*|TRAEFIK_HASHED_PASSWORD=$$HASH|" .env; \
	else \
		echo "TRAEFIK_HASHED_PASSWORD=$$HASH" >> .env; \
	fi; \
	rm -f .env.bak; \
	echo "Updated TRAEFIK_HASHED_PASSWORD in .env"
	@set -a; source .env; set +a; \
	if [ -z "$$LOG_VOLUME" ]; then \
		echo "ERROR: LOG_VOLUME is empty in .env"; \
		exit 1; \
	fi; \
	mkdir -p "$$LOG_VOLUME"; \
	echo "Ensured LOG_VOLUME directory exists: $$LOG_VOLUME"
	@docker network inspect traefik-public >/dev/null 2>&1 || docker network create traefik-public
	@echo "Ensured Docker network exists: traefik-public"
