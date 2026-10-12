/*
 * Service worker нашего «Онлайн-деканата» (PWA).
 *
 * Без сети у нас открываются оболочка, холл, справка по факультетам и карта
 * вузов (что успели загрузить в последний раз). Проверка документов и вход без
 * сервера никак: POST-запросы и /api/auth/* не кешируем вообще никогда. Тайлы карты
 * OpenStreetMap тоже не кешируем — у них в правилах это запрещено, не нарываемся.
 */
const VERSION = 'umir-v2'
const SHELL = `${VERSION}-shell`
const DATA = `${VERSION}-data`
const PRECACHE = [
  '/',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/brand/academium-mark.webp',
  '/brand/academium-mark-128.webp',
  '/brand/doodles.svg',
  '/universities/rinh/building.svg',
  '/universities/rinh/hall.svg',
  '/universities/rinh/logo.svg',
  '/universities/sfedu/logo.svg',
  '/universities/_default/building.svg',
  '/universities/_default/hall.svg',
]
// Это можно показать и протухшим: обновится, как только снова появится сеть.
const DATA_PATHS = [/^\/api\/app-config$/, /^\/api\/universities(\/[\w-]+)?$/, /^\/api\/regulation$/, /^\/api\/v1\/document-types$/]

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => !key.startsWith(VERSION)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

async function networkFirst(request, cacheName, fallbackUrl) {
  const cache = await caches.open(cacheName)
  try {
    const response = await fetch(request)
    if (response.ok) cache.put(fallbackUrl ?? request, response.clone())
    return response
  } catch (error) {
    const cached = (await cache.match(fallbackUrl ?? request)) ?? (await caches.match(fallbackUrl ?? request))
    if (cached) return cached
    throw error
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request)
  if (cached) return cached
  const response = await fetch(request)
  if (response.ok) (await caches.open(SHELL)).put(request, response.clone())
  return response
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, SHELL, '/'))
    return
  }
  if (url.pathname.startsWith('/api/')) {
    if (DATA_PATHS.some((re) => re.test(url.pathname))) event.respondWith(networkFirst(request, DATA))
    return
  }
  if (['/assets/', '/universities/', '/icons/', '/brand/'].some((prefix) => url.pathname.startsWith(prefix))) {
    event.respondWith(cacheFirst(request))
  }
})
