"""The hand-motion measurement of a finished avatar video."""
import subprocess

import pytest

from app.core.media_tools import ffmpeg_bin
from app.documentary.host_motion import hand_motion

pytestmark = pytest.mark.skipif(not ffmpeg_bin(), reason="ffmpeg is required")


def _video(path, filter_graph):
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=0x303030:s=640x360:d=12:r=10,{filter_graph}", "-pix_fmt", "yuv420p",
                    str(path)], check=True)


def test_a_still_video_has_no_hand_movement_and_a_waving_one_has_a_lot(tmp_path):
    still = tmp_path / "still.mp4"
    _video(still, "format=yuv420p")
    waving = tmp_path / "waving.mp4"
    # a bright box that jumps through the hand region (x 0.33-0.60, y 0.50-0.72)
    _video(waving, "drawbox=x=230:y=200:w=60:h=60:color=white:t=fill:enable='lt(mod(t\\,4)\\,2)',"
                   "drawbox=x=330:y=200:w=60:h=60:color=white:t=fill:enable='gte(mod(t\\,4)\\,2)'")
    quiet, busy = hand_motion(still), hand_motion(waving)
    assert quiet["windows"] == 5 and quiet["moving"] == 0 and quiet["mean_diff"] < 1
    assert busy["moving"] >= 3 and busy["mean_diff"] > quiet["mean_diff"] + 3
