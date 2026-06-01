from grasp.scale import Scaler, _target_for


def test_xga_4x3_downscales_to_1024():
    # 1600x1200 is 4:3 -> XGA target 1024x768
    assert _target_for(1600, 1200) == (1024, 768)


def test_wxga_16x10_downscales_to_1280():
    # 1920x1200 is 16:10 -> WXGA 1280x800
    assert _target_for(1920, 1200) == (1280, 800)


def test_fwxga_16x9_downscales_to_1366():
    # 1920x1080 is 16:9 -> FWXGA 1366x768
    assert _target_for(1920, 1080) == (1366, 768)


def test_small_screen_not_upscaled():
    # already <= target: leave it
    assert _target_for(800, 600) == (800, 600)


def test_odd_aspect_caps_long_side():
    # ultrawide 3440x1440 (~2.39:1): no known target, cap long side at 1280
    w, h = _target_for(3440, 1440)
    assert w == 1280
    assert abs(w / h - 3440 / 1440) < 0.02


def test_roundtrip_real_then_model_is_identity():
    s = Scaler(1920, 1080)
    # a model coord -> real -> back to model should be ~stable
    mx, my = 683, 384
    rx, ry = s.to_real(mx, my)
    bx, by = s.to_model(rx, ry)
    assert abs(bx - mx) <= 1
    assert abs(by - my) <= 1


def test_to_real_scales_up():
    s = Scaler(1920, 1080)            # model space 1366x768
    # center of model space maps near center of real space
    rx, ry = s.to_real(683, 384)
    assert abs(rx - 960) <= 2
    assert abs(ry - 540) <= 2


def test_info_reports_real_and_model():
    s = Scaler(1920, 1080)
    info = s.info()
    assert info["real"] == [1920, 1080]
    assert info["model_space"] == [1366, 768]
    assert 0 < info["scale_x"] < 1
