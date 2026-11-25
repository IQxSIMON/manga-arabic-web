import { defineConfig } from 'vite'

// The real app is the Flask backend on port 5000; the Vite dev server only
// exists so the managed preview can reach it through the public tunnel.
export default defineConfig({
  server: {
    host: true,
    allowedHosts: true,
    proxy: {
      '^/': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true,
      },
    },
  },
})
