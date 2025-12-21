import type { PlaywrightTestConfig } from '@playwright/test'

const config: PlaywrightTestConfig = {
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  webServer: {
    command: 'npx cross-env NEXT_DISABLE_TURBOPACK=1 PORT=3004 npm run dev',
    url: 'http://localhost:3004',
    reuseExistingServer: false,
    timeout: 180_000,
  },
  use: {
    baseURL: 'http://localhost:3004',
    headless: true,
    viewport: { width: 1280, height: 720 },
    trace: 'on-first-retry',
  },
}

export default config
