import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Produces .next/standalone with a self-contained server.js, which is what
  // the Dockerfile copies into the runtime image.
  output: 'standalone',
  // Keep file tracing rooted here so a lockfile in a parent directory does not
  // change the standalone layout.
  outputFileTracingRoot: projectRoot,
};

export default nextConfig;
