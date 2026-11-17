import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { port: 3000, host: true }, // purana port 3000, to CORS aur docker-compose same rahenge
  preview: { port: 3000, host: true },
});