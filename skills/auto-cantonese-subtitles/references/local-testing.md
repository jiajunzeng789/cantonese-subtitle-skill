# 本地测试入口

运行前先按 [environment-recovery.md](environment-recovery.md) 检查已有环境和本机记录；按 [task-progress.md](task-progress.md) 显示并保存进度。下面的安装说明只用于缺失的部分，不能因模型在缓存里却没有在新环境中安装依赖，就重复创建整套环境。下载完成、依赖导入通过、模型加载成功分别记录。

需要 Python、FFmpeg、FFprobe。Whisper 使用 `faster-whisper`（完整版 large-v3 的 CTranslate2 权重）；Qwen 使用 `qwen-asr`、PyTorch 及 Qwen3-ForcedAligner-0.6B。可用独立环境安装，勿修改系统 Python。依赖记录在 `scripts/requirements.txt`，安装时核对 Python/平台兼容性。`scripts/prepare_models.py --manifest /absolute/models.json` 下载并记录快照，实际加载另行验证。

准备阶段用 huggingface_hub.snapshot_download 下载以下仓库，记录快照路径：
- Systran/faster-whisper-large-v3
- Qwen/Qwen3-ASR-1.7B
- Qwen/Qwen3-ForcedAligner-0.6B

模型所有分片、索引、配置、词表/处理器必须完整。下载函数成功返回或目录存在不足以证明完整，需实际加载。不要用 large-v3-turbo 替换 large-v3 来冒充同一基准。

脚本通过本地路径加载并设置离线环境；输出原始识别 JSON。示例：

```sh
python scripts/transcribe.py media.mp4 --engine whisper --model /absolute/whisper-snapshot --output /absolute/run/whisper.json
python scripts/transcribe.py media.mp4 --engine qwen --model /absolute/qwen-snapshot --aligner /absolute/aligner-snapshot --output /absolute/run/qwen.json
```

CPU 是可移植默认项；确认可用后可选择 cuda/mps（Qwen）或 cuda（Whisper）。Mac 的 MPS 不可用可能来自沙箱，须实际检查，不承诺运行速度。每次依次运行避免内存占用叠加。长媒体按句边界或带重叠的语音窗口处理并去重，首版脚本先用于约30～120秒的测试样本，不宣称已支持任意长度素材。

首次测试不注入背景词，随后可用 `--context` 注入相同背景做增强对照。Whisper prompt 与 Qwen context 不是硬性热词约束。保留无提示版本，避免看似校准却是强制猜词。

改写与画幅语义分句后的 cues JSON 用 export_srt.py 导出，必须包含 subtitle_profile.layout，或通过 --profile / --layout 明确成片预设。新增调用及 schema 见 [subtitle-layout.md](subtitle-layout.md)。导出器强制正文单行，清理句读并保护必要数值/专名符号，超宽须重新分句和定位。结构检查不能证明文本忠实性、语义分句、轮次或声画同步正确。

## 非语音保护

已观察到 Qwen 直接处理纯静音时可能生成“係。”。测试入口使用本地 Silero VAD，纯非语音输入不送入识别；保留语音区间供检查。VAD 也可能漏掉低音量发言，若用户确认有声音而输出为空，必须回听并复核检测设置，不把空结果当作正确。混合语音/静音仍须检查凭空补字。
