# Удобные обёртки над python -m backend.scripts.tasks.
# На Windows те же задачи запускаются напрямую, без GNU make:
#   python -m backend.scripts.tasks install | test | batch | evaluate | verify | api | demo

PY ?= python
UMIR_ARCHIVE ?=

.PHONY: install test batch evaluate verify api demo clean

install:
	$(PY) -m backend.scripts.tasks install

test:
	$(PY) -m backend.scripts.tasks test

batch:
	$(PY) -m backend.scripts.tasks batch $(if $(UMIR_ARCHIVE),--root $(UMIR_ARCHIVE),)

evaluate:
	$(PY) -m backend.scripts.tasks evaluate $(if $(UMIR_ARCHIVE),--root $(UMIR_ARCHIVE),)

verify:
	$(PY) -m backend.scripts.tasks verify $(if $(UMIR_ARCHIVE),--root $(UMIR_ARCHIVE),)

api:
	$(PY) -m backend.scripts.tasks api

# Полный прогон: results.json -> метрики -> проверки целостности.
demo:
	$(PY) -m backend.scripts.tasks demo $(if $(UMIR_ARCHIVE),--root $(UMIR_ARCHIVE),)

clean:
	rm -rf .pytest_tmp .pytest_cache reports/*.local.json
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
