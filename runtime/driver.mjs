// Sandbox computer driver.
//
// Runs inside the runtime container on an Xvnc display (see start.sh). It
// exposes a token-protected HTTP API for the host API: navigate the bot's
// browser, capture the whole desktop, inject pointer/keyboard input into the
// desktop, run shell commands, list files, and bridge the local VNC server
// over a WebSocket so people can watch or take control of the same desktop.

import net from 'node:net';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createServer } from 'node:http';
import { mkdir, readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';
import { WebSocketServer } from 'ws';

const execFileAsync = promisify(execFile);
const port = Number(process.env.PORT || 3000);
const token = process.env.COMPUTER_TOKEN || '';
const workspace = path.resolve(process.env.WORKSPACE || '/workspace');
const computerId = process.env.COMPUTER_ID || 'computer-runtime';
const width = Number(process.env.VIEWPORT_WIDTH || 1280);
const height = Number(process.env.VIEWPORT_HEIGHT || 720);
const display = process.env.DISPLAY || ':1';
const profileDir = '/tmp/profile';
const vncPort = 5900;
const PANEL_HEIGHT = 40;

let context;
let page;
let server;
let queue = Promise.resolve();

const desktopEnv = {
  PATH: process.env.PATH || '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
  HOME: process.env.HOME || '/home/pwuser',
  DISPLAY: display,
  LANG: 'C.UTF-8',
  DBUS_SESSION_BUS_ADDRESS: process.env.DBUS_SESSION_BUS_ADDRESS || '',
  XDG_RUNTIME_DIR: process.env.XDG_RUNTIME_DIR || '',
};

function json(response, statusCode, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(statusCode, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
  });
  response.end(body);
}

function errorPayload(error) {
  return { error: error instanceof Error ? error.message : String(error) };
}

function authorized(request) {
  return Boolean(token) && request.headers['x-computer-token'] === token;
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (!chunks.length) return {};
  const raw = Buffer.concat(chunks).toString('utf8');
  if (raw.length > 1024 * 1024) throw new Error('Request body is too large.');
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error('Request body must be valid JSON.');
  }
}

function enqueue(operation) {
  const next = queue.then(operation, operation);
  queue = next.catch(() => {});
  return next;
}

function assertHttpUrl(value) {
  if (typeof value !== 'string' || value.length > 2048) {
    throw new Error('A browser URL is required and must be at most 2048 characters.');
  }
  const parsed = new URL(value);
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error('Browser navigation only accepts absolute HTTP(S) URLs.');
  }
  return parsed.toString();
}

function workspacePath(value) {
  const requested = typeof value === 'string' && value.trim() ? value.trim() : '/workspace';
  if (requested.length > 512 || !requested.startsWith('/')) {
    throw new Error('Computer file paths must be absolute and at most 512 characters.');
  }
  const resolved = path.resolve(requested);
  const relative = path.relative(workspace, resolved);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error('Computer file paths must stay inside /workspace.');
  }
  return resolved;
}

async function listFiles(requestedPath) {
  const target = workspacePath(requestedPath);
  const entries = await readdir(target, { withFileTypes: true });
  return entries.slice(0, 500).map((entry) => ({
    name: entry.name,
    kind: entry.isDirectory() ? 'directory' : entry.isFile() ? 'file' : 'other',
  }));
}

async function executeCommand(command) {
  if (typeof command !== 'string' || !command.trim()) throw new Error('A terminal command is required.');
  if (command.length > 4000) throw new Error('Terminal commands must be at most 4000 characters.');
  try {
    const result = await execFileAsync('/bin/sh', ['-lc', command], {
      cwd: workspace,
      timeout: 30000,
      maxBuffer: 1024 * 1024,
      env: desktopEnv,
    });
    return { exit_code: 0, stdout: result.stdout, stderr: result.stderr };
  } catch (error) {
    return {
      exit_code: typeof error.code === 'number' ? error.code : 1,
      stdout: error.stdout || '',
      stderr: error.stderr || error.message,
    };
  }
}

// ---- desktop capture and input ---------------------------------------------

