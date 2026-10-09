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
  webpack: (config) => {
    // noVNC uses top-level await. Every browser this app targets supports
    // async functions, so tell webpack rather than have it warn on each build.
    config.output.environment = { ...(config.output.environment || {}), asyncFunction: true };
    return config;
  },
};

export default nextConfig;
