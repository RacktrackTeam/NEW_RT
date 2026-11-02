import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import basicSsl from '@vitejs/plugin-basic-ssl';

export default defineConfig({
  plugins: [react(), basicSsl()],
  // VITE_* settings come from the one settings file at the repo root
  // (<repo>/.env, keys in .env.example). Only VITE_* keys reach the bundle.
  envDir: '..',
  build: {
    rollupOptions: {
      output: {
        // Bundle everything (lazy imports included) into ONE JS file. When
        // the app is served through the free ngrok tunnel, a separate
        // dynamic-import chunk request gets ngrok's browser-warning
        // interstitial (HTML) instead of the JS module, so a deferred page
        // failed to load ("Load failed"). A single bundle loads as part of
        // the page (which already cleared the interstitial), so there's no
        // separate chunk request to break.
        //
        // GATED, because it is a tunnel workaround and was being paid for
        // everywhere else: it forced every deferred page into the first
        // request, so a technician on a weak signal in a rack aisle
        // downloaded all of it to reach the login form. Set VITE_TUNNEL=1
        // when building for an ngrok demo; production and the native apps
        // get code splitting.
        inlineDynamicImports: process.env.VITE_TUNNEL === '1',
      },
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    // Allow any Host header so a public tunnel (e.g. *.trycloudflare.com) can
    // reach the dev server — otherwise Vite blocks unknown hosts.
    allowedHosts: true,
    watch: {
      usePolling: true,
      interval: 1000,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
      '/outputs': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
});
