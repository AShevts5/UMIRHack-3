# Демонстрационные случаи (реальный вывод pipeline)

| Документ | Тип | Что показывает | Статус | Пропуски | Ответ |
|---|---|---|---|---|---|
| `open-001` | `study_certificate` | Полная справка об обучении: все обязательные сведения и обе отметки | `document_ok` | — | `document_ok` / ['rule:3.1', 'rule:3.2', 'rule:3.3'] |
| `open-002` | `study_certificate` | Поле отсутствует: честный отказ вместо значения | `missing_fields` | issue_date | `None` / [] |
| `open-003` | `study_certificate` | Пустая линия подписи: подпись не обнаружена, печать есть | `missing_fields` | signature_present | `missing_fields` / ['rule:3.2'] |
| `open-004` | `study_certificate` | Есть подпись, нет печати (одинокое «М.П.» не считается печатью) | `missing_fields` | seal_present | `missing_fields` / ['rule:3.3'] |
| `open-010` | `study_certificate` | Нет ни подписи, ни печати: оба источника rule:3.2 и rule:3.3 | `missing_fields` | signature_present, seal_present | `missing_fields` / ['rule:3.2', 'rule:3.3'] |
| `open-014` | `academic_leave_application` | Заявление с пропуском: не хватает причины отпуска (rule:4.1) | `missing_fields` | leave_reason | `missing_fields` / ['rule:4.1'] |
| `open-018` | `academic_leave_application` | Ответ по Положению: сотрудник деканата, rule:1.3 | `document_ok` | — | `сотрудник деканата` / ['rule:1.3'] |
| `open-021` | `internship_certificate` | Справка о практике: графическая подпись и печать обнаружены | `document_ok` | — | `document_ok` / ['rule:5.1', 'rule:5.2', 'rule:5.3'] |
| `open-023` | `internship_certificate` | Печать есть, подписи нет (rule:5.2) | `missing_fields` | signature_present | `missing_fields` / ['rule:5.2'] |
| `open-004` | `study_certificate` | Нет данных в документе и Положении | `missing_fields` | seal_present | `None` / [] |
