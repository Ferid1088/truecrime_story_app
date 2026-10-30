import { ReviewQueuePage } from "@/components/documentary/review-queue";

export default async function DocumentaryReviewPage({ params, searchParams }: PageProps<"/documentary/[caseId]/review">) {
  const { caseId } = await params;
  const { portraits } = await searchParams;
  const id = Number(caseId);
  if (!Number.isInteger(id) || id <= 0) {
    return <p className="text-sm text-muted-foreground">Invalid case id.</p>;
  }
  return <ReviewQueuePage caseId={id} portraits={portraits === "1"} />;
}
