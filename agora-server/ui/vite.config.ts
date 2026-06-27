import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "./",
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
