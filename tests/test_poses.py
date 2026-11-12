import os

from overlay import poses


def test_pick_state_priority():
    base = dict(dragging=False, landing=False, sleeping=False, talking=False, mood="")
    assert poses.pick_state(**base) == "idle"
    assert poses.pick_state(**{**base, "mood": "music"}) == "music"
    assert poses.pick_state(**{**base, "mood": "music", "talking": True}) == "talk"
    assert poses.pick_state(**{**base, "talking": True, "sleeping": True}) == "sleep"
    assert poses.pick_state(**{**base, "sleeping": True, "landing": True}) == "land"
    assert poses.pick_state(**{**base, "landing": True, "dragging": True}) == "drag"


def test_poses_stay_in_sane_ranges():
    for state in poses.STATES:
        for step in range(200):
            t = step * 0.033
            tilt, squash = poses.pose_for(state, t, drag_dx=40.0, land_age=t)
            assert -25.0 <= tilt <= 25.0, (state, t, tilt)
            assert 0.8 <= squash <= 1.2, (state, t, squash)
    assert poses.pose_for("idle", 3.0) == (0.0, 1.0)
    # Landing settles back to normal.
    assert abs(poses.pose_for("land", 0, land_age=0.6)[1] - 1.0) < 0.02


def test_frame_paths_sorted_pngs_only(tmp_path):
    talk = tmp_path / "talk"
    talk.mkdir()
    for name in ("002.png", "000.png", "001.png", "notes.txt"):
        (talk / name).write_bytes(b"")
    paths = poses.frame_paths(str(tmp_path), "talk")
    assert [os.path.basename(p) for p in paths] == ["000.png", "001.png", "002.png"]
    assert poses.frame_paths(str(tmp_path), "sleep") == []
