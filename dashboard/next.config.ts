import path from "path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pins the workspace root to this directory - without it, Turbopack finds
  // an unrelated package-lock.json in the home directory (outside this repo
  // entirely) and warns about an ambiguous root.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
