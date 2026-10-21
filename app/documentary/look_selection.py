"""From the stage director's plan to one approved look of the video's outfit.

The director plans a stance (seated or standing), a camera and a framing from the words, the role
and the place of the scene. The app maps that plan to the best APPROVED look of the selected
outfit: never a look of another outfit, never one of another language's studio. When the exact
look does not exist it takes the nearest approved one and says why; when the outfit has no approved
look at all it raises with exactly what the owner has to approve. Neighbouring scenes may not
differ in how the host looks by more than host_looks.continuity.max_change_per_cut without a reason."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import HostLook, HostOutfit
from app.documentary import studio as ST

# Framings that are plain crops of an approved front or wide picture: they need no picture of their
# own (the room never changes).
CROPS = {"WIDE": ("MEDIUM", "MEDIUM_CLOSE", "CLOSE"), "MEDIUM": ("MEDIUM_CLOSE", "CLOSE")}
CROP_CAMERAS = ("CAM01_FRONT", "CAM06_WIDE")


class LookSelectionError(RuntimeError):
    pass


class NoApprovedLook(LookSelectionError):
    """The outfit has no approved look (at all, or none the plan can use)."""

    def __init__(self, outfit: HostOutfit, to_approve: list[str]):
        self.outfit, self.to_approve = outfit, to_approve
        super().__init__(
            f"outfit {outfit.id} ({outfit.name}, {outfit.language}) has no approved look: "
            + ("; ".join(to_approve) if to_approve else "request its pose set and approve its looks"))


@dataclass(frozen=True)
class Wanted:
    stance: str
    camera: str
    framing: str


def distance(a: Wanted, b: Wanted) -> int:
    """How different two ways of looking are: a change of stance, plus the steps between cameras
    and between framings."""
    cont = ai_config.host_looks.continuity
    cams, framings = cont.camera_position, cont.framing_order
    return ((cont.stance_weight if a.stance != b.stance else 0)
            + abs(cams.get(a.camera, 0) - cams.get(b.camera, 0))
            + abs(framings.index(a.framing) - framings.index(b.framing)))


def of_look(look: HostLook) -> Wanted:
    return Wanted(look.stance, look.camera, look.framing)


def approved_looks(db: Session, outfit: HostOutfit) -> list[HostLook]:
    """The approved looks of THIS outfit in the studio of the outfit's own language."""
    profile = ST.channel_profile(outfit.language)["studio_profile_id"]
    return (db.query(HostLook)
            .filter(HostLook.host_outfit_id == outfit.id, HostLook.status == "approved",
                    HostLook.language == outfit.language, HostLook.studio_profile_id == profile)
            .order_by(HostLook.id).all())


def usable_plans(db: Session, outfit: HostOutfit) -> set[tuple[str, str, str]]:
    """(stance, camera, framing) the director may plan: the approved looks, plus the plain crops of
    an approved front or wide picture."""
    out: set[tuple[str, str, str]] = set()
    for look in approved_looks(db, outfit):
        out.add((look.stance, look.camera, look.framing))
        if look.camera in CROP_CAMERAS:
            out.update((look.stance, look.camera, f) for f in CROPS.get(look.framing, ()))
    return out


def missing_looks(db: Session, outfit: HostOutfit) -> list[str]:
    """What the owner has to approve: the standard set's looks that are not approved yet."""
    have = {(lk.camera, lk.framing, lk.stance): lk for lk in db.query(HostLook).filter(
        HostLook.host_outfit_id == outfit.id, HostLook.status != "rejected")}
    out = []
    for entry in ai_config.host_looks.pose_set:
        look = have.get((entry.camera, entry.framing, entry.stance))
        if look is None:
            out.append(f"request {entry.label}")
        elif look.status != "approved":
            out.append(f"look #{look.id} ({entry.label}) is {look.status}: "
                       + ("generate it, then approve it" if look.status == "requested"
                          else "approve it"))
    return out


