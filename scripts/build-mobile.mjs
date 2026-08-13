import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const output = path.join(root, 'www')
const apiOrigin = String(process.env.UPLINK_API_ORIGIN || '').replace(/\/$/, '')

if (!/^https:\/\/[^/]+/.test(apiOrigin)) {
  throw new Error('Set UPLINK_API_ORIGIN to the production HTTPS origin before building the mobile app')
}

await rm(output, { recursive: true, force: true })
await mkdir(path.join(output, 'static'), { recursive: true })
await cp(path.join(root, 'static'), path.join(output, 'static'), { recursive: true })
await rm(path.join(output, 'static', 'index.html'), { force: true })
await cp(path.join(root, 'static', 'manifest.webmanifest'), path.join(output, 'manifest.webmanifest'))
await cp(path.join(root, 'static', 'service-worker.js'), path.join(output, 'service-worker.js'))
await cp(path.join(root, 'node_modules', '@capacitor', 'core', 'dist'),
  path.join(output, 'vendor', 'capacitor-core'), { recursive: true })
await cp(path.join(root, 'node_modules', '@aparajita', 'capacitor-secure-storage', 'dist', 'esm'),
  path.join(output, 'vendor', 'secure-storage'), { recursive: true })

let html = await readFile(path.join(root, 'static', 'index.html'), 'utf8')
const imports = JSON.stringify({ imports: {
  '@capacitor/core': './vendor/capacitor-core/index.js',
  '@aparajita/capacitor-secure-storage': './vendor/secure-storage/index.js',
} })
html = html.replace('</head>', `<script type="importmap">${imports}</script>\n<script type="module" src="./native-bridge.js"></script>\n</head>`)
await writeFile(path.join(output, 'index.html'), html, 'utf8')

let bridge = await readFile(path.join(root, 'mobile', 'native-bridge.js'), 'utf8')
bridge = bridge.replace('__UPLINK_API_ORIGIN__', JSON.stringify(apiOrigin))
await writeFile(path.join(output, 'native-bridge.js'), bridge, 'utf8')

console.log(`Built native web bundle for ${apiOrigin}`)
