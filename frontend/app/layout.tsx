import type { Metadata } from "next";
import { Geist, Geist_Mono, Vazirmatn } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { Sidebar } from "@/components/sidebar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const vazirmatn = Vazirmatn({
  variable: "--font-rtl",
  subsets: ["arabic"],
});

export const metadata: Metadata = {
  title: "TrueCrime Story Studio",
  description: "Research → Facts → Contradictions → Story Direction → Writing → Critique",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} ${vazirmatn.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <Providers>
          <Sidebar />
          <main className="min-h-screen md:ml-56">
            <div className="mx-auto max-w-6xl px-4 py-4 sm:px-6 sm:py-6">{children}</div>
          </main>
        </Providers>
      </body>
    </html>
  );
}
