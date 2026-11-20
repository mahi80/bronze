import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Medallion console",
  description: "Run the ticket pipeline, see what each run did, and approve what the agents propose.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
