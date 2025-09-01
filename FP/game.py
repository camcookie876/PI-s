import tkinter as tk
import time
import math
import json
import os

# =========================
# Config
# =========================
W, H = 1100, 620
GROUND_BASE_Y = 470
GRAVITY = 1500.0
JUMP_VY = -540.0
MAX_SPEED = 270.0
ACCEL = 620.0
BRAKE = 980.0
ROLL_DECEL = 720.0
FRAME_DT = 1/60
TRACK_LENGTH = 3600.0

BOT_COUNT_DEFAULT = 3
COUNTDOWN_MS = 3000

OBST_ROCKS = 12
OBST_LOGS = 8
OBST_RAMPS = 8

STATS_FILE = "dirt_dash_stats.json"

# =========================
# Persisted stats
# =========================
def load_stats():
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"total_races": 0, "wins": 0, "best_time": None, "reduced_motion": True, "bots": BOT_COUNT_DEFAULT}

def save_stats(stats):
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f)
    except Exception:
        pass

stats = load_stats()

# =========================
# Utilities
# =========================
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def fmt_time(t):
    if t is None:
        return "—"
    m = int(t // 60)
    s = t - 60 * m
    return f"{m}:{s:05.2f}"

def seeded_rand(seed):
    # Simple LCG, returns function that yields [0,1)
    state = {"x": seed & 0x7fffffff}
    def rnd():
        state["x"] = (1103515245 * state["x"] + 12345) & 0x7fffffff
        return state["x"] / 0x7fffffff
    return rnd

def ground_y_at(xw):
    # Gentle slopes for comfort
    return (GROUND_BASE_Y
            + 16.0 * math.sin((xw + 200) / 260.0)
            + 12.0 * math.sin((xw + 900) / 180.0))

# =========================
# Entities
# =========================
class Bike:
    def __init__(self, color="#FFD166", is_bot=False, name="You"):
        self.is_bot = is_bot
        self.name = name
        self.color = color
        self.xw = 0.0
        self.y = ground_y_at(0) - 40
        self.vy = 0.0
        self.speed = 0.0
        self.engine_on = False
        self.finished = False
        self.finish_time = None
        self.bump_cooldown = 0.0

    def on_ground(self):
        return abs(self.y - (ground_y_at(self.xw) - 36)) < 0.2

    def update_physics(self, dt):
        self.vy += GRAVITY * dt
        self.y += self.vy * dt
        floor = ground_y_at(self.xw) - 36
        if self.y >= floor:
            self.y = floor
            self.vy = 0.0
        if self.bump_cooldown > 0:
            self.bump_cooldown -= dt

    def jump(self, vy=JUMP_VY):
        if self.on_ground():
            self.vy = vy

class BotAI:
    def __init__(self, bike, target=220.0, jump_bias=0.0, name="Bot"):
        self.bike = bike
        self.target_speed = target
        self.jump_bias = jump_bias
        self.name = name

    def step(self, dt, obstacles):
        b = self.bike
        if b.finished:
            return
        # Maintain target speed
        if b.speed < self.target_speed:
            b.speed = min(b.speed + (ACCEL * 0.8) * dt, self.target_speed)
        else:
            b.speed = max(b.speed - (ROLL_DECEL * 0.4) * dt, self.target_speed * 0.9)
        # Look ahead and jump if needed
        look = 140 + self.jump_bias
        for ob in obstacles:
            dx = ob["xw"] - b.xw
            if 0 < dx < look:
                if b.on_ground():
                    if ob["type"] == "ramp":
                        b.jump(JUMP_VY * 0.95)
                    else:
                        b.jump(JUMP_VY * 0.88)
                break
        b.xw += b.speed * dt
        b.update_physics(dt)

# =========================
# Game
# =========================
class Game:
    def __init__(self, root):
        self.root = root
        self.root.title("Camcookie Dirt Dash — Race")
        self.canvas = tk.Canvas(root, width=W, height=H, bg="#1C2537", highlightthickness=0)
        self.canvas.pack()
        self.state = "HOME"  # HOME, GRID, PLAY, PAUSE, END
        self.keys = set()
        self.world_x = 0.0
        self.player = Bike()
        self.bots = []
        self.bot_ai = []
        self.time_elapsed = 0.0
        self.countdown_end = None
        self.results = []
        self.bot_count = int(stats.get("bots", BOT_COUNT_DEFAULT))
        self.reduced_motion = bool(stats.get("reduced_motion", True))
        self.last_time = time.perf_counter()
        self.home_buttons = []  # list of (x0,y0,x1,y1, callback, label)
        self.bind_events()
        self.start_loop()
        self.draw_home()

    # -------------
    # Event binding
    # -------------
    def bind_events(self):
        self.root.bind("<KeyPress>", self.on_keydown)
        self.root.bind("<KeyRelease>", self.on_keyup)
        self.canvas.bind("<Button-1>", self.on_click)

    def on_keydown(self, e):
        self.keys.add(e.keysym)
        if e.keysym == "space":
            if self.state == "PLAY":
                self.player.jump()
        elif e.keysym.lower() == "s":
            if self.state in ("GRID", "PLAY", "PAUSE"):
                self.player.engine_on = not self.player.engine_on
        elif e.keysym.lower() == "p":
            self.pause_toggle()
        elif e.keysym.lower() == "r":
            if self.state in ("PLAY", "PAUSE", "END"):
                self.start_race()
        elif e.keysym == "Return":
            if self.state == "HOME":
                self.start_race()
            elif self.state == "END":
                self.start_race()

    def on_keyup(self, e):
        if e.keysym in self.keys:
            self.keys.remove(e.keysym)

    def on_click(self, e):
        if self.state == "HOME":
            for x0, y0, x1, y1, cb, _ in self.home_buttons:
                if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                    cb()
                    break
        elif self.state == "PAUSE":
            # simple resume hitbox
            x0, y0, x1, y1 = self.pause_btn_box
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                self.pause_toggle()

    # -------------
    # Loop
    # -------------
    def start_loop(self):
        self.last_time = time.perf_counter()
        self.root.after(int(1000 * FRAME_DT), self.loop)

    def loop(self):
        now = time.perf_counter()
        dt = now - self.last_time
        self.last_time = now
        dt = min(dt, 1/30)  # avoid huge steps if window stalls

        if self.state == "PLAY":
            self.update_play(dt)
        elif self.state == "GRID":
            # countdown display only
            pass

        self.draw()
        self.root.after(int(1000 * FRAME_DT), self.loop)

    # -------------
    # Flow control
    # -------------
    def pause_toggle(self):
        if self.state == "PLAY":
            self.state = "PAUSE"
        elif self.state == "PAUSE":
            self.state = "PLAY"

    def to_home(self):
        stats["bots"] = self.bot_count
        stats["reduced_motion"] = self.reduced_motion
        save_stats(stats)
        self.state = "HOME"
        self.draw_home()

    def start_race(self):
        self.state = "GRID"
        self.results = []
        self.time_elapsed = 0.0
        self.world_x = 0.0
        self.player = Bike()
        self.player.engine_on = False
        self.spawn_obstacles()
        self.build_bots()
        self.countdown_end = time.perf_counter() + (COUNTDOWN_MS/1000.0)

    def build_bots(self):
        self.bots = []
        self.bot_ai = []
        colors = ["#93C5FD", "#86EFAC", "#FCA5A5", "#F0ABFC", "#FDE68A"]
        for i in range(self.bot_count):
            b = Bike(color=colors[i % len(colors)], is_bot=True, name=f"Bot {i+1}")
            self.bots.append(b)
            ai = BotAI(b, target=220.0 + 12*i, jump_bias=10*i, name=b.name)
            self.bot_ai.append(ai)

    # -------------
    # Track and obstacles
    # -------------
    def spawn_obstacles(self):
        self.obstacles = []
        rnd = seeded_rand(1337)
        # rocks
        for n in range(OBST_ROCKS):
            xw = 240 + (TRACK_LENGTH - 480) * (n + 1) / (OBST_ROCKS + 1) + int(rnd()*53) - 26
            r = 10 + (n * 31) % 7
            self.obstacles.append({"type": "rock", "xw": xw, "r": r})
        # logs
        for n in range(OBST_LOGS):
            xw = 400 + (TRACK_LENGTH - 800) * (n + 1) / (OBST_LOGS + 1) + int(rnd()*73) - 36
            w = 56 + (n * 13) % 16
            h = 12
            self.obstacles.append({"type": "log", "xw": xw, "w": w, "h": h})
        # ramps
        for n in range(OBST_RAMPS):
            xw = 350 + (TRACK_LENGTH - 700) * (n + 1) / (OBST_RAMPS + 1) + int(rnd()*61) - 30
            w = 84
            h = 40
            self.obstacles.append({"type": "ramp", "xw": xw, "w": w, "h": h})
        self.obstacles.sort(key=lambda o: o["xw"])

    # -------------
    # Update
    # -------------
    def update_play(self, dt):
        # Engine and input
        if not self.player.engine_on:
            self.player.speed = max(0.0, self.player.speed - ROLL_DECEL * dt)
        else:
            if "Right" in self.keys:
                self.player.speed = clamp(self.player.speed + ACCEL * dt, 0, MAX_SPEED)
            elif "Left" in self.keys:
                self.player.speed = clamp(self.player.speed - BRAKE * dt, 0, MAX_SPEED)
            else:
                self.player.speed = clamp(self.player.speed - ROLL_DECEL * dt, 0, MAX_SPEED)

        # Advance world
        self.player.xw += self.player.speed * dt
        self.player.update_physics(dt)

        # Bots
        for ai in self.bot_ai:
            ai.step(dt, self.obstacles)

        # Obstacle interaction (player)
        self.handle_obstacles(self.player)

        # Finish check
        everyone = [self.player] + self.bots
        for b in everyone:
            if (not b.finished) and b.xw >= TRACK_LENGTH:
                b.finished = True
                b.finish_time = self.time_elapsed

        # Race finished?
        if all(b.finished or b.xw >= TRACK_LENGTH for b in everyone):
            self.end_race()

        # Timer
        self.time_elapsed += dt

    def handle_obstacles(self, bike):
        # simple collisions/jumps
        for ob in self.obstacles:
            dx = ob["xw"] - bike.xw
            if -30 <= dx <= 30:
                gy = ground_y_at(ob["xw"])
                if ob["type"] == "rock":
                    top = gy - ob["r"]
                    if bike.y >= top - 10 and bike.bump_cooldown <= 0:
                        bike.speed = max(bike.speed * 0.6, bike.speed - 80)
                        bike.vy = -120
                        bike.bump_cooldown = 0.6
                elif ob["type"] == "log":
                    top = gy - ob["h"]
                    if bike.y >= top - 10 and bike.bump_cooldown <= 0:
                        bike.speed = max(bike.speed * 0.7, bike.speed - 90)
                        bike.vy = -150
                        bike.bump_cooldown = 0.6
                else:  # ramp, give a lift if on ground and at ramp mouth
                    mouth_left = ob["xw"] - ob["w"] / 2
                    mouth_right = ob["xw"] + ob["w"] / 2
                    if mouth_left - 10 <= bike.xw <= mouth_right + 10 and bike.on_ground():
                        bike.vy = JUMP_VY * 0.9  # gentle ramp jump

    def end_race(self):
        # gather results (sort by finish_time)
        everyone = []
        for b in [self.player] + self.bots:
            t = b.finish_time if b.finish_time is not None else self.time_elapsed
            everyone.append((b.name, t))
        everyone.sort(key=lambda t: t[1])
        self.results = everyone

        # update stats
        stats["total_races"] = stats.get("total_races", 0) + 1
        # is player first?
        if len(everyone) > 0 and everyone[0][0] == "You":
            stats["wins"] = stats.get("wins", 0) + 1
        # best time
        pt = next((t for n, t in everyone if n == "You"), None)
        if pt is not None:
            bt = stats.get("best_time", None)
            if (bt is None) or (pt < bt):
                stats["best_time"] = pt
        stats["bots"] = self.bot_count
        stats["reduced_motion"] = self.reduced_motion
        save_stats(stats)

        self.state = "END"

    # -------------
    # Draw
    # -------------
    def draw(self):
        self.canvas.delete("all")
        # Background
        self.draw_background()
        # Ground and level
        self.draw_ground()
        self.draw_finish_start()
        self.draw_obstacles()

        # Entities
        self.draw_player()
        self.draw_bots()

        # HUD and overlays
        self.draw_hud()

        if self.state == "HOME":
            self.draw_home_overlay()
        elif self.state == "GRID":
            self.draw_grid_overlay()
        elif self.state == "PAUSE":
            self.draw_pause_overlay()
        elif self.state == "END":
            self.draw_end_overlay()

    def draw_background(self):
        # sky is canvas background
        # parallax bands
        par = 0.15 if self.reduced_motion else 0.3
        off1 = -((self.world_x * par) % W)
        off2 = -((self.world_x * par * 1.4) % W)
        y1 = H - 260
        y2 = H - 200
        self.canvas.create_rectangle(off1, y1, off1 + W * 2, H, fill="#1B2438", width=0)
        self.canvas.create_rectangle(off2, y2, off2 + W * 2, H, fill="#162034", width=0)
        # stands strip
        self.canvas.create_rectangle(0, H - 150, W, H - 90, fill="#121A2A", width=0)

    def draw_ground(self):
        # camera scroll
        target_x = self.player.xw
        # ease world_x toward player x to reduce motion (smoother camera)
        self.world_x += (target_x - self.world_x) * (0.12 if self.reduced_motion else 0.2)

        step = 8
        pts = []
        for sx in range(0, W + step, step):
            xw = self.world_x + sx
            y = ground_y_at(xw)
            pts.append((sx, y))
        # close polygon to bottom
        poly = []
        for (x, y) in pts:
            poly.extend([x, y])
        poly.extend([W, H, 0, H])
        self.canvas.create_polygon(*poly, fill="#2C3A4F", outline="#192235")

    def draw_finish_start(self):
        # finish
        fx = TRACK_LENGTH - self.world_x
        if -50 <= fx <= W + 50:
            self.canvas.create_rectangle(fx, H - 320, fx + 6, H, fill="#0EA5E9", width=0)
            # checker
            for r in range(3):
                for c in range(3):
                    if (r + c) % 2 == 0:
                        x0 = fx + 6 + c * 8
                        y0 = H - 320 + r * 8
                        self.canvas.create_rectangle(x0, y0, x0 + 8, y0 + 8, fill="#111827", width=0)
        # start
        sx = 0 - self.world_x
        if -50 <= sx <= W + 50:
            self.canvas.create_rectangle(sx, H - 320, sx + 6, H, fill="#10B981", width=0)
            self.canvas.create_rectangle(sx + 6, H - 320, sx + 6 + 70, H - 298, fill="#22D3EE", width=0)

    def draw_obstacles(self):
        for ob in self.obstacles:
            x = ob["xw"] - self.world_x
            y = ground_y_at(ob["xw"])
            if -100 <= x <= W + 100:
                if ob["type"] == "rock":
                    r = ob["r"]
                    self.canvas.create_oval(x - r, y - 2 * r, x + r, y, fill="#7DD3FC", outline="#0EA5E9", width=2)
                elif ob["type"] == "log":
                    w = ob["w"]; h = ob["h"]
                    self.canvas.create_rectangle(x - w/2, y - h, x + w/2, y, fill="#A78B6A", outline="#6B4F33", width=2)
                else:
                    w = ob["w"]; h = ob["h"]
                    # ramp triangle
                    self.canvas.create_polygon(x - w/2, y, x + w/2, y, x - w/2, y - h,
                                               fill="#9CA3AF", outline="#6B7280", width=2)

    def draw_player(self):
        # player drawn as bike rectangle + wheels
        sx = (self.player.xw - self.world_x) + 220
        sy = self.player.y
        # body
        self.canvas.create_rectangle(sx - 30, sy - 12, sx + 32, sy + 6, fill=self.player.color, outline="#2B2D42", width=2)
        # wheels
        self.canvas.create_oval(sx - 30 - 12, sy + 6, sx - 30 + 12, sy + 30, outline="#F8FAFC", width=3)
        self.canvas.create_oval(sx + 20 - 12, sy + 6, sx + 20 + 12, sy + 30, outline="#F8FAFC", width=3)
        # rider head
        self.canvas.create_oval(sx - 2 - 6, sy - 12 - 6, sx - 2 + 6, sy - 12 + 6, fill="#E11D48", width=0)

    def draw_bots(self):
        for b in self.bots:
            sx = (b.xw - self.world_x) + 220
            sy = b.y
            self.canvas.create_rectangle(sx - 30, sy - 12, sx + 32, sy + 6, fill=b.color, outline="#0F172A", width=2)
            self.canvas.create_oval(sx - 42, sy + 6, sx - 18, sy + 30, outline="#E5E7EB", width=2)
            self.canvas.create_oval(sx + 8, sy + 6, sx + 32, sy + 30, outline="#E5E7EB", width=2)
            self.canvas.create_oval(sx - 2 - 6, sy - 12 - 6, sx - 2 + 6, sy - 12 + 6, fill="#334155", width=0)

    def draw_hud(self):
        # bg
        self.canvas.create_rectangle(12, 12, 12 + 460, 12 + 92, fill="#00000059", outline="", width=0)
        # time
        self.canvas.create_text(24, 36, anchor="w", fill="#E5E7EB", font=("Segoe UI", 14),
                                text=f"Time {fmt_time(self.time_elapsed)}")
        # speed
        self.canvas.create_text(24, 60, anchor="w", fill="#E5E7EB", font=("Segoe UI", 12),
                                text=f"Speed {int(self.player.speed)}")
        # position
        everyone = [("You", self.player.xw)] + [(b.name, b.xw) for b in self.bots]
        everyone.sort(key=lambda t: -t[1])
        pos = next((i+1 for i, (n, _) in enumerate(everyone) if n == "You"), 1)
        self.canvas.create_text(160, 60, anchor="w", fill="#E5E7EB", font=("Segoe UI", 12),
                                text=f"Pos {pos}/{len(everyone)}")
        # hint
        self.canvas.create_text(320, 60, anchor="w", fill="#CBD5E1", font=("Segoe UI", 10),
                                text="S engine | → throttle | ← brake")
        # status strip
        self.canvas.create_text(12, H - 12, anchor="sw", fill="#CBD5E1", font=("Segoe UI", 10),
                                text=f"State: {self.state}   Engine: {'ON' if self.player.engine_on else 'OFF'}   Reduced motion: {'ON' if self.reduced_motion else 'OFF'}   Bots: {self.bot_count}")

    # -------------
    # Overlays
    # -------------
    def draw_home(self):
        self.canvas.delete("all")
        self.draw_background()
        self.draw_ground()
        self.home_buttons = []
        # panel
        x, y, w, h = 320, 140, 480, 300
        self.canvas.create_rectangle(x, y, x + w, y + h, fill="#0000008C", outline="#0EA5E9", width=2)
        self.canvas.create_text(x + 30, y + 40, anchor="w", fill="#F8FAFC", font=("Segoe UI", 22, "bold"),
                                text="Race Setup")
        best = fmt_time(stats.get("best_time"))
        races = stats.get("total_races", 0)
        wins = stats.get("wins", 0)
        self.canvas.create_text(x + 30, y + 70, anchor="w", fill="#CBD5E1", font=("Segoe UI", 12),
                                text=f"Best: {best}   Races: {races}   Wins: {wins}")

        # Bots control
        self.canvas.create_text(x + 30, y + 100, anchor="w", fill="#E5E7EB", font=("Segoe UI", 12),
                                text=f"Bots: {self.bot_count}")
        # buttons
        def button(rx, ry, rw, rh, label, cb):
            self.canvas.create_rectangle(rx, ry, rx + rw, ry + rh, fill="#FFD166", outline="", width=0)
            self.canvas.create_text(rx + rw/2, ry + rh/2, fill="#1F2937", font=("Segoe UI", 12, "bold"), text=label)
            self.home_buttons.append((rx, ry, rx + rw, ry + rh, cb, label))

        button(x + 140, y + 90, 28, 28, "-", self.dec_bots)
        button(x + 174, y + 90, 28, 28, "+", self.inc_bots)

        # Reduced motion toggle
        label_rm = "Reduced Motion: ON" if self.reduced_motion else "Reduced Motion: OFF"
        button(x + 30, y + 130, 210, 34, label_rm, self.toggle_rm)

        # Start
        button(x + 30, y + 180, 160, 38, "Start Race (Enter)", self.start_race)

        # Static showcase
        self.world_x = 120.0
        self.draw()

    def draw_home_overlay(self):
        # Buttons already drawn in draw_home()
        pass

    def draw_grid_overlay(self):
        # Countdown
        ms_left = int(max(0, (self.countdown_end - time.perf_counter()) * 1000))
        if ms_left == 0 and self.state == "GRID":
            self.state = "PLAY"
            self.player.engine_on = True  # auto on when race starts
        # big numbers
        if self.state == "GRID":
            sec = ms_left // 1000
            disp = "3" if sec >= 2 else "2" if sec >= 1 else "1" if ms_left > 0 else "GO"
            self.canvas.create_text(W/2, H/2 - 60, fill="#FDE68A", font=("Segoe UI", 54, "bold"), text=disp)

    def draw_pause_overlay(self):
        # panel with resume button
        x, y, w, h = 400, 220, 300, 140
        self.canvas.create_rectangle(x, y, x + w, y + h, fill="#0000008C", outline="#0EA5E9", width=2)
        self.canvas.create_text(x + w/2, y + 40, fill="#F3F4F6", font=("Segoe UI", 20, "bold"), text="Paused")
        # resume button
        rx, ry, rw, rh = x + 50, y + 70, 200, 34
        self.canvas.create_rectangle(rx, ry, rx + rw, ry + rh, fill="#FFD166", outline="", width=0)
        self.canvas.create_text(rx + rw/2, ry + rh/2, fill="#1F2937", font=("Segoe UI", 12, "bold"), text="Resume (P)")
        self.pause_btn_box = (rx, ry, rx + rw, ry + rh)

    def draw_end_overlay(self):
        x, y, w, h = 260, 120, 560, 320
        self.canvas.create_rectangle(x, y, x + w, y + h, fill="#0000008C", outline="#0EA5E9", width=2)
        self.canvas.create_text(x + 20, y + 40, anchor="w", fill="#F8FAFC", font=("Segoe UI", 22, "bold"),
                                text="Race Results")
        yy = y + 70
        pos = 1
        for name, t in self.results:
            self.canvas.create_text(x + 20, yy, anchor="w", fill="#E5E7EB", font=("Segoe UI", 12),
                                    text=f"{pos}. {name} — {fmt_time(t)}")
            yy += 24
            pos += 1
        # buttons
        def draw_btn(bx, by, bw, bh, label, cb):
            self.canvas.create_rectangle(bx, by, bx + bw, by + bh, fill="#FFD166", outline="", width=0)
            self.canvas.create_text(bx + bw/2, by + bh/2, fill="#1F2937", font=("Segoe UI", 12, "bold"), text=label)
            self.home_buttons.append((bx, by, bx + bw, by + bh, cb, label))
        # reuse simple click system
        self.home_buttons = []
        draw_btn(x + 20, yy + 10, 160, 36, "Race Again (R)", self.start_race)
        draw_btn(x + 200, yy + 10, 120, 36, "Home", self.to_home)

    # -------------
    # Home actions
    # -------------
    def dec_bots(self):
        self.bot_count = max(0, self.bot_count - 1)
        self.draw_home()

    def inc_bots(self):
        self.bot_count = min(5, self.bot_count + 1)
        self.draw_home()

    def toggle_rm(self):
        self.reduced_motion = not self.reduced_motion
        self.draw_home()


if __name__ == "__main__":
    root = tk.Tk()
    game = Game(root)
    root.mainloop()