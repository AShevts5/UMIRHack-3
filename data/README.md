# Примеры манифестов

`sample_documents.json` и `sample_questions.json` — минимальный пример формата
манифестов кейса. Реальных персональных данных здесь нет: чтобы получить образец,
сгенерируйте синтетическую страницу тестовым рендерером и положите её рядом:

```bash
python - <<'PY'
from pathlib import Path
from backend.tests.test_pipeline import render_study_certificate, SYNTHETIC_VALUES

out = Path("data/sample-study-01.png")
out.write_bytes(render_study_certificate(SYNTHETIC_VALUES))
print("готово:", out)
PY
```

Затем пакетный прогон на этом примере:

```bash
python -m backend.scripts.run_batch \
  --documents data/sample_documents.json \
  --questions data/sample_questions.json \
  --root data \
  --output data/sample_results.json
```

Открытый набор организатора (30 документов, 120 вопросов) в репозиторий не
копируется: он подключается параметром `--root` / переменной `UMIR_ARCHIVE`.
