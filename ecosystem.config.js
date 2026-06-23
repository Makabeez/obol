// PM2 process config for Obol on the home VPS (WSL Ubuntu).
// Use absolute paths. Stale-config fix if needed:
//   pm2 delete obol-dashboard obol-agent; rm ~/.pm2/dump.pm2; pm2 save --force
//   pm2 start ecosystem.config.js
//
// Adjust `cwd` to your checkout. On WSL the Windows path C:\Github\obol maps to
// /mnt/c/Github/obol -- but run from a native Linux path if you hit file-watch issues.

module.exports = {
  apps: [
    {
      name: "obol-sidecar",
      cwd: "/mnt/c/Github/obol/sidecar",
      script: "node",
      args: "--env-file=.env payer.mjs",
      interpreter: "none",
      env: { PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: "obol-dashboard",
      cwd: "/mnt/c/Github/obol",
      script: "python",
      args: "-m uvicorn dashboard.server:app --host 0.0.0.0 --port 8099",
      interpreter: "none",
      env: { PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: "obol-sidecar",
      cwd: "/mnt/c/Github/obol/sidecar",
      script: "node",
      args: "--env-file=.env payer.mjs",
      interpreter: "none",
      env: { PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_restarts: 10,
    },
  ],
};
