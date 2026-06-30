import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "./",
  server: {
    proxy: {
      // dance.json / lyrics live on the Python backend; proxy the JSON endpoints
      // so the dev server (5173) reaches them while assets stay served by Vite.
      "/dance/json": "http://127.0.0.1:8000",
      "/dance/lyrics": "http://127.0.0.1:8000",
    },
  },
  plugins: [
    {
      name: "reject-legacy-media-assets",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (/^\/dance\/assets\/.*\.(mp4|m4a)(?:\?|$)/.test(req.url || "")) {
            res.statusCode = 404;
            res.end("legacy media asset is not supported");
            return;
          }
          next();
        });
      },
    },
    react(),
    tailwindcss(),
  ],
});
