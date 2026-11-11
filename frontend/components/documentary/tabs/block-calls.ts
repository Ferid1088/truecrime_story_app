import { api } from "@/lib/api";

/** The hand reviews are one screen: the narration's (with assembling and approval), the host voice's (the host's scenes), the film opening's (its hook and context text) and the short clips' (the parts of each clip); the last three have nothing to assemble or approve. */
export type ReviewKind = "narration" | "host" | "opening" | "clips";

export function reviewCalls(kind: ReviewKind) {
  if (kind === "host") return { get: api.hostReview, open: api.openHostReview, edit: api.editHostBlock, send: api.sendHostBlock, replace: api.replaceInHostBlocks, sendAll: api.sendAllHostBlocks, activate: api.activateHostTake };
  if (kind === "clips") return { get: api.clipsReview, open: api.openClipsReview, edit: api.editClipsBlock, send: api.sendClipsBlock, replace: api.replaceInClipsBlocks, sendAll: api.sendAllClipsBlocks, activate: api.activateClipsTake };
  if (kind === "opening") return { get: api.openingReview, open: api.openOpeningReview, edit: api.editOpeningBlock, send: api.sendOpeningBlock, replace: api.replaceInOpeningBlocks, sendAll: api.sendAllOpeningBlocks, activate: api.activateOpeningTake };
  return { get: api.blockReview, open: api.openBlockReview, edit: api.editBlock, send: api.sendBlock, replace: api.replaceInBlocks, sendAll: api.sendAllBlocks, activate: api.activateTake };
}
