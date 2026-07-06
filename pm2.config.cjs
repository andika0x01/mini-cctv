module.exports = {
  apps: [
    {
      name: "cctv-backend",
      cwd: "./backend",
      script: "uv",
      args: "run uvicorn main:app --host 0.0.0.0 --port 80"
    }
  ]
};
