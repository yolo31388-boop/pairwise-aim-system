"""
瞄准系统 - 核心模块

已修复的问题：
- 准星扩散考虑玩家状态（站立/蹲下/移动/跳跃）与连续射击累积
- 后坐力为固定模式（可练习压枪），且随时间逐渐恢复
- 开镜有过渡动画，开镜期间降低移动速度
- 扩散恢复速度按武器配置（冲锋枪快、狙击枪慢），连续射击扩散叠加
- 屏息有时间限制，结束后有手抖惩罚
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class Config:
    """配置项"""
    pass


# 各武器的默认参数，可通过 config["weapons"] 覆盖
DEFAULT_WEAPONS: Dict[str, Dict] = {
    "rifle": {
        "base_spread": 0.02,
        "max_spread": 0.12,
        "spread_per_shot": 0.012,
        "spread_recovery_rate": 0.08,
        "recoil_pattern": [
            (0.0, 0.030),
            (0.004, 0.032),
            (-0.003, 0.034),
            (0.005, 0.031),
            (-0.004, 0.029),
            (0.002, 0.028),
        ],
        "recoil_recovery_speed": 0.12,
        "ads_time": 0.25,
        "ads_spread_multiplier": 0.4,
        "ads_move_multiplier": 0.6,
        "sway_amplitude": 0.010,
        "breath_hold_duration": 4.0,
        "breath_penalty_duration": 3.0,
    },
    "smg": {
        "base_spread": 0.025,
        "max_spread": 0.10,
        "spread_per_shot": 0.008,
        "spread_recovery_rate": 0.20,  # 冲锋枪扩散恢复快
        "recoil_pattern": [
            (0.0, 0.018),
            (0.003, 0.019),
            (-0.003, 0.020),
            (0.002, 0.018),
        ],
        "recoil_recovery_speed": 0.20,
        "ads_time": 0.18,
        "ads_spread_multiplier": 0.5,
        "ads_move_multiplier": 0.75,
        "sway_amplitude": 0.008,
        "breath_hold_duration": 3.0,
        "breath_penalty_duration": 2.0,
    },
    "sniper": {
        "base_spread": 0.05,
        "max_spread": 0.20,
        "spread_per_shot": 0.05,
        "spread_recovery_rate": 0.03,  # 狙击枪扩散恢复慢
        "recoil_pattern": [
            (0.0, 0.120),
            (0.010, 0.100),
        ],
        "recoil_recovery_speed": 0.05,
        "ads_time": 0.40,
        "ads_spread_multiplier": 0.05,
        "ads_move_multiplier": 0.40,
        "sway_amplitude": 0.020,
        "breath_hold_duration": 6.0,
        "breath_penalty_duration": 4.0,
    },
}

# 姿态对扩散的倍率
STANCE_SPREAD_MULTIPLIERS = {
    "standing": 1.0,
    "crouching": 0.7,
    "prone": 0.5,
}
MOVE_SPREAD_MULTIPLIER = 1.6    # 移动惩罚
JUMP_SPREAD_MULTIPLIER = 2.5    # 跳跃惩罚（与移动叠加）

BREATH_HOLD_SWAY_MULTIPLIER = 0.2    # 屏息时晃动减弱（但不完全消除）
BREATH_PENALTY_SWAY_MULTIPLIER = 2.5  # 屏息结束后手抖惩罚


class AimSystem:
    """瞄准系统核心类"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.weapons: Dict[str, Dict] = {name: dict(w) for name, w in DEFAULT_WEAPONS.items()}
        self.weapons.update(self.config.get("weapons", {}))
        self.base_move_speed = self.config.get("base_move_speed", 1.0)
        self._state = {}
        self._history = []
        self.reset()

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------
    def reset(self):
        """重置状态"""
        self._state = {}
        self._history = []
        self.weapon_name = self.config.get("default_weapon", "rifle")
        self.stance = "standing"
        self.moving = False
        self.jumping = False
        self.ads = False
        self.ads_progress = 0.0
        self.consecutive_shots = 0
        self._bloom = 0.0  # 连续射击累积的扩散
        self.recoil_offset = [0.0, 0.0]
        self.breath_hold_active = False
        self.breath_hold_remaining = 0.0
        self.breath_penalty_timer = 0.0
        self.time = 0.0

    @property
    def weapon(self) -> Dict:
        return self.weapons[self.weapon_name]

    def set_weapon(self, name: str):
        if name not in self.weapons:
            raise ValueError(f"未知武器: {name}")
        self.weapon_name = name
        self._bloom = 0.0
        self.consecutive_shots = 0
        self.recoil_offset = [0.0, 0.0]

    def set_player_state(self, stance: Optional[str] = None,
                         moving: Optional[bool] = None,
                         jumping: Optional[bool] = None):
        if stance is not None:
            self.stance = stance
        if moving is not None:
            self.moving = moving
        if jumping is not None:
            self.jumping = jumping

    # ------------------------------------------------------------------
    # 准星扩散：考虑玩家状态 + 连续射击累积
    # ------------------------------------------------------------------
    def calculate_spread(self, player_state: Optional[Dict] = None) -> float:
        """计算当前准星扩散。

        考虑姿态（站立/蹲下/趴下）、移动、跳跃、开镜程度以及
        连续射击累积的扩散（bloom），结果不超过武器最大扩散。
        """
        state = player_state or {}
        stance = state.get("stance", self.stance)
        moving = state.get("moving", self.moving)
        jumping = state.get("jumping", self.jumping)

        w = self.weapon
        spread = w["base_spread"]
        spread *= STANCE_SPREAD_MULTIPLIERS.get(stance, 1.0)
        if moving:
            spread *= MOVE_SPREAD_MULTIPLIER
        if jumping:
            spread *= JUMP_SPREAD_MULTIPLIER
        # 开镜按过渡进度插值降低扩散
        ads_mult = 1.0 + (w["ads_spread_multiplier"] - 1.0) * self.ads_progress
        spread *= ads_mult
        # 连续射击累积扩散叠加
        spread += self._bloom
        return min(spread, w["max_spread"])

    def fire(self) -> Dict:
        """开一枪：应用固定后坐力模式并累积扩散。"""
        w = self.weapon
        recoil = self.recoil_pattern(self.consecutive_shots)
        self.recoil_offset[0] += recoil[0]
        self.recoil_offset[1] += recoil[1]
        self._bloom = min(self._bloom + w["spread_per_shot"], w["max_spread"])
        self.consecutive_shots += 1
        result = {
            "shot_index": self.consecutive_shots - 1,
            "recoil": recoil,
            "spread": self.calculate_spread(),
        }
        self._history.append(result)
        return result

    # ------------------------------------------------------------------
    # 后坐力：固定模式（可练习）+ 渐进恢复
    # ------------------------------------------------------------------
    def recoil_pattern(self, shot_index: int = 0) -> Tuple[float, float]:
        """返回第 shot_index 发的后坐力偏移。

        每种武器有固定的后坐力模式，循环使用，玩家可以练习压枪。
        """
        pattern = self.weapon["recoil_pattern"]
        return pattern[shot_index % len(pattern)]

    def recoil_recovery(self, dt: float) -> List[float]:
        """后坐力随时间逐渐恢复（不是瞬间回位）。"""
        speed = self.weapon["recoil_recovery_speed"]
        for axis in (0, 1):
            offset = self.recoil_offset[axis]
            if offset > 0:
                self.recoil_offset[axis] = max(0.0, offset - speed * dt)
            elif offset < 0:
                self.recoil_offset[axis] = min(0.0, offset + speed * dt)
        return self.recoil_offset

    # ------------------------------------------------------------------
    # 开镜：过渡动画 + 降低移动速度
    # ------------------------------------------------------------------
    def start_ads(self):
        self.ads = True

    def stop_ads(self):
        self.ads = False

    def _update_ads(self, dt: float):
        target = 1.0 if self.ads else 0.0
        step = dt / max(self.weapon["ads_time"], 1e-6)
        if self.ads_progress < target:
            self.ads_progress = min(target, self.ads_progress + step)
        elif self.ads_progress > target:
            self.ads_progress = max(target, self.ads_progress - step)

    def ads_transition(self, dt: Optional[float] = None) -> float:
        """开镜过渡进度（0=腰射，1=完全开镜），随时间平滑过渡。"""
        if dt:
            self._update_ads(dt)
        return self.ads_progress

    def movement_speed(self) -> float:
        """当前移动速度，开镜期间按过渡进度降低。"""
        w = self.weapon
        speed = self.base_move_speed
        speed *= 1.0 + (w["ads_move_multiplier"] - 1.0) * self.ads_progress
        if self.stance == "crouching":
            speed *= 0.5
        return speed

    # ------------------------------------------------------------------
    # 扩散恢复：按武器配置的速率恢复
    # ------------------------------------------------------------------
    def spread_recovery(self, dt: Optional[float] = None) -> float:
        """不传 dt 时返回当前武器配置的恢复速率；传 dt 时按速率恢复扩散。"""
        rate = self.weapon["spread_recovery_rate"]
        if dt is None:
            return rate
        self._bloom = max(0.0, self._bloom - rate * dt)
        if self._bloom == 0.0:
            self.consecutive_shots = 0
        return self._bloom

    # ------------------------------------------------------------------
    # 屏息：减弱晃动、有时间限制、结束后手抖惩罚
    # ------------------------------------------------------------------
    def start_breath_hold(self) -> bool:
        """开始屏息。手抖惩罚期间无法屏息。"""
        if self.breath_hold_active or self.breath_penalty_timer > 0:
            return False
        self.breath_hold_active = True
        self.breath_hold_remaining = self.weapon["breath_hold_duration"]
        return True

    def stop_breath_hold(self, exhausted: bool = False):
        """结束屏息并施加手抖惩罚；憋到极限被迫结束时惩罚更重。"""
        if not self.breath_hold_active:
            return
        self.breath_hold_active = False
        self.breath_hold_remaining = 0.0
        penalty = self.weapon["breath_penalty_duration"]
        if exhausted:
            penalty *= 1.5
        self.breath_penalty_timer = max(self.breath_penalty_timer, penalty)

    def sway_amplitude(self) -> float:
        """当前准星晃动幅度。"""
        amplitude = self.weapon["sway_amplitude"]
        if self.breath_hold_active:
            amplitude *= BREATH_HOLD_SWAY_MULTIPLIER
        if self.breath_penalty_timer > 0:
            amplitude *= BREATH_PENALTY_SWAY_MULTIPLIER
        return amplitude

    def breath_hold(self) -> Dict:
        """屏息状态信息。"""
        return {
            "active": self.breath_hold_active,
            "remaining": max(0.0, self.breath_hold_remaining),
            "duration": self.weapon["breath_hold_duration"],
            "penalty": self.breath_penalty_timer > 0,
            "penalty_remaining": max(0.0, self.breath_penalty_timer),
            "sway_amplitude": self.sway_amplitude(),
        }

    # ------------------------------------------------------------------
    # 帧更新
    # ------------------------------------------------------------------
    def update(self, dt: float):
        """每帧更新：推进开镜过渡、扩散恢复、后坐力恢复、屏息计时。"""
        if dt <= 0:
            return
        self._update_ads(dt)
        self.spread_recovery(dt)
        self.recoil_recovery(dt)
        if self.breath_hold_active:
            self.breath_hold_remaining -= dt
            if self.breath_hold_remaining <= 0:
                self.stop_breath_hold(exhausted=True)
        if self.breath_penalty_timer > 0:
            self.breath_penalty_timer = max(0.0, self.breath_penalty_timer - dt)
        self.time += dt
