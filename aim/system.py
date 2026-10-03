"""
瞄准系统 - 核心模块

修复内容：
- 扩散考虑玩家状态（站姿/蹲姿/移动/跳跃）与连续射击累积扩散
- 后坐力为固定可练习模式，松开扳机后渐进恢复而非瞬间回位
- 开镜有过渡动画，且开镜期间降低移动速度
- 扩散按武器配置做指数恢复（冲锋枪快、狙击枪慢），连续射击扩散叠加
- 屏息有时间限制，结束后有手抖惩罚
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math


BASE_MOVE_SPEED = 5.0  # m/s，腰射状态下的基础移动速度

DEFAULT_BREATH_CONFIG = {
    "max_duration": 4.0,        # 屏息最长持续时间（秒）
    "cooldown": 2.0,            # 屏息结束后的冷却时间（秒）
    "regen_rate": 1.0,          # 屏息体力恢复速度（秒/秒）
    "tremor_duration": 3.0,     # 屏息结束后的手抖惩罚时长（秒）
    "hold_sway_multiplier": 0.15,   # 屏息时准星晃动倍率（大幅降低但不归零）
    "tremor_sway_multiplier": 2.5,  # 手抖惩罚期间准星晃动倍率
}

DEFAULT_WEAPONS: Dict[str, Dict] = {
    "smg": {
        "base_spread": 0.012,
        "crouch_multiplier": 0.75,
        "move_penalty": 0.010,
        "air_penalty": 0.030,
        "bloom_per_shot": 0.003,
        "max_bloom": 0.045,
        "spread_recovery_rate": 6.0,   # 冲锋枪扩散恢复快
        "recoil_pattern": [
            (0.00, 0.8), (0.10, 0.9), (-0.10, 0.9),
            (0.15, 0.8), (-0.15, 0.8), (0.00, 0.7),
        ],
        "recoil_scale": 0.010,
        "recoil_recovery_speed": 6.0,
        "ads_time": 0.20,
        "ads_move_multiplier": 0.85,
        "ads_spread_multiplier": 0.60,
        "sway_amplitude": 0.003,
    },
    "rifle": {
        "base_spread": 0.015,
        "crouch_multiplier": 0.70,
        "move_penalty": 0.015,
        "air_penalty": 0.045,
        "bloom_per_shot": 0.005,
        "max_bloom": 0.060,
        "spread_recovery_rate": 3.5,
        "recoil_pattern": [
            (0.00, 1.0), (0.05, 1.1), (-0.08, 1.2), (0.12, 1.1),
            (-0.15, 1.0), (0.18, 0.9), (-0.12, 0.9), (0.05, 0.8),
        ],
        "recoil_scale": 0.012,
        "recoil_recovery_speed": 4.0,
        "ads_time": 0.25,
        "ads_move_multiplier": 0.75,
        "ads_spread_multiplier": 0.50,
        "sway_amplitude": 0.004,
    },
    "sniper": {
        "base_spread": 0.030,
        "crouch_multiplier": 0.60,
        "move_penalty": 0.040,
        "air_penalty": 0.090,
        "bloom_per_shot": 0.020,
        "max_bloom": 0.080,
        "spread_recovery_rate": 1.2,   # 狙击枪扩散恢复慢
        "recoil_pattern": [(0.00, 2.5), (0.05, 1.5), (-0.05, 0.8)],
        "recoil_scale": 0.020,
        "recoil_recovery_speed": 2.0,
        "ads_time": 0.40,
        "ads_move_multiplier": 0.50,
        "ads_spread_multiplier": 0.05,
        "sway_amplitude": 0.006,
    },
}


@dataclass
class Config:
    """配置项"""
    pass


class AimSystem:
    """瞄准系统核心类"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        custom_weapons = self.config.get("weapons", {})
        self.weapons: Dict[str, Dict] = {}
        for name, defaults in DEFAULT_WEAPONS.items():
            merged = dict(defaults)
            merged.update(custom_weapons.get(name, {}))
            self.weapons[name] = merged
        for name, weapon in custom_weapons.items():
            if name not in self.weapons:
                self.weapons[name] = dict(weapon)
        breath = dict(DEFAULT_BREATH_CONFIG)
        breath.update(self.config.get("breath", {}))
        self.breath_config = breath
        self._state = {}
        self._history: List[Dict] = []
        self.reset()

    # ------------------------------------------------------------------
    # 状态设置
    # ------------------------------------------------------------------
    def reset(self):
        """重置状态"""
        self._state = {
            "weapon": "rifle",
            "stance": "standing",      # standing / crouching
            "moving": False,
            "airborne": False,
            "bloom": 0.0,              # 连续射击累积的额外扩散
            "shots_fired": 0,
            "recoil_index": 0,
            "recoil_offset": [0.0, 0.0],
            "ads": False,
            "ads_progress": 0.0,
            "breath_holding": False,
            "breath_remaining": self.breath_config["max_duration"],
            "breath_cooldown_remaining": 0.0,
            "tremor_remaining": 0.0,
            "sway_time": 0.0,
        }
        self._history = []

    def set_weapon(self, name: str):
        if name not in self.weapons:
            raise ValueError("未知武器: %s" % name)
        self._state["weapon"] = name

    def set_stance(self, stance: str):
        if stance not in ("standing", "crouching"):
            raise ValueError("未知姿态: %s" % stance)
        self._state["stance"] = stance

    def set_moving(self, moving: bool):
        self._state["moving"] = bool(moving)

    def set_airborne(self, airborne: bool):
        self._state["airborne"] = bool(airborne)

    @property
    def weapon_config(self) -> Dict:
        return self.weapons[self._state["weapon"]]

    # ------------------------------------------------------------------
    # 扩散
    # ------------------------------------------------------------------
    def calculate_spread(self) -> float:
        """当前准星扩散：基础值 + 姿态/移动/跳跃修正 + 累积扩散，开镜按比例收敛"""
        cfg = self.weapon_config
        spread = cfg["base_spread"]
        if self._state["stance"] == "crouching":
            spread *= cfg["crouch_multiplier"]
        if self._state["moving"]:
            spread += cfg["move_penalty"]
        if self._state["airborne"]:
            spread += cfg["air_penalty"]
        spread += self._state["bloom"]
        progress = self._state["ads_progress"]
        spread *= 1.0 - (1.0 - cfg["ads_spread_multiplier"]) * progress
        return spread

    def current_bloom(self) -> float:
        """当前连续射击累积的额外扩散"""
        return self._state["bloom"]

    def spread_recovery(self, dt: float) -> float:
        """扩散恢复：按武器配置速率做指数衰减（非线性），连续射击的累积会叠加"""
        rate = self.weapon_config["spread_recovery_rate"]
        bloom = self._state["bloom"] * math.exp(-rate * dt)
        self._state["bloom"] = 0.0 if bloom < 1e-6 else bloom
        return self._state["bloom"]

    # ------------------------------------------------------------------
    # 射击与后坐力
    # ------------------------------------------------------------------
    def recoil_pattern(self, weapon: Optional[str] = None) -> List[Tuple[float, float]]:
        """武器固定的后坐力模式（确定性，可练习压枪）"""
        cfg = self.weapons[weapon] if weapon else self.weapon_config
        return list(cfg["recoil_pattern"])

    def fire(self) -> Dict:
        """开一枪：累积扩散叠加，后坐力按固定模式施加"""
        cfg = self.weapon_config
        pattern = cfg["recoil_pattern"]
        index = self._state["recoil_index"]
        dx, dy = pattern[index % len(pattern)]
        scale = cfg["recoil_scale"]
        self._state["recoil_offset"][0] += dx * scale
        self._state["recoil_offset"][1] += dy * scale
        self._state["recoil_index"] = index + 1
        self._state["shots_fired"] += 1
        self._state["bloom"] = min(
            cfg["max_bloom"], self._state["bloom"] + cfg["bloom_per_shot"]
        )
        record = {
            "shot": self._state["shots_fired"],
            "spread": self.calculate_spread(),
            "recoil_offset": tuple(self._state["recoil_offset"]),
        }
        self._history.append(record)
        return record

    def recoil_offset(self) -> Tuple[float, float]:
        """当前后坐力偏移"""
        return tuple(self._state["recoil_offset"])

    # ------------------------------------------------------------------
    # 开镜（ADS）
    # ------------------------------------------------------------------
    def set_ads(self, active: bool):
        self._state["ads"] = bool(active)
        if not active and self._state["breath_holding"]:
            self._end_breath_hold()

    def ads_transition(self) -> Tuple[float, float]:
        """返回 (开镜进度 0~1, 当前移动速度倍率)，开镜越深移动越慢"""
        progress = self._state["ads_progress"]
        multiplier = self.weapon_config["ads_move_multiplier"]
        move_multiplier = 1.0 + (multiplier - 1.0) * progress
        return progress, move_multiplier

    def movement_speed(self) -> float:
        """当前移动速度，开镜时按过渡进度降低"""
        _, move_multiplier = self.ads_transition()
        return BASE_MOVE_SPEED * move_multiplier

    # ------------------------------------------------------------------
    # 屏息
    # ------------------------------------------------------------------
    def breath_hold(self, active: bool = True) -> bool:
        """开始/结束屏息。需处于开镜状态、冷却结束且体力充足。返回是否成功"""
        if active:
            if not self._state["ads"]:
                return False
            if self._state["breath_cooldown_remaining"] > 0.0:
                return False
            if self._state["breath_remaining"] <= 0.0:
                return False
            self._state["breath_holding"] = True
            return True
        if self._state["breath_holding"]:
            self._end_breath_hold()
        return True

    def _end_breath_hold(self):
        """结束屏息：进入冷却并触发手抖惩罚"""
        self._state["breath_holding"] = False
        self._state["breath_cooldown_remaining"] = self.breath_config["cooldown"]
        self._state["tremor_remaining"] = self.breath_config["tremor_duration"]

    def is_breath_holding(self) -> bool:
        return self._state["breath_holding"]

    def is_trembling(self) -> bool:
        return self._state["tremor_remaining"] > 0.0

    def sway_offset(self) -> Tuple[float, float]:
        """当前准星晃动偏移。屏息时大幅减弱（不归零），手抖惩罚期间加剧"""
        cfg = self.weapon_config
        amplitude = cfg["sway_amplitude"]
        if self._state["breath_holding"]:
            amplitude *= self.breath_config["hold_sway_multiplier"]
        elif self._state["tremor_remaining"] > 0.0:
            amplitude *= self.breath_config["tremor_sway_multiplier"]
        t = self._state["sway_time"]
        x = amplitude * math.sin(2.0 * math.pi * 0.50 * t)
        y = amplitude * math.sin(2.0 * math.pi * 0.83 * t + 1.3)
        return x, y

    # ------------------------------------------------------------------
    # 帧更新
    # ------------------------------------------------------------------
    def update(self, dt: float):
        """每帧更新：开镜过渡、后坐力恢复、扩散恢复、屏息计时"""
        if dt <= 0.0:
            return
        cfg = self.weapon_config

        # 开镜过渡动画：按 ads_time 渐进，而非瞬间切换
        target = 1.0 if self._state["ads"] else 0.0
        step = dt / cfg["ads_time"]
        progress = self._state["ads_progress"]
        if progress < target:
            progress = min(target, progress + step)
        elif progress > target:
            progress = max(target, progress - step)
        self._state["ads_progress"] = progress

        # 后坐力渐进恢复：指数衰减回初始位置，完全恢复后连发模式从头开始
        speed = cfg["recoil_recovery_speed"]
        factor = math.exp(-speed * dt)
        offset = self._state["recoil_offset"]
        offset[0] *= factor
        offset[1] *= factor
        if abs(offset[0]) < 1e-6 and abs(offset[1]) < 1e-6:
            offset[0] = 0.0
            offset[1] = 0.0
            self._state["recoil_index"] = 0

        # 扩散恢复（按武器速率，指数衰减）
        self.spread_recovery(dt)

        # 屏息计时：到时自动结束并触发手抖；非屏息时冷却/手抖倒计时、体力恢复
        breath = self.breath_config
        if self._state["breath_holding"]:
            self._state["breath_remaining"] -= dt
            if self._state["breath_remaining"] <= 0.0:
                self._state["breath_remaining"] = 0.0
                self._end_breath_hold()
        else:
            self._state["breath_cooldown_remaining"] = max(
                0.0, self._state["breath_cooldown_remaining"] - dt
            )
            self._state["tremor_remaining"] = max(
                0.0, self._state["tremor_remaining"] - dt
            )
            if self._state["breath_cooldown_remaining"] <= 0.0:
                self._state["breath_remaining"] = min(
                    breath["max_duration"],
                    self._state["breath_remaining"] + breath["regen_rate"] * dt,
                )

        self._state["sway_time"] += dt
