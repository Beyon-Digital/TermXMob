import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':{target:'http://127.0.0.1:8787',ws:true},'/auth':'http://127.0.0.1:8787','/graphql':{target:'http://127.0.0.1:8787',ws:true}}},build:{outDir:'dist',emptyOutDir:true},test:{maxWorkers:2,testTimeout:15000,environment:'jsdom',setupFiles:['./src/test-setup.ts']}});
