/** @type {import('next').NextConfig} */
const nextConfig = {
  // Types are enforced: `tsc --noEmit` is the CI gate (see .github/workflows/ci.yml) and
  // `next build` also validates types now (ignoreBuildErrors was removed so the build can no
  // longer claim to check while silently ignoring type errors).
  images: {
    unoptimized: true,
  },
}

export default nextConfig
