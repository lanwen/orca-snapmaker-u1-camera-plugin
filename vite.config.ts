import { readFileSync } from 'node:fs';
import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [
    svelte(),
    {
      name: 'self-contained-camera',
      enforce: 'post',
      generateBundle(_options, bundle) {
        const files = Object.values(bundle);
        const script = files.find((file) => file.type === 'chunk');
        const style = files.find((file) => file.type === 'asset' && file.fileName.endsWith('.css'));
        if (
          !script ||
          script.type !== 'chunk' ||
          files.length !== 2 ||
          !style ||
          style.type !== 'asset'
        )
          throw new Error('The camera must build to one script and one stylesheet.');
        const template = readFileSync('src/camera.html', 'utf8');
        const page = template
          .replace('<!-- CAMERA_STYLE -->', () => `<style>${style.source}</style>`)
          .replace(
            '<!-- CAMERA_SCRIPT -->',
            () => `<script>${script.code.replaceAll('</script', '<\\/script')}</script>`,
          );
        for (const name of Object.keys(bundle)) delete bundle[name];
        this.emitFile({ type: 'asset', fileName: 'camera.html', source: page });
      },
    },
  ],
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  build: {
    outDir: 'dist/ui',
    target: ['chrome80', 'firefox78', 'safari14'],
    lib: { entry: 'src/ui/main.ts', name: 'U1Camera', formats: ['iife'] },
  },
  test: { environment: 'jsdom', include: ['tests/**/*.test.ts'] },
});
