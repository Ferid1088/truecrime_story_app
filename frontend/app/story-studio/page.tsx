import { Suspense } from "react";
import { Studio } from "@/components/studio/studio";
import { Skeleton } from "@/components/ui/skeleton";

export default function StudioPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[70vh]" />}>
      <Studio />
    </Suspense>
  );
}
