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


class TestAimSystem:
    """瞄准系统测试套件"""

    def setup_method(self):
        """每个测试前的初始化"""
        self.system = AimSystem()

    def test_case_01(self):
        """站立静止腰射时，扩散等于武器基础扩散且不为固定常数"""
        spread = self.system.calculate_spread()
        assert spread == pytest.approx(self.system.weapon["base_spread"])
        assert spread > 0

    def test_case_02(self):
        """蹲下时扩散低于站立"""
        standing = self.system.calculate_spread()
        self.system.set_player_state(stance="crouching")
        crouching = self.system.calculate_spread()
        assert crouching < standing

    def test_case_03(self):
        """移动时扩散大于静止"""
        still = self.system.calculate_spread()
        self.system.set_player_state(moving=True)
        moving = self.system.calculate_spread()
        assert moving > still

    def test_case_04(self):
        """跳跃时扩散大于单纯移动"""
        self.system.set_player_state(moving=True)
        moving = self.system.calculate_spread()
        self.system.set_player_state(jumping=True)
        jumping = self.system.calculate_spread()
        assert jumping > moving

    def test_case_05(self):
        """连续射击扩散叠加，且不超过武器最大扩散"""
        before = self.system.calculate_spread()
        self.system.fire()
        after_one = self.system.calculate_spread()
        for _ in range(9):
            self.system.fire()
        after_ten = self.system.calculate_spread()
        assert after_one > before
        assert after_ten > after_one
        for _ in range(200):
            self.system.fire()
        assert self.system.calculate_spread() <= self.system.weapon["max_spread"] + 1e-9

    def test_case_06(self):
        """完全开镜后扩散低于腰射"""
        hipfire = self.system.calculate_spread()
        self.system.start_ads()
        for _ in range(100):
            self.system.update(0.02)
        assert self.system.ads_progress == 1.0
        assert self.system.calculate_spread() < hipfire

    def test_case_07(self):
        """后坐力为固定模式：重置后同一发偏移一致，可练习压枪"""
        first_run = [self.system.recoil_pattern(i) for i in range(8)]
        self.system.reset()
        second_run = [self.system.recoil_pattern(i) for i in range(8)]
        assert first_run == second_run
        # 模式内各发不完全相同（有固定弹道而非单调/随机）
        assert len(set(first_run)) > 1

    def test_case_08(self):
        """后坐力恢复是渐进的：停止射击后逐步回位而非瞬间归零"""
        self.system.fire()
        peak = abs(self.system.recoil_offset[1])
        assert peak > 0
        self.system.update(0.01)
        mid = abs(self.system.recoil_offset[1])
        assert 0 < mid < peak
        for _ in range(1000):
            self.system.update(0.05)
        assert self.system.recoil_offset[1] == 0.0

    def test_case_09(self):
        """开镜有过渡动画：进度随时间从 0 平滑到 1，而非瞬间切换"""
        assert self.system.ads_transition() == 0.0
        self.system.start_ads()
        assert self.system.ads_transition() < 1.0  # 开镜指令后不是瞬间完成
        self.system.update(self.system.weapon["ads_time"] / 2)
        mid = self.system.ads_transition()
        assert 0.0 < mid < 1.0
        self.system.update(self.system.weapon["ads_time"])
        assert self.system.ads_transition() == 1.0

    def test_case_10(self):
        """开镜期间移动速度降低"""
        hip_speed = self.system.movement_speed()
        self.system.start_ads()
        for _ in range(100):
            self.system.update(0.02)
        ads_speed = self.system.movement_speed()
        assert ads_speed < hip_speed

    def test_case_11(self):
        """扩散恢复速度按武器配置：冲锋枪快于狙击枪，且恢复确实生效"""
        self.system.set_weapon("smg")
        smg_rate = self.system.spread_recovery()
        self.system.set_weapon("sniper")
        sniper_rate = self.system.spread_recovery()
        assert smg_rate > sniper_rate
        # 恢复行为验证：射击后扩散随时间下降
        self.system.fire()
        before = self.system.calculate_spread()
        self.system.update(0.5)
        after = self.system.calculate_spread()
        assert after < before

    def test_case_12(self):
        """屏息减弱晃动但不完全消除，且有时间限制会自动结束"""
        normal = self.system.sway_amplitude()
        assert self.system.start_breath_hold()
        held = self.system.sway_amplitude()
        assert 0 < held < normal
        # 超过屏息时限后自动结束
        duration = self.system.weapon["breath_hold_duration"]
        for _ in range(int(duration / 0.05) + 2):
            self.system.update(0.05)
        assert not self.system.breath_hold_active

    def test_case_13(self):
        """屏息结束后有手抖惩罚，惩罚随时间消退"""
        normal = self.system.sway_amplitude()
        self.system.start_breath_hold()
        self.system.update(0.1)
        self.system.stop_breath_hold()
        assert self.system.sway_amplitude() > normal
        status = self.system.breath_hold()
        assert status["penalty"]
        assert status["penalty_remaining"] > 0
        for _ in range(200):
            self.system.update(0.05)
        assert self.system.sway_amplitude() == pytest.approx(normal)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
