/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained production server for Docker (Gate D2): `next build` emits `.next/standalone`
  // (a minimal server.js + only the traced node_modules), so the runtime image needs no dev deps
  // and no full node_modules. Compatible with `next dev` (output is a build-only concern) — local
  // `npm run dev` is unchanged.
  output: "standalone",
  // Types are enforced: `tsc --noEmit` is the CI gate (see .github/workflows/ci.yml) and
  // `next build` also validates types now (ignoreBuildErrors was removed so the build can no
  // longer claim to check while silently ignoring type errors).
  images: {
    unoptimized: true,
  },
}

export default nextConfig
