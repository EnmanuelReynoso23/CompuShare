import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    allowedHosts: true,
  },
  worker: {
    // WebLLM's worker uses ES module imports; without this the production
    // build would bundle it as an IIFE and break at runtime.
    format: 'es',
  },
})
