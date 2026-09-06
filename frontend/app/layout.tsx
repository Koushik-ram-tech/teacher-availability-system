import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Teacher Availability System",
  description: "Department timetable and faculty availability prototype",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
