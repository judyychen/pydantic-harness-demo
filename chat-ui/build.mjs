import {build} from 'esbuild';
await build({entryPoints:['chat.jsx'],bundle:true,format:'esm',outfile:'dist/chat.js',minify:true,jsx:'automatic',loader:{'.woff2':'file','.woff':'file','.ttf':'file'},assetNames:'[name]-[hash]',define:{'process.env.NODE_ENV':'"development"','process.env.COPILOTKIT_TELEMETRY_DISABLED':'"true"'},logLevel:'info'});
