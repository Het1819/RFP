import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    outDir: resolve(__dirname, 'app/static/dist'),
    emptyOutDir: true,
    minify: true,
    rollupOptions: {
      input: {
        marketing: resolve(__dirname, 'app/frontend/marketing.ts'),
        app: resolve(__dirname, 'app/frontend/app.ts'),
      },
      output: {
        entryFileNames: '[name].js',
        assetFileNames: '[name].[ext]',
        chunkFileNames: '[name].js',
      },
    },
  },
});
