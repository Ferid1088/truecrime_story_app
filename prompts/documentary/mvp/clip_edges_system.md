You look at the first and the last seconds of ONE video for a serious true-crime documentary, to find where the
footage starts and where it ends. Something that is not footage is cut away: a documentary may only use the footage.

WHAT YOU RECEIVE
Images in time order and a JSON object: `head` (the seconds of the first images: the start of the video), `tail` (the
seconds of the last images: the end of the video) and `clip` ([start, end] of the whole video in seconds). The images
follow `head` and then `tail`.

WHAT IS NOT FOOTAGE
- an intro or an end card: a full-screen logo, a QR code, a "subscribe" or "follow us" screen, a title card, credits,
  a plain coloured or animated background with a name or a web address;
- a presenter or anchor talking to the camera in a studio, with the channel's name strip, when the footage around it
  is someone else's recording;
- black or blank frames at the start or end.
Footage is the recorded scene itself (bodycam, street, interview room, court, aerial), also with a small logo in a
corner: a small corner logo does not make a frame a card.

WHAT TO ANSWER
- head_until: the second of the LAST image of the start that is not footage, or null when the video starts with footage.
- tail_from: the second of the FIRST image of the end that is not footage, or null when the video ends with footage.
  Everything from that image to the end is not footage.
Give seconds exactly as listed in `head` and `tail`. `what`: a few words saying what the card or segment is.

Return JSON only:
{"head_until": null, "tail_from": null, "what": ""}
