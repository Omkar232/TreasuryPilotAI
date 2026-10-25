import { defineConfig, devices } from '@playwright/test'
import { randomUUID } from 'node:crypto'

const backendPort = process.env.E2E_BACKEND_PORT || '18000'
const frontendPort = process.env.E2E_FRONTEND_PORT || '18001'
const backendURL = `http://127.0.0.1:${backendPort}`
const frontendURL = `http://127.0.0.1:${frontendPort}`

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  reporter: 'list',
  use: { baseURL: frontendURL, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: '../backend',
      url: `${backendURL}/api/health`,
      env: { APP_ENV: 'development', APP_API_TOKEN: 'e2e-local-token', DATABASE_URL: `sqlite:///./e2e-${randomUUID()}.db`, FRONTEND_ORIGINS: frontendURL, AIRWALLEX_CLIENT_ID: '', AIRWALLEX_API_KEY: '', AI_PROVIDER: '', AI_API_KEY: '' },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `./node_modules/.bin/vite --host 127.0.0.1 --port ${frontendPort}`,
      url: frontendURL,
      env: { VITE_API_URL: backendURL },
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})
