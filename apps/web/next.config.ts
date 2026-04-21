import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Allow connections from any origin in dev (LAN IPs, Lightsail, etc.)
  allowedDevOrigins: ["172.17.160.1", "localhost", "staging.lexai.zuarione.com"],
};

export default nextConfig;
