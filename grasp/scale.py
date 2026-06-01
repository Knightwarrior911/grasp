"""Coordinate scaling. Vision models target/click more accurately at <= XGA resolution,
so screenshots are downscaled to one of these targets and the model works in that space;
we scale its coordinates back up to physical pixels on execute, and scale screenshots down
on capture. (Anthropic's computer-use discipline.)"""

# (width, height) -- 4:3, 16:10, ~16:9
MAX_TARGETS = [("XGA", 1024, 768), ("WXGA", 1280, 800), ("FWXGA", 1366, 768)]


def _target_for(real_w, real_h):
    ratio = real_w / real_h if real_h else 1.0
    for _name, tw, th in MAX_TARGETS:
        if abs(tw / th - ratio) < 0.02:           # aspect matches a known target
            return (tw, th) if real_w > tw else (real_w, real_h)
    # no aspect match: cap the long side near XGA while preserving the real ratio
    cap = 1280
    if real_w >= real_h:
        return (cap, round(cap / ratio)) if real_w > cap else (real_w, real_h)
    return (round(cap * ratio), cap) if real_h > cap else (real_w, real_h)


class Scaler:
    """Maps between the model's scaled coordinate space and physical pixels."""

    def __init__(self, real_w, real_h):
        self.real = (int(real_w), int(real_h))
        self.target = _target_for(self.real[0], self.real[1])
        self.fx = self.target[0] / self.real[0]
        self.fy = self.target[1] / self.real[1]

    @property
    def scaled(self):
        return self.target

    def to_real(self, x, y):
        """model (scaled) coordinate -> physical pixel"""
        return round(x / self.fx), round(y / self.fy)

    def to_model(self, x, y):
        """physical pixel -> model (scaled) coordinate"""
        return round(x * self.fx), round(y * self.fy)

    def info(self):
        return {"real": list(self.real), "model_space": list(self.target),
                "scale_x": round(self.fx, 4), "scale_y": round(self.fy, 4)}
