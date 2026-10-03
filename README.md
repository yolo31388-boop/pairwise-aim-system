# 瞄准系统

## 项目描述

瞄准系统的bug修复项目。核心模块 `aim/system.py` 存在多个已知bug，需要修复后使测试全部通过。

## 已知问题

- 扩散固定不考虑玩家状态
- 后坐力随机不可练习恢复瞬间
- 开镜无过渡不降移速
- 扩散恢复线性不按武器配置
- 屏息无时间限制无副作用

## 项目结构

```
├── aim/system.py       # 核心模块（带bug）
├── tests/test_aim.py    # 验收测试（红态）
├── pyproject.toml          # 项目配置
└── README.md               # 说明文档
```

## 运行测试

```bash
python -m pytest tests/test_aim.py -q
```

修复目标：所有 13 个测试全部通过。

## 技术要求

- 纯Python标准库，不引入第三方依赖
- 不修改测试文件
- 保持现有API接口不变
- 修复所有已知bug
