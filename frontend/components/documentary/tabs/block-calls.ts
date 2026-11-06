import { api } from "@/lib/api";

/** The two hand reviews are one screen: the narration's (with assembling and approval) and the host voice's (the host's scenes; nothing to assemble or approve). */
export type ReviewKind = "narration" | "host";

export function reviewCalls(kind: ReviewKind) {
  return kind === "host"
    ? { get: api.hostReview, open: api.openHostReview, edit: api.editHostBlock, send: api.sendHostBlock, replace: api.replaceInHostBlocks, sendAll: api.sendAllHostBlocks, activate: api.activateHostTake }
    : { get: api.blockReview, open: api.openBlockReview, edit: api.editBlock, send: api.sendBlock, replace: api.replaceInBlocks, sendAll: api.sendAllBlocks, activate: api.activateTake };
}
