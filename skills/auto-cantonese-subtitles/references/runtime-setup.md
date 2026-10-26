# 统一安装与自检入口

## 苹果芯片Mac离线一键安装

拿到完整离线ZIP后解压，双击其中“一键安装.command”。包含Python、FFmpeg/FFprobe、全部wheel、两个识别模型及自检音频，不依赖Homebrew、系统Python或现场编译；适用于macOS 14及以上的Apple Silicon。源码仓库中的command不包含这些大文件，不能单独复制当作完整安装包。

安装完成后在Agent新对话中提供素材。运行环境记录位于 `~/.config/cantonese-subtitles/environment.local.json`，包含实际解释器、`tools_bin` 和 `models_manifest`；模型在用户的Application Support下，不一定出现在Hugging Face默认缓存。`prepare_models.py`会先复用安装记录中的完整模型，其他媒体脚本按记录自动启用捆绑工具，无需修改全局PATH。离线安装是环境准备；文字校准仍由Agent完成，使用在线Agent不等于文字也离线。

安装器有校验、日志和恢复入口，已有Skill源文件修改会先备份。安装包未经Developer ID签名/公证，首次打开是否需要macOS额外确认取决于系统与下载来源；不以关闭系统防护作为安装步骤。

所有路径示例都需替换成本机实际路径。以下命令从Skill目录执行；在仓库根目录时给脚本加上 `skills/auto-cantonese-subtitles/` 前缀。安装入口仅依赖标准库，可由已有Python直接启动，无需先手动安装Qwen依赖。

## 1. 检查、复用或安装

```sh
python scripts/setup_runtime.py --workspace /absolute/work --share
```

`--workspace`指定素材任务的工作目录。`--share`将验证通过的解释器记录到个人配置，供其他工作目录及Agent重新检查并复用；不提供它时只保存在本次工作目录。

脚本顺序：读取记录与候选解释器 → 检查关键版本、模块导入、FFmpeg/FFprobe和真实音频解码 → 能通过则复用 → 否则在工作目录`.cantonese-subtitles/runtime`分步安装 → `pip check`及运行自检。候选环境检查失败不会被修改，已有非脚本管理的目标目录不会被覆盖。复用候选包括工作目录或Skill目录内已有的`.venv`，不全盘扫描电脑。

基础工具FFmpeg/FFprobe需要事先可调用。缺少系统工具时明确报告，由Agent按系统正常安装流程准备；本脚本不执行管理员安装，也不修改系统Python。关键版本固定在`requirements.txt`，不自动改成不兼容的最新版本。

常用选项：

| 选项 | 用途 |
|---|---|
| `--python /absolute/runtime/bin/python` | 优先验证用户已知环境；Windows可指定对应`Scripts/python.exe` |
| `--base-python /absolute/python` | 创建新环境时使用的Python，不改变系统默认解释器 |
| `--offline` | 仅验证并复用本地环境，不安装或下载 |
| `--index-url https://.../simple` | 用户或组织指定本次可信HTTPS依赖源 |
| `--fresh --venv /absolute/new-runtime` | 验证全新环境；目标必须不存在，不借用系统site-packages |
| `--timeout 1200` | 每个子步骤最长执行秒数，超时记录失败并结束本次子进程 |

新装默认先检查清华镜像，不可达则提示后采用PyPI；不修改全局pip配置。源码构建仅对已知需要的`sox`包装器在准备好构建工具和NumPy后进行，PyAV、Torch等核心包要求对应平台的预编译文件。macOS选择本机平台包，其他系统默认安装官方CPU版Torch，避免未选择CUDA却下载整套NVIDIA运行库；已有CUDA环境可作为候选接受自检。暂未在其他平台做完整实机验收，不承诺所有Python版本均有相应wheel。

安装时持有独占锁；同一环境不要同时执行其他手工pip安装或模型推理。锁已存在时先核对记录中的原任务是否结束，不能默认删除锁。如果曾中断，正常重跑会复用安装缓存并跳过已满足的包；不会因等待超时就并发启动第二次安装。

## 2. 准备本地模型清单

安装成功后使用脚本输出的实际`python_executable`执行后续命令，不能又回到另一个`python`。

```sh
/absolute/runtime/bin/python scripts/prepare_models.py --manifest /absolute/work/.cantonese-subtitles/models.local.json
# 已有完整缓存且不允许联网时
/absolute/runtime/bin/python scripts/prepare_models.py --manifest /absolute/work/.cantonese-subtitles/models.local.json --offline
```

默认只准备Whisper large-v3和Qwen3-ASR-1.7B；只有需要原文强制对齐时再加`--with-aligner`。已有清单会复查本地文件并复用，不重复下载；文件检查不代表加载成功。下载依赖的镜像不用于模型权重。

## 3. 验证模型加载和短音频识别

```sh
python scripts/setup_runtime.py --workspace /absolute/work --share \
  --manifest /absolute/work/.cantonese-subtitles/models.local.json \
  --sample /absolute/source.mp3
```

第二次运行会重新检查并复用已准备的环境，然后依次加载两模型，各识别素材开头至多12秒的真实音频。默认Qwen设备按本次检查的MPS/CUDA/CPU能力选择，也可显式指定`--qwen-device cpu`等。

提供清单但不提供`--sample`时，只做模型加载检查；不提供清单时，只做环境、导入和音频解码检查。输出分别记录`model_load_verified`和`sample_inference_verified`，不能将环境就绪说成完整字幕已生成。样本返回空文本会提示无语音或VAD漏检，并视为未通过语音样本检查；不会凭空生成对白。

`verify_models.py`也可用实际运行环境单独调用。它会保存每次验证的原始结果与分阶段进度；语音调用成功且有文本仍不等于准确率或同步验收通过。

## 输出、错误与续接

- `.cantonese-subtitles/environment.local.json`：实际解释器、关键版本、自检证据、模型及验证范围。
- `.cantonese-subtitles/setup-progress.json`：安装各步骤状态、任务ID、日志位置、退出码和错误。
- `.cantonese-subtitles/logs/`：实际命令输出，包含真实下载包名、来源与可取得的速度。
- `.cantonese-subtitles/model-progress.json`、`models-check.local.json`：模型加载/推理的单独验证结果。
- `.cantonese-subtitles/verification/`：真实样本、原始识别JSON和各模型逐阶段进度，不提交GitHub。

脚本约每30秒打印当前步骤、已耗时和最近真实输出；没有字节进度时明确写未知。Agent仍需读取这些记录，按[任务反馈规则](task-progress.md)向用户显示完整字幕任务清单。脚本没有后台通知界面，也不替代语言校准和最终同步核对。

失败时先查日志分类：`pyav_incompatible`、`temporary_directory_conflict`、`permission_denied`、`network`、`distribution_unavailable`等。`EEXIST`不等于已证实安全软件拦截。安装入口不会自动换版本、删除未知临时目录或关闭防护；同一原因有依据修复后最多再执行一次，仍失败则保留中断点并报告。

## 已知兼容依据

当前关键基线为Whisper 1.2.1 + PyAV 17.0.0，Torch 2.10.0 + Qwen-ASR 0.0.6 + Transformers 4.57.6，并固定相关关键依赖以减少版本解析回溯。其他间接依赖仍由pip解析，不能称为所有平台的完整锁文件。PyAV 19与旧Whisper接口不兼容的依据见[上游问题记录](https://github.com/SYSTRAN/faster-whisper/issues/1589)。