async function captureDesktop() {
  const file = '/tmp/shot.jpg';
  await execFileAsync('scrot', ['--overwrite', '--quality', '72', file], { env: desktopEnv, timeout: 10000 });
  const data = await readFile(file);
  return data.toString('base64');
}

async function xdotool(args) {
  await execFileAsync('xdotool', args, { env: desktopEnv, timeout: 15000 });
}

// Browser-style key names (what the model and the UI tend to use) to X keysyms.
const KEY_MAP = {
  Enter: 'Return',
  Return: 'Return',
  ArrowUp: 'Up',
  ArrowDown: 'Down',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  Backspace: 'BackSpace',
  Escape: 'Escape',
  Esc: 'Escape',
  Tab: 'Tab',
  Delete: 'Delete',
  Home: 'Home',
  End: 'End',
  PageUp: 'Prior',
  PageDown: 'Next',
  Space: 'space',
  ' ': 'space',
  Control: 'ctrl',
  Ctrl: 'ctrl',
  Shift: 'shift',
  Alt: 'alt',
  Meta: 'super',
  Super: 'super',
};

function keyCombo(value) {
  const raw = String(value || '');
  if (!raw || raw.length > 40 || !/^[A-Za-z0-9+_\- ]+$/.test(raw)) {
    throw new Error('A key name such as Enter, Tab, ArrowDown, or ctrl+c is required.');
  }
  return raw
    .split('+')
    .map((part) => KEY_MAP[part] || KEY_MAP[part.trim()] || (part.length === 1 ? part : part.charAt(0).toUpperCase() + part.slice(1)))
    .join('+');
}

function coordinate(value, max, label) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`Input ${label} must be a number.`);
  return Math.max(0, Math.min(max - 1, Math.round(number)));
}

async function sendInput(event) {
  if (!event || typeof event !== 'object') throw new Error('Computer input must be a JSON object.');
  const type = String(event.type || '').toLowerCase();
  if (type === 'click') {
    const x = coordinate(event.x, width, 'x');
    const y = coordinate(event.y, height, 'y');
    const button = { left: '1', middle: '2', right: '3' }[event.button || 'left'];
    if (!button) throw new Error('Click button must be left, middle, or right.');
    await xdotool(['mousemove', '--sync', String(x), String(y), 'click', button]);
    return { x, y, button: event.button || 'left' };
  }
  if (type === 'keypress') {
    const combo = keyCombo(event.key);
    await xdotool(['key', '--clearmodifiers', combo]);
    return { key: combo };
  }
  if (type === 'type') {
    const text = String(event.text || '');
    if (!text) throw new Error('Typed input requires text.');
    if (text.length > 2000) throw new Error('Typed input must be at most 2000 characters.');
    await xdotool(['type', '--delay', '12', '--', text]);
    return { characters: text.length };
  }
  if (type === 'scroll') {
    const deltaY = Number(event.deltaY || 0);
    const deltaX = Number(event.deltaX || 0);
    const steps = (delta) => Math.min(20, Math.max(1, Math.round(Math.abs(delta) / 100)));
    if (deltaY) await xdotool(['click', '--repeat', String(steps(deltaY)), '--delay', '20', deltaY > 0 ? '5' : '4']);
    if (deltaX) await xdotool(['click', '--repeat', String(steps(deltaX)), '--delay', '20', deltaX > 0 ? '7' : '6']);
    return { deltaX, deltaY };
  }
  throw new Error('Supported input types are click, keypress, type, and scroll.');
}

// ---- browser ----------------------------------------------------------------

function trackPages() {
  const pages = context.pages();
  page = pages[pages.length - 1];
  context.on('page', (opened) => {
    page = opened;
    opened.on('close', () => {
      const remaining = context.pages();
      page = remaining[remaining.length - 1] || null;
    });
  });
}

async function ensurePage() {
  if (!page || page.isClosed()) {
    page = context.pages()[0] || (await context.newPage());
  }
  return page;
}

// ---- HTTP API ------------------------------------------------------------------