def choose(db: Session, outfit: HostOutfit, wanted: Wanted, previous: HostLook | None = None,
           change_reason: str | None = None) -> dict:
    """The look for a plan: {"look": HostLook, "choice": {...why...}}."""
    looks = approved_looks(db, outfit)
    if not looks:
        raise NoApprovedLook(outfit, missing_looks(db, outfit))
    cont = ai_config.host_looks.continuity
    ranked = sorted(looks, key=lambda lk: (distance(wanted, of_look(lk)), lk.id))
    exact = next((lk for lk in ranked if of_look(lk) == wanted), None)
    cropped = None
    if exact is None:                                   # a crop of a front or wide picture
        for look in ranked:
            if (look.stance == wanted.stance and look.camera in CROP_CAMERAS
                    and wanted.camera in CROP_CAMERAS
                    and wanted.framing in CROPS.get(look.framing, ())):
                exact = cropped = look
                break
    pick = exact or ranked[0]
    why = ("the planned look" if exact is not None and cropped is None else
           f"a {wanted.framing} crop of the approved {pick.framing} picture" if cropped
           else f"the planned {wanted.stance}/{wanted.camera}/{wanted.framing} is not approved; "
                f"the nearest approved look is {pick.stance}/{pick.camera}/{pick.framing}")
    note = None
    if previous is not None and previous.id != pick.id:
        jump = distance(of_look(previous), of_look(pick))
        if jump > cont.max_change_per_cut and not (change_reason or "").strip():
            near = [lk for lk in ranked if distance(of_look(previous), of_look(lk))
                    <= cont.max_change_per_cut]
            if near:
                note = (f"the planned change from look #{previous.id} to #{pick.id} is {jump} "
                        f"(more than {cont.max_change_per_cut}) and has no reason")
                pick = near[0]
                why = (f"{note}: the nearest allowed approved look is "
                       f"{pick.stance}/{pick.camera}/{pick.framing}")
                cropped = None
    return {"look": pick, "choice": {
        "look_id": pick.id, "wanted": wanted.__dict__,
        "chosen": {"stance": pick.stance, "camera": pick.camera, "framing": pick.framing},
        "exact": exact is not None and pick is exact, "cropped_from_framing": pick.framing
        if cropped is not None and pick is cropped else None,
        "why": why, "continuity": note,
        "previous_look_id": previous.id if previous else None,
        "considered": [lk.id for lk in looks]}}


def _fits(look: HostLook, wanted: Wanted) -> bool:
    """The look the scene may legally use (the stage director's plan first): the exact
    stance/camera/framing, or a wider front/wide picture the wanted framing is a crop of."""
    if of_look(look) == wanted:
        return True
    return (look.stance == wanted.stance and look.camera in CROP_CAMERAS
            and wanted.camera in CROP_CAMERAS and wanted.framing in CROPS.get(look.framing, ()))


def choose_appearance(db: Session, outfit: HostOutfit, wanted: Wanted, appearance_index: int,
                      previous: HostLook | None = None,
                      change_reason: str | None = None) -> dict:
    """O2: the look of the video's n-th host appearance. The owner's fixed picture
    (checkbox C / "same picture") is used for every appearance when set; otherwise the
    approved looks that fit the plan rotate in their stored order — appearance n takes
    picture n — never the same one twice in a row when at least two fit. The continuity
    limit (max_change_per_cut) is unchanged."""
    looks = approved_looks(db, outfit)
    if not looks:
        raise NoApprovedLook(outfit, missing_looks(db, outfit))
    by_id = {lk.id: lk for lk in looks}
    fixed = by_id.get(outfit.fixed_look_id or 0)
    if fixed is not None:
        return {"look": fixed, "choice": {
            "look_id": fixed.id, "wanted": wanted.__dict__, "fixed": True,
            "chosen": {"stance": fixed.stance, "camera": fixed.camera,
                       "framing": fixed.framing},
            "why": "the fixed picture chosen in the Outfits panel",
            "previous_look_id": previous.id if previous else None,
            "considered": [lk.id for lk in looks]}}
    ranked = sorted(looks, key=lambda lk: (distance(wanted, of_look(lk)), lk.id))
    fitting = [lk for lk in ranked if _fits(lk, wanted)] or [ranked[0]]
    i = appearance_index % len(fitting)
    pick = fitting[i]
    if len(fitting) > 1 and previous is not None and pick.id == previous.id:
        pick = fitting[(i + 1) % len(fitting)]
    why = ("the planned look, in rotation" if len(fitting) > 1
           else ("the planned look" if of_look(pick) == wanted
                 else f"a {wanted.framing} crop of the approved {pick.framing} picture"
                 if _fits(pick, wanted) and of_look(pick) != wanted
                 else f"the planned {wanted.stance}/{wanted.camera}/{wanted.framing} is not "
                      f"approved; the nearest approved look is "
                      f"{pick.stance}/{pick.camera}/{pick.framing}"))
    note = None
    if previous is not None and previous.id != pick.id:
        cont = ai_config.host_looks.continuity
        jump = distance(of_look(previous), of_look(pick))
        if jump > cont.max_change_per_cut and not (change_reason or "").strip():
            near = [lk for lk in ranked if distance(of_look(previous), of_look(lk))
                    <= cont.max_change_per_cut]
            if near:
                note = (f"the planned change from look #{previous.id} to #{pick.id} is {jump} "
                        f"(more than {cont.max_change_per_cut}) and has no reason")
                pick = near[0]
                why = (f"{note}: the nearest allowed approved look is "
                       f"{pick.stance}/{pick.camera}/{pick.framing}")
    return {"look": pick, "choice": {
        "look_id": pick.id, "wanted": wanted.__dict__,
        "chosen": {"stance": pick.stance, "camera": pick.camera, "framing": pick.framing},
        "exact": of_look(pick) == wanted,
        "cropped_from_framing": pick.framing
        if of_look(pick) != wanted and _fits(pick, wanted) else None,
        "rotation": {"appearance": appearance_index, "fitting": [lk.id for lk in fitting]},
        "fixed_missing": outfit.fixed_look_id is not None,
        "why": why, "continuity": note,
        "previous_look_id": previous.id if previous else None,
        "considered": [lk.id for lk in looks]}}
