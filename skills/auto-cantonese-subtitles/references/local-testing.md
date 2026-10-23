# 本地测试入口

运行前先按 [environment-recovery.md](environment-recovery.md) 检查已有环境和本机记录；按 [task-progress.md](task-progress.md) 显示并保存进度。下面的安装说明只用于缺失的部分，不能因模型在缓存里却没有在新环境中安装依赖，就重复创建整套环境。下载完成、依赖导入通过、模型加载成功分别记录。

需要 Python、FFmpeg、FFprobe。Whisper 使用 `faster-whisper`（完整版 large-v3 的 CTranslate2 权重）；Qwen 使用 `qwen-asr`、PyTorch，ForcedAligner为可选项。先执行[runtime-setup.md](runtime-setup.md)的统一脚本，不自行拼装安装顺序；依赖记录在`scripts/requirements.txt`。环境检查包含真实音频解码，随后分别验证模型加载与短音频识别。

准备阶段用 huggingface_hub.snapshot_download 下载以下仓库，记录快照路径：
- Systran/faster-whisper-large-v3
- Qwen/Qwen3-ASR-1.7B
- 可选：Qwen/Qwen3-ForcedAligner-0.6B，仅在确需额外原文对齐时准备

模型所有分片、索引、配置、词表/处理器必须完整。下载函数成功返回或目录存在不足以证明完整，需实际加载。不要用 large-v3-turbo 替换 large-v3 来冒充同一基准。

脚本通过本地路径加载并设置离线环境；输出原始识别 JSON。示例：

```sh
python scripts/transcribe.py media.mp4 --engine whisper --model /absolute/whisper-snapshot --output /absolute/run/whisper.json
python scripts/transcribe.py media.mp4 --engine qwen --model /absolute/qwen-snapshot --output /absolute/run/qwen.json
```

CPU 是可移植默认项；确认可用后可选择 cuda/mps（Qwen）或 cuda（Whisper）。Mac 的 MPS 不可用可能来自沙箱，须实际检查，不承诺运行速度。每次依次运行避免内存占用叠加。`transcribe.py`是180秒内的短样本入口，长片使用下面的固定窗口入口。

Qwen默认仅输出文字用于交叉校准，不提供词时间。额外对齐时加`--aligner /absolute/aligner-snapshot`，仍需检查零时长和错位。`transcribe.py`为每次原始结果生成`.progress.json`，分别记录探测、解码、加载、推理和输出。不可在同一环境后台安装依赖的同时运行识别。

## 长片固定入口

在已通过自检的实际解释器下执行：

```sh
python scripts/transcribe_long.py /absolute/source.mp4 \
  --manifest /absolute/models.local.json --output-dir /absolute/run
```

默认120秒窗口、8秒重叠，Qwen按本次可用设备选择MPS/CUDA/CPU，也可指定`--qwen-device cpu`等。窗口不超过180秒。先提取完整音轨再切窗，Python生成`window-0000.wav`、`whisper-0000.json`、`qwen-0000.json`等唯一名称，不依赖shell变量展开。同一模型在处理当前批次各窗口时复用内存中的权重，两模型依次处理以控制占用。

再次执行相同命令会检查素材大小/修改时间、模型路径、关键版本文件及脚本指纹，匹配才继续复用已完成的窗口结果；变化时要求使用新输出目录。损坏或不匹配的单窗结果会另存，不覆盖后重跑。任务有独占锁，日志和窗口进度保存在输出目录。

`combined.raw.json`包含映射至原始解码音轨的Whisper段/词时间、各Qwen窗口原文、原容器信息及`boundary_reviews`。自动去重只针对相邻窗口中正文相同且时间边界近乎一致的片段；短回答、同文不同时间及存在差异的接缝保留。Qwen文字没有词时间，文本重复候选不能当作已确认重复直接删除。Agent必须阅读接缝记录、校准重叠或截断内容，再做最终语义分句及SRT导出。

原音轨时间零点不一定等于视频容器零点，合并结果明确保留该限制；不得把加上窗口偏移等同于播放验收。CPU双模型耗时较长不等于故障，先报告实际设备、已完成窗口及进展；预计剩余时间只能根据本次实际速度估计，不对所有电脑承诺20分钟等固定耗时。8秒文件不增长不足以单独认定下载卡死。

首次测试不注入背景词，随后可用 `--context` 注入相同背景做增强对照。Whisper prompt 与 Qwen context 不是硬性热词约束。保留无提示版本，避免看似校准却是强制猜词。

改写与画幅语义分句后的 cues JSON 用 export_srt.py 导出，必须包含 subtitle_profile.layout，或通过 --profile / --layout 明确成片预设。新增调用及 schema 见 [subtitle-layout.md](subtitle-layout.md)。导出器强制正文单行，清理句读并保护必要数值/专名符号，超宽须重新分句和定位。结构检查不能证明文本忠实性、语义分句、轮次或声画同步正确。

## 非语音保护

已观察到 Qwen 直接处理纯静音时可能生成“係。”。测试入口使用本地 Silero VAD，纯非语音输入不送入识别；保留语音区间供检查。VAD 也可能漏掉低音量发言，若用户确认有声音而输出为空，必须回听并复核检测设置，不把空结果当作正确。混合语音/静音仍须检查凭空补字。
