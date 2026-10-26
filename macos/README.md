# 苹果芯片Mac离线一键安装

给同事的是完整离线ZIP，而不是单独的command或GitHub源码ZIP。完整包包括独立Python、FFmpeg/FFprobe、全部Python依赖wheel、Whisper large-v3、Qwen3-ASR-1.7B、Skill、自检音频和第三方许可。

同事只需完整解压、双击“一键安装.command”，等待检查通过，再在WorkBuddy等Agent新对话中提供素材。macOS首次打开可能要求确认；此版本没有Developer ID签名或公证。不要关闭Gatekeeper或其他安全防护。

适用于Apple Silicon、macOS 14及以上，建议16GB及以上内存。安装时不下载、不用sudo、不装Homebrew、不改系统Python或全局PATH。安装到当前用户的 `Library/Application Support/CantoneseSubtitleSkill`，Skill安装到Codex、WorkBuddy及通用`.agents/skills`目录；已有源码变更先备份。当前Agent的文字校准能力仍然需要。

## 维护者打包

打包脚本使用Python 3.13及以上。准备macOS arm64独立Python发行版，核对上游SHA-256。用同一Python小版本系列创建独立构建环境，先准备NumPy/setuptools/wheel，再把sox 1.5.0包装器构建成wheel；为Skill的固定requirements收集全部兼容wheel。仅在构建机进行源码构建。

将wheel离线安装到干净构建环境并执行 `pip check`，用 `pip freeze --all`保存纯`name==version`锁文件。FFmpeg使用原生解码器和系统VideoToolbox，静态链接FFmpeg库，只依赖macOS系统库；保存对应源码、许可证和完整configure选项。提供常见MP3/WAV/M4A/MP4/MOV等媒体处理能力，不包含libx264等可选第三方编码器，预览可使用系统`h264_videotoolbox`或原生`mpeg4`。

按实际目录运行：

```sh
python3 macos/build_offline.py \
  --runtime /absolute/python-prefix --tools /absolute/tools-directory \
  --wheels /absolute/wheelhouse --lockfile /absolute/requirements.lock \
  --whisper /absolute/whisper-model --qwen /absolute/qwen-model \
  --test-audio /absolute/synthetic-cantonese.wav \
  --ffmpeg-source /absolute/ffmpeg-source.tar.xz --licenses /absolute/licenses-directory \
  --output /absolute/dist --version YYYY.MM.DD-arm64-N
```

tools目录中需有bin/ffmpeg与bin/ffprobe。模型文件必须完整，不能只复制HF缓存的符号链接。自检音频使用合成测试句，不打包用户录音。打包脚本记录每个文件SHA-256，生成ZIP64和整包校验文件；不把模型或私有转写提交Git。

在另一个用户目录和带空格的安装路径完成实际安装，验证离线pip、解码、双模型加载和短音频识别，以及再次安装时的环境/模型复用。还需验证ZIP解压后可执行权限及独立Python链接完整，不能只验证构建目录能运行。
