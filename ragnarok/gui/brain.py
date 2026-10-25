"""What the cat does, and when. No windows here: the brain is told how much time passed and where
the mouse pointer is, and says which picture to show and where the cat is, so every reaction can be
tested without a screen.

Like a real cat it never does one thing for long: it wanders along the taskbar, stops to sit,
grooms, dozes off, wakes and wanders again. And it notices you:

  hover    the pointer rests on it: it purrs
  chase    the pointer moves along the taskbar: it runs after it, then sits and watches
  pounce   the pointer flicks past close by: it jumps at it
  carried  you picked it up: it dangles from your pointer, wherever you take it
  fall     you let go above the floor: it drops, bounces and lands
  poke     it was clicked: a hop and a happy moment
  alert    something needs you (a reminder): it stands up and shows "!" until you look
  busy     Ragnarok is working: it sits and "thinks", with dots over its head

While the chat is open above it, or it is carried, or it is busy or alerting, it ignores the pointer.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

WALK_SPEED = 38.0  # screen pixels per second (a stroll, not a march)
CHASE_SPEED = 135.0
STEP_TIME = 0.14  # seconds per walking frame
GRAVITY = 2600.0
MAX_LIFT = 280.0  # how high above the taskbar you can carry it
NOTICE_ABOVE = 150.0  # the pointer counts as "on the taskbar" up to this far above the floor
NOTICE_RADIUS = 210.0  # it only notices the pointer this close (sideways)...
LOSE_RADIUS = 400.0  # ...and keeps following until the pointer is this far away
NOTICE_AFTER = 0.3  # the pointer must linger this long near it first (not just whizz by)
WATCH_GAP = 85.0  # how close it sits to the pointer
POUNCE_SPEED = 950.0  # pointer speed (px/s) that makes it jump
POUNCE_COOLDOWN = 1.5  # seconds before it will jump at the pointer again
# What it does next, and for how long. The default is CALM: a cat that mostly rests, so it never
# pulls your eye while you work. LIVELY (the menu's "Playful") is a restless one.
CALM_CHOICES = (
    ("sit", 4.0), ("sleep", 3.0), ("stand", 1.0), ("walk", 2.6), ("groom", 0.8),
    ("scratch", 0.6), ("yawn", 0.5), ("play", 0.12),
)  # fmt: skip
CALM_DURATION = {
    "sit": (30.0, 80.0), "stand": (10.0, 25.0), "groom": (5.0, 9.0), "sleep": (90.0, 240.0),
    "yawn": (2.0, 2.4), "scratch": (3.0, 5.0), "play": (0.5, 0.5), "stretch": (2.6, 3.2),
}  # fmt: skip
LIVELY_CHOICES = (
    ("walk", 5.0), ("sit", 3.0), ("stand", 1.2), ("groom", 1.4), ("sleep", 1.0),
    ("yawn", 1.0), ("scratch", 1.1), ("play", 0.8),
)  # fmt: skip
LIVELY_DURATION = {
    "sit": (4.0, 9.0), "stand": (1.0, 3.0), "groom": (2.5, 4.5), "sleep": (12.0, 28.0),
    "yawn": (2.0, 2.4), "scratch": (2.2, 3.6), "play": (0.5, 0.5), "stretch": (2.6, 3.2),
}  # fmt: skip
WAKEFUL_AT_START = 60.0  # seconds after it appears before it may doze off
TILT_EVERY = 7.0  # a cat watching the pointer tilts its head twice in this many seconds...
TILT_FOR = 1.3  # ...for this long each time
MAX_STROLL = 260.0  # a calm walk goes at most this far
PLAY_REACH = 46.0  # how far it dashes when it bats at nothing
FOLLOW_MODES = ("chase", "watch", "pounce", "purr")
BUSY_MODES = ("carried", "fall", "alert", "think")


@dataclass(frozen=True)
class Pose:
    frame: str
    x: float
    facing: int  # 1 looks right, -1 looks left
    mode: str
    lift: float = 0.0  # pixels above the taskbar (a jump, a fall, being carried)
    look: int = 0  # which way the pupils turn, as the picture sees it (-1, 0, 1)


class CatBrain:
    def __init__(
        self,
        left: float,
        right: float,
        *,
        rng: random.Random | None = None,
        x: float | None = None,
        floor: float = 0.0,
        width: float = 0.0,
        height: float = 0.0,
    ) -> None:
        self.left, self.right = left, right
        self.floor, self.width, self.height = floor, width, height
        self.rng = rng or random.Random()
        self.x = x if x is not None else self.rng.uniform(left, right)
        self.facing = 1
        self.mode = "stand"
        self.clock = 0.0  # time spent in the current mode
        self.length = 1.0  # how long this mode lasts
        self.target = self.x
        self.lift = 0.0
        self.vy = 0.0  # vertical speed while falling (pixels/s, down is positive)
        self.busy = False
        self.alerting = False
        self.dragging = False
        self.anchored = False  # the chat is open above the cat: it stays where it is
        self.chase = True  # may it follow the pointer?
        self.lively = False  # restless instead of calm
        self.cursor: tuple[float, float] | None = None
        self.cursor_speed = 0.0
        self._last_cursor: tuple[float, float] | None = None
        self._hover = 0.0
        self._lost = 0.0
        self._near = 0.0  # how long the pointer has been close
        self._micro_in = self.rng.uniform(5.0, 12.0)  # until the next small fidget
        self._micro = ""  # "swish" (the tail) or "ear" (an ear twitch), while it lasts
        self._micro_left = 0.0
        self._pounce_from = 0.0
        self._last_pounce = -1e9
        self.clock_total = 0.0  # time since it was made
        self._pounce_to = 0.0
        self._blink_in = self.rng.uniform(2.5, 5.5)
        self._since_blink = 0.0

    # ---- what happens to the cat ------------------------------------------------------------

    def poke(self) -> None:
        """It was clicked."""
        if self.mode not in ("carried", "fall"):
            self._enter("happy", 1.4)

    def alert(self) -> None:
        self.alerting = True
        self._enter("alert", 1e9)

    def clear_alert(self) -> None:
        if self.alerting:
            self.alerting = False
            self._enter("happy", 1.0)

    def set_busy(self, busy: bool) -> None:
        if busy == self.busy:
            return
        self.busy = busy
        if busy:
            self._enter("think", 1e9)
        else:
            self._enter("happy", 1.6)

    def sleep_now(self) -> None:
        if self.mode not in BUSY_MODES:
            self._enter("sleep", 40.0)

    def set_cursor(self, x: float, y: float) -> None:
        """Where the mouse pointer is (screen pixels). Call it every frame."""
        self.cursor = (x, y)

    def drag_to(self, x: float, lift: float = 0.0) -> None:
        """You are holding it: it hangs from the pointer."""
        if not self.dragging:
            self.dragging = True
            self._enter("carried", 1e9)
        self.x = min(max(x, self.left), self.right)
        self.lift = min(max(lift, 0.0), MAX_LIFT)

    def drop(self) -> None:
        """You let go. Above the floor it falls; on the floor it just stands."""
        self.dragging = False
        if self.lift > 2:
            self.vy = 0.0
            self._enter("fall", 1e9)
        else:
            self.lift = 0.0
            self._enter("stand", 1.2)

    def resize(self, left: float, right: float) -> None:
        """The screen changed (a monitor was unplugged, the taskbar moved)."""
        self.left, self.right = left, right
        self.x = min(max(self.x, left), right)
        self.target = min(max(self.target, left), right)

    # ---- time passing -------------------------------------------------------------------------

    def step(self, dt: float) -> Pose:
        self.clock += dt
        self.clock_total += dt
        self._since_blink += dt
        self._read_cursor(dt)
        self._react_to_cursor(dt)
        self._fidget(dt)
        if self.mode == "fall":
            self._fall(dt)
        elif self.mode in FOLLOW_MODES:
            self._follow(dt)
        elif self.mode == "walk":
            self._walk(dt)
        elif self.mode == "play":
            self._play()
        elif self.mode not in ("carried", "think", "alert") and self.clock >= self.length:
            self._choose()
        return Pose(self._frame(), self.x, self.facing, self.mode, self.lift, self._look())

    def _fidget(self, dt: float) -> None:
        """While it sits, now and then: the tail swishes or an ear twitches (never both at once)."""
        if self.mode not in ("sit", "watch", "think"):
            self._micro, self._micro_left = "", 0.0
            return
        if self._micro_left > 0:
            self._micro_left -= dt
            if self._micro_left <= 0:
                self._micro = ""
            return
        self._micro_in -= dt
        if self._micro_in <= 0:
            self._micro = self.rng.choices(["swish", "ear"], [0.7, 0.3])[0]
            self._micro_left = 1.5 if self._micro == "swish" else 0.35
            self._micro_in = self.rng.uniform(6.0, 14.0)

    def _look(self) -> int:
        """Its eyes follow the pointer when it is near: +1 is toward the way the picture faces."""
        if (
            self.cursor is None
            or self.width <= 0
            or self.mode not in ("sit", "stand", "watch", "walk")
        ):
            return 0
        gap = self.cursor[0] - (self.x + self.width / 2)
        if abs(gap) < 45 or abs(gap) > 700:
            return 0
        return (1 if gap > 0 else -1) * self.facing

    # ---- the pointer ----------------------------------------------------------------------------

    def _read_cursor(self, dt: float) -> None:
        if self.cursor is None or dt <= 0:
            self.cursor_speed = 0.0
            return
        if self._last_cursor is not None:
            moved = math.dist(self.cursor, self._last_cursor)
            self.cursor_speed = 0.6 * self.cursor_speed + 0.4 * (moved / dt)
        self._last_cursor = self.cursor

    def _watching_allowed(self) -> bool:
        return (
            self.width > 0
            and self.cursor is not None
            and self.chase
            and not self.anchored
            and self.mode not in BUSY_MODES
        )

    def _react_to_cursor(self, dt: float) -> None:
        if not self._watching_allowed():
            if self.mode in FOLLOW_MODES:
                self._enter("stand", 1.0)  # it was following; now it must not
            self._hover = 0.0
            return
        assert self.cursor is not None
        cx, cy = self.cursor
        centre = self.x + self.width / 2
        hovering = (
            abs(cx - centre) <= self.width / 2 and self.floor - self.height <= cy <= self.floor + 60
        )
        if hovering:
            self._hover += dt
            if self._hover > 0.8 and self.mode != "purr":
                self._enter("purr", 1e9)
            return
        self._hover = 0.0
        if self.mode == "purr":
            self._enter("stand", 0.6)
        gap = cx - centre
        on_strip = self.floor - NOTICE_ABOVE <= cy <= self.floor + 200
        following = self.mode in FOLLOW_MODES
        # it only notices the pointer when it comes near, and keeps following a while after
        if not on_strip or abs(gap) > (LOSE_RADIUS if following else NOTICE_RADIUS):
            self._near = 0.0
            if following:
                self._lost += dt
                if self._lost > 0.8:
                    self._lost = 0.0
                    self._enter("stand", 1.0)
            return
        self._lost = 0.0
        if not following:
            self._near += dt
            if self._near < NOTICE_AFTER:
                return  # it just whizzed past
        if self.mode == "pounce":
            return
        ready = self.clock_total - self._last_pounce > POUNCE_COOLDOWN
        if (
            self.mode in ("watch", "chase")
            and ready
            and self.cursor_speed > POUNCE_SPEED
            and abs(gap) < 320
        ):
            self._last_pounce = self.clock_total
            reach = math.copysign(min(abs(gap), 130), gap)
            self._pounce_from = self.x
            self._pounce_to = min(max(self.x + reach, self.left), self.right)
            self.facing = 1 if gap > 0 else -1
            self._enter("pounce", 0.45)
        elif abs(gap) > WATCH_GAP + 30 and self.mode != "chase":
            self._enter("chase", 1e9)
        elif abs(gap) <= WATCH_GAP and self.mode != "watch":
            self._enter("watch", 1e9)

    def _follow(self, dt: float) -> None:
        if self.cursor is None:
            return
        cx, _cy = self.cursor
        centre = self.x + self.width / 2
        gap = cx - centre
        if self.mode in ("watch", "purr"):
            if self.mode == "watch":
                self.facing = 1 if gap > 0 else -1
                if abs(gap) > WATCH_GAP + 30:
                    self._enter("chase", 1e9)
            return
        if self.mode == "pounce":
            t = min(self.clock / self.length, 1.0)
            self.x = self._pounce_from + (self._pounce_to - self._pounce_from) * t
            self.lift = 46.0 * math.sin(math.pi * t)
            if self.clock >= self.length:
                self.lift = 0.0
                self._enter("watch", 1e9)
            return
        # chase: run at the pointer, stop a little way short of it
        if abs(gap) <= WATCH_GAP:
            self._enter("watch", 1e9)
            return
        self.facing = 1 if gap > 0 else -1
        self.x = min(max(self.x + self.facing * CHASE_SPEED * dt, self.left), self.right)

    # ---- moving by itself ---------------------------------------------------------------------

    def _fall(self, dt: float) -> None:
        self.vy += GRAVITY * dt
        self.lift -= self.vy * dt
        if self.lift <= 0:
            self.lift = 0.0
            if self.vy > 420:  # a bounce, smaller each time
                self.vy = -self.vy * 0.32
                return
            self.vy = 0.0
            self._enter("happy", 0.9)  # it landed on its feet

    def _walk(self, dt: float) -> None:
        distance = self.target - self.x
        move = WALK_SPEED * dt
        if abs(distance) <= move:
            self.x = self.target
            self._enter(self.rng.choice(["sit", "sit", "sit", "stand", "groom"]))
            return
        self.x += move if distance > 0 else -move
        self.facing = 1 if distance > 0 else -1

    def _play(self) -> None:
        """It bats at something only it can see: a short hop forward."""
        t = min(self.clock / self.length, 1.0)
        self.x = self._pounce_from + (self._pounce_to - self._pounce_from) * t
        self.lift = 22.0 * math.sin(math.pi * t)
        if self.clock >= self.length:
            self.lift = 0.0
            self._choose()

    def _choose(self) -> None:
        self.lift = 0.0
        if (
            self.mode == "yawn"
            and self.clock_total >= WAKEFUL_AT_START
            and self.rng.random() < 0.45
        ):
            return self._enter("sleep")  # a yawn is often followed by a nap
        if self.mode == "sleep":
            return self._enter("stretch")  # and waking up is a good long stretch
        if self.mode == "stretch" and self.rng.random() < 0.5:
            return self._enter("yawn")
        choices = LIVELY_CHOICES if self.lively else CALM_CHOICES
        if self.clock_total < WAKEFUL_AT_START:  # a cat that has just appeared does not nap at once
            choices = tuple(c for c in choices if c[0] != "sleep")
        modes, weights = zip(*choices, strict=True)
        pick = self.rng.choices(modes, weights)[0]
        if self.mode == "sleep" and pick == "sleep":
            pick = "stand"
        if self.anchored and pick in ("walk", "play"):
            pick = "sit"
        if pick == "stand" and self.rng.random() < 0.4:
            self.facing = -self.facing  # it looks round behind it
        if pick == "walk":
            span = self.right - self.left
            if span < 60:
                return self._enter("sit")
            for _ in range(8):
                if self.lively:
                    target = self.rng.uniform(self.left, self.right)
                else:  # a short stroll, not a trek across the screen
                    target = min(
                        max(self.x + self.rng.uniform(-MAX_STROLL, MAX_STROLL), self.left),
                        self.right,
                    )
                if abs(target - self.x) > min(70.0 if not self.lively else 120.0, span / 3):
                    self.target = target
                    self.facing = 1 if target > self.x else -1
                    return self._enter("walk", 1e9)
            return self._enter("sit")
        self._enter(pick)

    def _enter(self, mode: str, length: float | None = None) -> None:
        self.mode = mode
        self.clock = 0.0
        if mode not in ("carried", "fall", "pounce"):
            self.lift = 0.0
        if length is None:
            low, high = (LIVELY_DURATION if self.lively else CALM_DURATION).get(mode, (1.0, 2.0))
            length = self.rng.uniform(low, high)
        self.length = length
        if mode == "play":
            self._pounce_from = self.x
            reach = self.facing * PLAY_REACH
            self._pounce_to = min(max(self.x + reach, self.left), self.right)

    # ---- which picture ------------------------------------------------------------------------

    def _blinking(self) -> bool:
        if self._since_blink >= self._blink_in:
            if self._since_blink >= self._blink_in + 0.16:
                self._since_blink = 0.0
                self._blink_in = self.rng.uniform(2.5, 6.0)
            return True
        return False

    def _curious_tilt(self) -> str:
        """While it watches the pointer it tilts its head, as soon as it notices and now and then
        after, one way and then the other. "" is no tilt."""
        phase = self.clock % TILT_EVERY
        if phase < TILT_FOR:
            return "0"
        if TILT_EVERY / 2 <= phase < TILT_EVERY / 2 + TILT_FOR:
            return "1"
        return ""

    def _frame(self) -> str:
        mode = self.mode
        if mode == "walk":
            return f"walk{int(self.clock / STEP_TIME) % 4}"
        if mode == "chase":
            return f"walk{int(self.clock / (STEP_TIME * 0.55)) % 4}"
        if mode == "watch" and (tilt := self._curious_tilt()):
            return f"sit_tilt{tilt}"
        if mode in ("sit", "watch"):
            if self._micro == "swish":
                return f"sit_swish{int(self.clock_total / 0.22) % 2}"
            if self._micro == "ear":
                return "sit_ear"
            if self._blinking():
                return "sit_blink"
            return "sit_b" if int(self.clock_total / 1.8) % 2 else "sit"  # slow breathing
        if mode == "stand":
            return "stand_blink" if self._blinking() else "stand"
        if mode == "groom":
            return f"groom{int(self.clock / 0.4) % 2}"
        if mode == "yawn":
            return "yawn0" if self.clock < 0.5 else "yawn1" if self.clock < 1.5 else "yawn2"
        if mode == "scratch":
            return f"scratch{int(self.clock / 0.13) % 2}"
        if mode == "play":
            return "pounce"
        if mode == "sleep":
            if self.clock < 2.5:
                return "loaf"  # first it lies down, eyes still open
            return f"sleep{int(self.clock / 0.9) % 3}"
        if mode == "stretch":
            return "stretch1" if 0.6 < self.clock < self.length - 0.6 else "stretch0"
        if mode == "think":
            return f"think{int(self.clock / 0.5) % 3 + 1}"
        if mode == "alert":
            return "alert"
        if mode == "pounce":
            return "pounce"
        wag = "happy_b" if int(self.clock_total / 0.3) % 2 else "happy"  # the tail wags
        if mode == "purr":
            return wag
        if mode == "happy":
            return "jump" if int(self.clock / 0.25) % 2 == 0 and self.clock < 0.9 else wag
        if mode in ("carried", "fall"):
            return f"carried{int(self.clock / 0.18) % 2}"
        return "stand"
