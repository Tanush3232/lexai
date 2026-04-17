import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Allow connections from any origin in dev (LAN IPs, Lightsail, etc.)
  allowedDevOrigins: ["192.168.65.2", "localhost"],
};

export default nextConfig;
