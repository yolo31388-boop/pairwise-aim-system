"""
瞄准系统 - 验收测试

运行方式：python -m pytest tests/test_aim.py -q
共 13 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aim.system import AimSystem


def _max_sway(system, seconds=4.0, dt=1.0 / 60.0):
    """采样一段时间内的最大准星晃动幅度"""
    peak = 0.0
    steps = int(seconds / dt)
    for _ in range(steps):
        system.update(dt)
        x, y = system.sway_offset()
        peak = max(peak, abs(x), abs(y))
    return peak


class TestAimSystem:
    """瞄准系统测试套件"""

    def setup_method(self):
        """每个测试前的初始化"""
        self.system = AimSystem()

    def test_case_01(self):
        """扩散考虑姿态：蹲下比站立扩散更小，且不再是固定值"""
        standing = self.system.calculate_spread()
        self.system.set_stance("crouching")
        crouching = self.system.calculate_spread()
        assert crouching < standing
        assert standing != 0.02  # 不再是写死的固定值

    def test_case_02(self):
        """扩散考虑移动/跳跃：移动惩罚大于静止，跳跃惩罚最大"""
        still = self.system.calculate_spread()
        self.system.set_moving(True)
        moving = self.system.calculate_spread()
        self.system.set_airborne(True)
        airborne = self.system.calculate_spread()
        assert moving > still
        assert airborne > moving

    def test_case_03(self):
        """连续射击扩散叠加：打十发的扩散大于打一发"""
        self.system.fire()
        spread_one = self.system.calculate_spread()
        for _ in range(9):
            self.system.fire()
        spread_ten = self.system.calculate_spread()
        assert spread_ten > spread_one
        assert self.system.current_bloom() > 0.0

    def test_case_04(self):
        """后坐力模式固定可练习：两次获取的模式一致，连射偏移可复现"""
        pattern_a = self.system.recoil_pattern()
        pattern_b = self.system.recoil_pattern()
        assert pattern_a == pattern_b
        assert len(pattern_a) > 1
        offsets_first = [self.system.fire()["recoil_offset"] for _ in range(5)]
        self.system.reset()
        offsets_second = [self.system.fire()["recoil_offset"] for _ in range(5)]
        assert offsets_first == offsets_second

    def test_case_05(self):
        """后坐力渐进恢复：偏移随时间逐渐归零，而非瞬间回位"""
        for _ in range(3):
            self.system.fire()
        x0, y0 = self.system.recoil_offset()
        mag0 = abs(x0) + abs(y0)
        assert mag0 > 0.0
        self.system.update(0.05)
        x1, y1 = self.system.recoil_offset()
        mag1 = abs(x1) + abs(y1)
        assert 0.0 < mag1 < mag0  # 有恢复过程，不是瞬间归零
        self.system.update(10.0)
        x2, y2 = self.system.recoil_offset()
        assert abs(x2) + abs(y2) == 0.0

    def test_case_06(self):
        """开镜有过渡动画：开镜进度随时间从 0 渐进到 1"""
        self.system.set_ads(True)
        assert self.system.ads_transition()[0] == 0.0
        self.system.update(0.1)
        progress_mid = self.system.ads_transition()[0]
        assert 0.0 < progress_mid < 1.0
        self.system.update(1.0)
        assert self.system.ads_transition()[0] == 1.0

    def test_case_07(self):
        """开镜降低移动速度：开镜后移速低于腰射"""
        hipfire_speed = self.system.movement_speed()
        self.system.set_ads(True)
        self.system.update(1.0)
        ads_speed = self.system.movement_speed()
        assert ads_speed < hipfire_speed

    def test_case_08(self):
        """扩散恢复按武器配置：冲锋枪恢复比狙击枪快"""
        results = {}
        for weapon in ("smg", "sniper"):
            self.system.reset()
            self.system.set_weapon(weapon)
            for _ in range(2):
                self.system.fire()
            before = self.system.current_bloom()
            self.system.update(0.5)
            after = self.system.current_bloom()
            results[weapon] = after / before  # 剩余比例
        assert results["smg"] < results["sniper"]

    def test_case_09(self):
        """扩散恢复非线性：指数衰减，相同时间间隔按相同比例减少"""
        for _ in range(5):
            self.system.fire()
        b0 = self.system.current_bloom()
        self.system.update(0.2)
        b1 = self.system.current_bloom()
        self.system.update(0.2)
        b2 = self.system.current_bloom()
        assert b0 > b1 > b2 > 0.0
        # 指数衰减：等时长衰减比例一致（线性恢复则衰减量一致）
        assert (b1 / b0) == pytest.approx(b2 / b1, rel=1e-6)
        assert (b0 - b1) > (b1 - b2)

    def test_case_10(self):
        """开镜降低扩散：完全开镜后的扩散显著小于腰射"""
        hipfire = self.system.calculate_spread()
        self.system.set_ads(True)
        self.system.update(1.0)
        ads = self.system.calculate_spread()
        assert ads < hipfire * 0.6

    def test_case_11(self):
        """屏息减弱准星晃动：屏息时晃动幅度显著降低但不完全为零"""
        self.system.set_ads(True)
        self.system.update(1.0)
        normal_sway = _max_sway(self.system)
        assert self.system.breath_hold(True)
        hold_sway = _max_sway(self.system)
        assert 0.0 < hold_sway < normal_sway * 0.5

    def test_case_12(self):
        """屏息有时间限制：超过最长持续时间后自动结束"""
        self.system.set_ads(True)
        self.system.update(1.0)
        assert self.system.breath_hold(True)
        assert self.system.is_breath_holding()
        self.system.update(10.0)  # 远超 4 秒上限
        assert not self.system.is_breath_holding()

    def test_case_13(self):
        """屏息结束有副作用：手抖惩罚期间晃动比正常更大"""
        self.system.set_ads(True)
        self.system.update(1.0)
        normal_sway = _max_sway(self.system)
        assert self.system.breath_hold(True)
        self.system.update(1.0)
        self.system.breath_hold(False)  # 主动结束屏息
        assert self.system.is_trembling()
        tremor_sway = _max_sway(self.system, seconds=2.0)
        assert tremor_sway > normal_sway


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
