import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <p className="text-2xl font-semibold">404</p>
      <p className="text-sm text-muted-foreground">This page does not exist.</p>
      <Link href="/" className="text-sm text-primary hover:underline">
        Back to Dashboard
      </Link>
    </div>
  );
}
