import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The desktop edition serves these prebuilt files with the Python API.
  // Normal source-tree development keeps its existing Next server.
  ...(process.env.IEI_DESKTOP_EXPORT === "1" ? { output: "export" as const } : {}),
  // Next 16.3 writes AGENTS.md/CLAUDE.md into the project whenever `next
  // dev` detects an AI-agent environment; those files would then be
  // ingested as project instructions by future agent sessions with
  // content controlled by the next package. This is a clinical tool —
  // keep the working tree exactly what the repository ships.
  agentRules: false,
};

export default nextConfig;
