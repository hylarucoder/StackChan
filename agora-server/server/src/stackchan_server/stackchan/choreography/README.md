# 编舞编译器 (MP3 → dance.json)

把一个音频文件编译成 StackChan 固件可直接播放的关键帧序列 (`dance.json`)。

这是 "MP3 → 编舞" 链路的**第 1 步**（纯离线、不碰硬件）。生成的 JSON 与固件
`stackchan/json/json_helper.cpp::parse_sequence_from_json` 的格式完全一致，可经
`0x14 DanceSequence` WebSocket 通道下发，或交给 `DanceModifier` 播放。

## 安装

```bash
uv sync --group choreo          # 在 stackchan-server/server 下
```

## 用法

```bash
# 整首歌
stackchan-choreograph song.mp3 -o dance.json

# 只编排某一段（demo 常用，payload 更小）
stackchan-choreograph song.mp3 --start 30 --duration 20 --pretty

# 强制 BPM / 放宽摆头幅度
stackchan-choreograph song.mp3 --bpm 128 --yaw-max 800
```

输出示例：

```
tempo=92.3 BPM  duration=129.7s  beats=193  keyframes=195  motion=126.1s  sections[loud:49, mid:67, quiet:77]
wrote 195 keyframes -> dance.json (70.2 KB)
```

## 工作原理 (A2：节拍网格 + 参数化舞步库)

1. `analysis.py` — librosa 提取 **BPM + 每个节拍时间点 + 每拍能量(0~1) + 段落(quiet/mid/loud)**。
2. `moves.py` — 一组**参数化舞步**，把"一个节拍"映射成"一个关键帧"：
   - `groove` 走 2D 摆头/抬头姿势循环，小节首拍可叠加 `accent`，长音/强段落可触发 `look_up`；
   - 能量越大 → 摆幅越大、速度越快；嘴巴张合跟随能量（廉价"唱歌"对口型）；
   - 每小节循环一次 RGB 灯色。
3. `compiler.py` — 走完节拍网格，逐拍选舞步并落帧；首尾各加一个 `home` 中立姿态。
4. `schema.py` — 关键帧数据模型，序列化时**钳制到安全角度范围**。

## 安全范围（单位 0.1°，来自固件 `hal/hal_servo.cpp`）

| 轴 | 物理极限 | 默认生成范围 |
|---|---|---|
| yaw  | ±1280 (±128°) | ±450 (±45°) |
| pitch | 30~870 (相对 zero 可为负=下俯) | -200~+250 |
| speed | 0~1000 | 80~1000 |

`MotionLimits` 里硬上限永不被突破，即使 `--yaw-max` 调大。

## 关键帧格式

```json
{
  "leftEye":  {"x":5,"y":0,"rotation":0,"weight":67,"size":0},
  "rightEye": {"x":5,"y":0,"rotation":0,"weight":67,"size":0},
  "mouth":    {"x":0,"y":0,"rotation":0,"weight":54,"size":0},
  "yawServo":   {"angle":180,"speed":793},
  "pitchServo": {"angle":-40,"speed":793},
  "leftRgbColor":"#FF8C00",
  "rightRgbColor":"#FF8C00",
  "durationMs":650
}
```

## 下发与播放

- **第 2 步已实现**：服务端提供 `WS /dance/ws` 和 `POST /trigger/dance/push`，
  把整段 keyframe JSON 包成 `0x14 DanceSequence` 二进制帧推给设备的独立 dance 通道。
  固件侧 `hal/hal_dance_ws.cpp` 接收后交给现有 `DanceModifier` 播放。
- **第 3 步（架构 B / B1）**：服务端把 MP3 解码成下行音频流推给设备，与编舞同时起播
  并做校时，实现真机"边放歌边跳"。
