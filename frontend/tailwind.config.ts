import type { Config } from "tailwindcss";

// Tailwind v4 is CSS-first (see src/app/globals.css's @theme inline block) —
// this file only exists for shadcn CLI's components.json content-path
// compatibility, not for theme customization.
const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
};
export default config;
