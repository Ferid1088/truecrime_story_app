import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async redirects() {
    return [
      // /story-studio is canonical (it matches the sidebar label "Story
      // Studio"); /studio was the old path and 404s unless redirected.
      { source: "/studio", destination: "/story-studio", permanent: false },
    ];
  },
};

export default nextConfig;
