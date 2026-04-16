import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Allow connections from any origin in dev (LAN IPs, Lightsail, etc.)
  allowedDevOrigins: ["*"],
};

export default nextConfig;
