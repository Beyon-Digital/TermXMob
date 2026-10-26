import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const hostApi=process.env.TERMX_DEV_API||'http://127.0.0.1:8787';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':{target:hostApi,ws:true},'/auth':hostApi,'/workspace-version.json':hostApi,'/graphql':{target:hostApi,ws:true}}},build:{outDir:'dist',emptyOutDir:true},test:{maxWorkers:2,testTimeout:15000,environment:'jsdom',setupFiles:['./src/test-setup.ts']}});
