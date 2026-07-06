import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  server: {
    host: true,
    port: 5000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:80",
        changeOrigin: true,
        ws: true
      },
    },
  },
  resolve: {
    tsconfigPaths: true,
  },
});
