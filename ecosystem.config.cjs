// PM2 config for Obol on the home VPS (WSL). Absolute paths; run `pm2 save` after start.
//   pm2 start ecosystem.config.js && pm2 save
// obol-sidecar: always on, localhost only. obol-agent: one capped run every 6 h (cron),
// not auto-restarted, so a crash can never loop-spend.
module.exports = {
  apps: [
    {
      name: "obol-sidecar",
      cwd: "/mnt/c/Github/obol/sidecar",
      script: "node",
      args: "--env-file=.env payer.mjs",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: "obol-agent",
      cwd: "/mnt/c/Github/obol",
      script: "/mnt/c/Github/obol/.venv/bin/python",
      args: "-m obol.cli run --live --budget 0.05 --max-calls 20 --providers config/providers.mainnet.yaml --out runs/latest.json",
      interpreter: "none",
      env: { PYTHONUNBUFFERED: "1", OBOL_SIDECAR_URL: "http://127.0.0.1:8401" },
      autorestart: false,
      cron_restart: "17 */6 * * *",
    },
  ],
};
