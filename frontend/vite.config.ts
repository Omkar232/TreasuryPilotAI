import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig as defineVitestConfig } from 'vitest/config'
export default defineVitestConfig({ plugins: [react(), tailwindcss()], test: { environment: 'jsdom', setupFiles: './src/test-setup.ts', exclude: ['e2e/**', '**/node_modules/**'] } })