async function handle(request, response) {
  if (!authorized(request)) {
    json(response, 401, { error: 'Computer runtime authentication failed.' });
    return;
  }

  try {
    const body = request.method === 'POST' ? await readBody(request) : {};
    if (request.method === 'GET' && request.url === '/health') {
      json(response, 200, {
        status: 'healthy',
        computer_id: computerId,
        url: page && !page.isClosed() ? page.url() : 'about:blank',
        width,
        height,
        desktop: true,
        vnc: true,
      });
      return;
    }

    const result = await enqueue(async () => {
      if (request.method === 'POST' && request.url === '/navigate') {
        const url = assertHttpUrl(body.url);
        const target = await ensurePage();
        await target.bringToFront();
        await target.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        return { operation: 'browser.navigate', url: target.url(), title: await target.title() };
      }

      if (request.method === 'POST' && request.url === '/screenshot') {
        return {
          operation: 'screenshot',
          format: 'jpeg',
          width,
          height,
          url: page && !page.isClosed() ? page.url() : null,
          frame_id: `frame-${Date.now()}`,
          data: await captureDesktop(),
        };
      }

      if (request.method === 'POST' && request.url === '/terminal') {
        return { operation: 'terminal.exec', ...(await executeCommand(body.command)) };
      }

      if (request.method === 'POST' && request.url === '/files') {
        return {
          operation: 'files.list',
          path: workspacePath(body.path),
          entries: await listFiles(body.path),
        };
      }

      if (request.method === 'POST' && request.url === '/input') {
        const detail = await sendInput(body.event);
        return { operation: 'input', accepted: true, type: String(body.event.type).toLowerCase(), ...detail };
      }

      if (request.method === 'GET' && request.url === '/state') {
        const current = page && !page.isClosed() ? page : null;
        return { operation: 'state', url: current ? current.url() : null, title: current ? await current.title() : null };
      }

      throw new Error('Runtime route not found.');
    });
    json(response, 200, { computer_id: computerId, ...result });
  } catch (error) {
    json(response, 400, errorPayload(error));
  }
}

// ---- VNC bridge ---------------------------------------------------------------
// GET /vnc with the token upgrades to a WebSocket carrying raw RFB bytes to
// the loopback VNC server. noVNC in the browser speaks exactly this, and the
// host API relays it after checking the user's session.

const wss = new WebSocketServer({ noServer: true, maxPayload: 16 * 1024 * 1024 });

function bridgeVnc(socket) {
  const tcp = net.connect(vncPort, '127.0.0.1');
  tcp.on('data', (chunk) => {
    if (socket.readyState === socket.OPEN) socket.send(chunk);
  });
  tcp.on('close', () => socket.close());
  tcp.on('error', () => socket.close());
  socket.on('message', (message) => {
    if (!tcp.destroyed) tcp.write(message);
  });
  socket.on('close', () => tcp.destroy());
  socket.on('error', () => tcp.destroy());
}

function handleUpgrade(request, socket, head) {
  const url = (request.url || '').split('?')[0];
  if (!authorized(request) || url !== '/vnc') {
    socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
    socket.destroy();
    return;
  }
  wss.handleUpgrade(request, socket, head, (ws) => bridgeVnc(ws));
}

// ---- lifecycle -----------------------------------------------------------------

async function main() {
  if (!token) throw new Error('COMPUTER_TOKEN is required.');
  await mkdir(workspace, { recursive: true });
  await stat(workspace);

  context = await chromium.launchPersistentContext(profileDir, {
    headless: false,
    viewport: null,
    ignoreDefaultArgs: ['--enable-automation'],
    args: [
      `--window-size=${width},${height - PANEL_HEIGHT}`,
      '--window-position=0,0',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-infobars',
      '--disable-session-crashed-bubble',
    ],
    env: { ...process.env, DISPLAY: display },
  });
  trackPages();
  await (await ensurePage()).goto('about:blank');

  server = createServer((request, response) => handle(request, response));
  server.on('upgrade', handleUpgrade);
  server.listen(port, '0.0.0.0', () => {
    console.log(JSON.stringify({ ready: true, computer_id: computerId, port, width, height, desktop: true }));
  });
}

async function shutdown() {
  if (server) await new Promise((resolve) => server.close(resolve));
  if (context) await context.close().catch(() => {});
  process.exit(0);
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
