// Черновик демо-видео (≈2 минуты, без звука) по docs/demo_scenario.md: Playwright сам
// прокликивает сценарий, рисует субтитры и курсор и пишет экран в .webm.
//
//   npm i -g playwright && npx playwright install chromium   # один раз
//   python -m backend.scripts.tasks api                       # сервер на :8000
//   UMIR_DEMO_LOGIN=... UMIR_DEMO_PASSWORD=... UMIR_DEMO_DOCS="/путь/к/архиву/documents" node scripts/record_demo.cjs
//   ffmpeg -i video/<файл>.webm -c:v libx264 -crf 24 -pix_fmt yuv420p demo.mp4
//
// Пользователя UMIR_DEMO_LOGIN надо заранее завести в приложении руками.
const path = require('path')
let playwright
try { playwright = require('playwright') } catch { playwright = require(require('child_process').execSync('npm root -g').toString().trim() + '/playwright') }
const { chromium } = playwright
const BASE = process.env.UMIR_DEMO_URL || 'http://localhost:8000/'
const LOGIN = process.env.UMIR_DEMO_LOGIN || 'daniil'
const PASSWORD = process.env.UMIR_DEMO_PASSWORD || 'demo-pass-2026'
if (!process.env.UMIR_DEMO_DOCS) {
  console.error('Укажите UMIR_DEMO_DOCS — папку documents из архива кейса (open-001.png, open-014.png, open-023.png).')
  process.exit(2)
}
const D = process.env.UMIR_DEMO_DOCS + path.sep
const OUT = process.env.UMIR_DEMO_OUT || 'video'
const W=1280, H=720;
const INIT = `
(() => {
  const style = document.createElement('style');
  style.textContent = '#demo-cap{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99999;max-width:88vw;padding:10px 18px;border-radius:12px;background:rgba(10,14,26,.86);color:#fff;font:600 19px/1.35 system-ui,sans-serif;text-align:center;box-shadow:0 8px 24px rgba(0,0,0,.35);pointer-events:none;transition:opacity .25s}#demo-cap:empty{opacity:0}#demo-cap small{display:block;font-weight:500;font-size:14px;opacity:.75;margin-top:2px}#demo-cur{position:fixed;z-index:100000;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgba(255,196,0,.85);box-shadow:0 0 0 3px rgba(255,255,255,.7);pointer-events:none;transition:transform .12s}#demo-cur.down{transform:scale(.7)}';
  const mount = () => {
    if (!document.body || document.getElementById('demo-cap')) return;
    document.head.appendChild(style);
    const cap = document.createElement('div'); cap.id = 'demo-cap'; document.body.appendChild(cap);
    const cur = document.createElement('div'); cur.id = 'demo-cur'; cur.style.left='-40px'; cur.style.top='-40px'; document.body.appendChild(cur);
    addEventListener('mousemove', e => { cur.style.left = e.clientX+'px'; cur.style.top = e.clientY+'px' }, true);
    addEventListener('mousedown', () => cur.classList.add('down'), true);
    addEventListener('mouseup', () => cur.classList.remove('down'), true);
  };
  window.__cap = (t, s) => { mount(); const c = document.getElementById('demo-cap'); c.innerHTML = t ? t + (s ? '<small>'+s+'</small>' : '') : ''; };
  document.addEventListener('DOMContentLoaded', mount);
})();`;
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: W, height: H }, colorScheme: 'light', recordVideo: { dir: OUT, size: { width: W, height: H } }, permissions: ['geolocation'] });
  await ctx.addInitScript(INIT);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  const t0 = Date.now(); const mark = (n) => console.log(((Date.now()-t0)/1000).toFixed(1)+'s', n);
  const wait = ms => p.waitForTimeout(ms);
  const cap = (t, s) => p.evaluate(([t, s]) => window.__cap && window.__cap(t, s), [t, s || '']);
  const move = async (loc) => { const box = await loc.boundingBox(); if (!box) return; await p.mouse.move(box.x + box.width/2, box.y + box.height/2, { steps: 18 }); await wait(250); };
  const click = async (loc) => { loc = loc.first(); await loc.scrollIntoViewIfNeeded().catch(()=>{}); await move(loc); await loc.click(); };
  const type = async (loc, text) => { await click(loc); await loc.pressSequentially(text, { delay: 45 }); };

  // 0. вход
  await p.goto(BASE); await wait(900); mark('login');
  await cap('Онлайн-деканат', 'проверка документов по Положению + путеводитель по вузу; всё локально, без внешних API');
  await wait(3500);
  await type(p.locator('#auth-login'), LOGIN);
  await type(p.locator('#auth-pass'), PASSWORD);
  await cap('Вход: логин и пароль, Яндекс ID или 2FA');
  await click(p.locator('.auth__submit')); await wait(2600); mark('hall');
  await cap('Холл главного корпуса', 'разделы и картинки берутся из конфига вуза');
  await wait(2500);

  // 1. полная справка
  await click(p.locator('text=Обращение в деканат')); await wait(1200); mark('dean');
  await cap('Шаг 1. Справка об обучении', 'реальный OCR на этом ноутбуке');
  await p.setInputFiles('input[type=file]', D + 'open-001.png');
  await p.locator('.tabs').waitFor({ timeout: 30000 }); await wait(800); mark('result1');
  await cap('Все обязательные сведения есть', 'у каждого поля — ключ, пункт Положения и уверенность');
  await wait(3500);
  const field = p.locator('.field-row, .fields button, [class*=field] button').first();
  if (await field.count()) { await click(field); await wait(1800); }
  await click(p.locator('role=tab[name=/Распознанный текст/]')); await cap('Распознанный текст — настоящий вывод OCR'); await wait(2500);

  // 2. заявление без причины
  await click(p.locator('text=Проверить другой документ')); await wait(800); mark('doc2');
  await cap('Шаг 2. Заявление на академический отпуск');
  await p.setInputFiles('input[type=file]', D + 'open-014.png');
  await p.locator('.tabs').waitFor({ timeout: 30000 }); await wait(800);
  await cap('Не хватает обязательного поля «Причина отпуска»', 'пункт 4.1 Положения; остальные поля прочитаны');
  await wait(3500);
  await click(p.locator('role=tab[name=/Вопросы/]')); await wait(500);
  await type(p.locator('.chat input, .chat textarea, input[placeholder*="Например"]'), 'Укажи значение поля «Причина отпуска».');
  await p.keyboard.press('Enter'); await wait(2200);
  await cap('Значения нет — сервис не придумывает', 'answerable = false, value = null'); await wait(3000); mark('q2');

  // 3. отказ, и честный
  await type(p.locator('.chat input, .chat textarea, input[placeholder*="Например"]'), 'Какова стоимость обучения за следующий семестр?');
  await p.keyboard.press('Enter'); await wait(2200);
  await cap('Шаг 3. Вопрос вне документа и Положения', 'честный отказ вместо выдумки'); await wait(3200);

  // 4. практика: подпись по графике
  await click(p.locator('text=Проверить другой документ')); await wait(800); mark('doc3');
  await cap('Шаг 4. Справка о практике без подписи');
  await p.setInputFiles('input[type=file]', D + 'open-023.png');
  await p.locator('.tabs').waitFor({ timeout: 30000 }); await wait(800);
  await cap('Подпись и печать определяются по изображению', 'а не по слову «Подпись» в тексте'); await wait(4000);
  await cap('Открытый набор организатора: поля 1.0 · проверка 1.0 · ответы 1.0', 'evaluate.py, 30 документов и 120 вопросов'); await wait(3800);

  // 5. путеводитель по вузу
  await click(p.locator('.back-btn')); await wait(1200); mark('guide');
  await cap('Сверх кейса: путеводитель по вузу');
  await click(p.locator('text=Факультеты и корпуса')); await wait(900);
  await type(p.locator('.fac input, input[type=search]'), 'юрид'); await wait(900);
  await cap('Справка — только с официального сайта', 'у каждой записи источник; 103 из 103 значений сверены скриптом'); await wait(3200);
  await click(p.locator('text=Показать на карте')); await wait(2500); mark('map');
  await cap('Карта вузов: Leaflet + OpenStreetMap');
  await click(p.locator('text=Старт рядом с вузом')); await wait(1200);
  await click(p.locator('text=Идти')); await wait(4800);
  await cap('Добежали до корпуса', 'расстояние и время — по прямой, без выдуманных маршрутов'); await wait(2400);

  // 6. помощник
  await click(p.locator('.brand--btn')); await wait(1200);
  await click(p.locator('.hall__guard')); await wait(900); mark('ai');
  await cap('AI-помощник без внешних LLM', 'ищет ответ в справке, данных вуза и Положении; умеет голосом');
  await type(p.locator('.ai__form input'), 'Где деканат юридического факультета?');
  await p.keyboard.press('Enter'); await wait(3500);
  await click(p.locator('.ai__fab')); await wait(500);

  // 7. профиль
  await click(p.locator('.user-chip')); await wait(900); mark('profile');
  await click(p.locator('text=Включить 2FA')); await wait(900);
  await cap('2FA по TOTP: QR рисуется локально', 'Яндекс Ключ, Google Authenticator'); await wait(3000);
  await p.mouse.wheel(0, 900); await wait(1500);
  await cap('Значки за действия, выгрузка и удаление своих данных'); await wait(3000);

  await cap('Онлайн-деканат', 'Docker одной командой · PWA и Android · results.json в репозитории'); await wait(3500);
  mark('end');
  console.log('ERRORS', JSON.stringify(errs));
  await ctx.close(); await b.close();
})();
