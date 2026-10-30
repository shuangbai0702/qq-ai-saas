import {defineConfig} from 'vite';import react from '@vitejs/plugin-react';import path from 'node:path';
export default defineConfig({root:path.resolve('apps/web'),plugins:[react()],server:{host:'127.0.0.1',port:5173,strictPort:true,proxy:{'/v1':'http://127.0.0.1:8787'}},build:{outDir:'dist',emptyOutDir:true}});
