import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import "./globals.css";
import "@/styles/modernist.css";
import { Providers } from "./providers";
import { SiteChrome } from "@/components/site-chrome";
import { WebVitalsReporter } from "@/components/web-vitals-reporter";

// Modernist's stylesheet specifies Archivo 400/600/800 via a CSS @import;
// next/font/google self-hosts and preloads the same family/weights instead
// (see src/styles/modernist.css's header comment for why).
const archivo = Archivo({
  subsets: ["latin"],
  weight: ["400", "600", "800"],
  variable: "--font-archivo",
  display: "swap",
});

export const metadata: Metadata = {
  title: "CV Tailoring",
  description: "Evidence-backed CV tailoring and cover letters.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${archivo.variable} antialiased`}>
        <WebVitalsReporter />
        <Providers>
          <SiteChrome>{children}</SiteChrome>
        </Providers>
      </body>
    </html>
  );
}
