import type { CapacitorConfig } from '@capacitor/cli'

/**
 * Андроид-приложение через Capacitor: это та же сборка интерфейса (dist), просто
 * завёрнутая в нативную оболочку. OCR и проверка всё равно крутятся на нашем сервере —
 * его адрес вшиваем при сборке: VITE_API_BASE=http://<сервер>:8000/api/v1.
 *
 *   pnpm run build:mobile          # dist с адресом сервера + cap sync
 *   pnpm exec cap open android     # открыть в Android Studio и собрать APK
 */
const config: CapacitorConfig = {
  appId: 'ru.umir.onlinedean',
  appName: 'Академиум',
  webDir: 'dist',
  android: {
    // пускаем http до сервера в локалке (демо гоняем с ноутбука)
    allowMixedContent: true,
  },
  server: {
    androidScheme: 'https',
    cleartext: true,
  },
}

export default config
