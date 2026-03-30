import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';

// https://vite.dev/config/
export default defineConfig({
  // The base URL for the build, adjust this to match your desired path
  plugins: [react()],

  // publicDir: '/data',

  server: {
    port: 9191,

    proxy: {
      '/api': {
        target: 'http://localhost:5656',
        changeOrigin: true,
        secure: false,
      },
      '/proxy': {
        target: 'http://localhost:5656',
        changeOrigin: true,
        secure: false,
      },
      '/output': {
        target: 'http://localhost:5656',
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        secure: false,
        ws: true,
      },
    },
  },

  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setupTests.js'],
    globals: true,
  },
});
