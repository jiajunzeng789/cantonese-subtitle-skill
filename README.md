# 粤语字幕制作skill

## 用 WorkBuddy 等国产 AI Agent 怎么做字幕？

在 **WorkBuddy、QoderWork、TRAE** 等能读取本地文件、执行程序的桌面 AI Agent 中，可以参照下面的通用流程。音视频在本地识别，文字校准使用当前 Agent 的语言模型。

### 第一步：打开一个本地工作文件夹

新建一个文件夹，在 Agent 中将它选为本次任务的工作目录。软件需要能够读取你的素材并运行本地程序。

### 第二步：复制这段话，让 AI 帮你准备

```text
请帮我准备“粤语字幕制作skill”。
项目地址：https://github.com/jiajunzeng789/cantonese-subtitle-skill
请将项目下载到当前工作文件夹，读取 skills/auto-cantonese-subtitles/SKILL.md，按其中的流程执行。
如果软件支持安装Skill，请安装完整技能文件夹；否则直接读取并使用，不要只复制SKILL.md。
先检查并复用已有可用环境，缺少时再准备独立环境和本地识别模型，可以下载公开依赖和模型，不要修改系统Python。
确需下载依赖时，优先检查清华等可信国内镜像并仅对本次安装使用，不修改全局设置；显示实际下载包名和进度，读不到时明确说明。
音视频必须在本地识别，不要上传到云端识别服务；文字校准使用你的语言能力。
请显示任务清单，检查通过一项就打勾；长任务报告当前进展，失败说明具体原因并保存中断点，不要无限重试。
准备好后告诉我，我会提供音视频素材。
```

首次准备需要下载模型，耗时取决于电脑和网络。之后在同一台电脑上使用，可直接进行第三步。

已有可用环境时直接复用；国内镜像可用于加速安装依赖，但不保证速度，也不会自动加速模型权重下载。

准备与制作时，AI会按规则显示清单：✅ 检查通过、🔄 正在执行、❌ 失败、⏸️ 等待条件。中断后可说“继续”，让它核对已完成的步骤后接着处理。消息更新频率取决于所用Agent的执行能力。

### 第三步：给它素材，直接说要什么

把音视频放到工作文件夹里，告诉 AI 文件名；也可以提供电脑上的完整文件路径。然后发：

```text
请使用“粤语字幕制作skill”处理这个文件：填写文件名或完整路径。
输出简体中文书面语SRT字幕，不要粤语表达，每条只有一行，不要句读标点，时间轴对齐原素材。
成片画幅：按原视频；如果我要竖屏，则按1080×1920制作。
汽车品牌、车型和人物姓名请核对，不确定的地方单独告诉我。
请将结果保存在工作文件夹里。
请持续更新任务清单，导出后分别说明格式检查和时间轴验收结果。
```

只有音频时，把“成片画幅”改成“横屏”或“竖屏”。有车型名单、配置表或团队姓名表，也可以一起提供。

**完成后，拿到 `.srt` 文件，导入剪映、Premiere 等剪辑软件即可使用。** 导入后预览一下时间和字号，确认画面上保持单行。

上面采用直接读取技能的通用方式，不依赖各软件的具体安装按钮。产品功能可参考 [WorkBuddy 官方介绍](https://cloud.tencent.com/product/workbuddy)、[QoderWork 技能说明](https://docs.qoder.com/qoderwork/skills)、[TRAE SOLO 官方介绍](https://www.trae.ai/blog/new_solo_beta_0331)；本项目尚未逐一验证各软件的完整运行环境。

---

<details>
<summary>技术说明、手动安装与字幕标准（普通使用者可以跳过）</summary>

## Codex 手动安装

把项目中的 `skills/auto-cantonese-subtitles` 完整文件夹复制到个人技能目录 `~/.agents/skills/`，或工作项目的 `.agents/skills/`，然后在对话中使用 `$auto-cantonese-subtitles`。目标位置已有同名技能时先备份。参考 [OpenAI 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。

## 首次环境准备：给负责安装的人

需要 Python、FFmpeg、FFprobe，以及可加载下列模型的运行环境。现有样本在 Apple Silicon Mac 上完成过测试：Whisper 使用 CPU int8，Qwen 使用 PyTorch MPS。其他平台需要先做加载和短样本检查，不承诺相同速度。

| 用途 | 模型或组件 | 说明 |
|---|---|---|
| 第一份转写 | `Systran/faster-whisper-large-v3` | 完整版 large-v3 的 CTranslate2 权重 |
| 第二份转写 | `Qwen/Qwen3-ASR-1.7B` | 显式指定 Cantonese |
| 可选时间核对组件 | `Qwen/Qwen3-ForcedAligner-0.6B` | 当前短样本 Qwen 脚本会要求；结果需要检查 |
| 媒体探测和音轨提取 | FFmpeg、FFprobe | 两个程序均需能从终端调用 |
| 文字与语义校准 | 当前智能体的语言模型 | 比较原文、上下文和术语资料 |
| 实际字体宽度测量 | Pillow | 配置字幕字体时使用 |

设备需要容纳两个识别模型及可选对齐模型；实际内存、磁盘占用和运行速度依平台、缓存和音视频长度变化。依次运行两个模型可降低同时占用；先跑短样本判断本机是否适用。

在仓库根目录创建独立环境，例如：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r skills/auto-cantonese-subtitles/scripts/requirements.txt
python -m pip install Pillow
ffmpeg -version
ffprobe -version
```

Windows PowerShell 激活环境用 `.venv\Scripts\Activate.ps1`。FFmpeg、FFprobe 需单独安装并加入 PATH。PyTorch 的安装方式应匹配操作系统和 CPU/CUDA/MPS；安装失败时先核对平台支持，不要往系统环境反复混装。

下载模型并记录快照路径：

```sh
python skills/auto-cantonese-subtitles/scripts/prepare_models.py --manifest models.local.json
```

脚本会下载三个模型，记录路径与快照；`load_verified: false` 表示只检查了下载文件，还需要实际加载。`models.local.json` 含本机路径，留在本机，不提交仓库。模型受各自模型仓库条款约束，本仓库不重新分发权重。

### 识别入口与长片处理

辅助脚本输出原始识别 JSON，文字校准、语义断句和最后的时间核对仍由智能体编排。

下面的模型路径需要替换成 `models.local.json` 中相应的实际目录，素材使用自己的短样本：

```sh
python skills/auto-cantonese-subtitles/scripts/transcribe.py sample.mp4 \
  --engine whisper --model /path/to/whisper-snapshot --output outputs/whisper.json

python skills/auto-cantonese-subtitles/scripts/transcribe.py sample.mp4 \
  --engine qwen --model /path/to/qwen-snapshot \
  --aligner /path/to/aligner-snapshot --device cpu --output outputs/qwen.json
```

Qwen 确认支持后可选 `--device mps` 或 `--device cuda`；Whisper 可选 CPU 或 CUDA，不支持 MPS。两份结果应依次生成。

**当前 `transcribe.py` 限制为180秒内的样本。** 长片由智能体按有上下文重叠的语音窗口分别识别，记录每段起点偏移、去重并检查接缝。现有长片测试不是用短样本入口直接跑完整视频。具体执行规则见 [本地测试说明](skills/auto-cantonese-subtitles/references/local-testing.md)。

遇到开场漏段、异常重复、静音补字或零时长对齐，须裁出对应原音频重新识别或核对。对齐时使用原始粤语转写，不能用改写后的普通话文字直接对齐粤语发音。

### 画幅和导出

```sh
# 根据主视频显示尺寸生成横屏/竖屏/方屏预设
python skills/auto-cantonese-subtitles/scripts/subtitle_profile.py \
  --media source.mp4 --output outputs/profile.json

# 最终会裁成竖屏时，覆盖原画幅
python skills/auto-cantonese-subtitles/scripts/subtitle_profile.py \
  --media source.mp4 --final-size 1080x1920 --output outputs/portrait.json

# 智能体完成文字校准、语义分句与时间映射后导出
python skills/auto-cantonese-subtitles/scripts/export_srt.py \
  outputs/edited.json outputs/subtitles.srt --profile outputs/profile.json
```

`edited.json` 不是原始转写文件。它需要包含素材时长、画幅、已校准短句及各自起止时间。格式见 [画幅与导出说明](skills/auto-cantonese-subtitles/references/subtitle-layout.md)。脚本会拒绝换行、超宽、时间重叠、越界及明显粤语字符残留；阅读速度和时长提示需要结合声音处理。

## 字幕制作标准

### 书面表达

先理解整句再改写。例如“佢揸呢架车觉得几舒服”可以写为“他认为这款车驾驶起来很舒适”。“系”作为判断词改为“是”，但“系统”“关系”“车系”中的“系”保留。只做繁简转换不够，还要检查词汇和句式。

不补造未说过的参数，不把“可能”“尚未确定”改成确定结论，不因为缩短字幕而丢失否定、条件或观点。

### 单行与长度

| 最终画幅 | 默认建议宽度 | 默认上限 |
|---|---|---|
| 横屏 | 16～20 | 20 |
| 竖屏 | 10～14 | 14 |
| 方屏 | 12～16 | 16 |

一个汉字通常计1，ASCII字符约计0.5。短回答可以更短，不补写内容凑字数。长句按自然语义和原始发音位置拆分，不能拆开品牌、车型、人名、数字和单位。不同人的问答分别显示。

画幅规则只是初始预设。最终字号、字体、字距、描边和安全区会影响一行能放多少字；配置本地字体后可以测量实际宽度。SRT主要保存文字和时间，不可靠保存字体和排版，剪辑软件的自动换行仍需预览。

### 无句读标点

去除逗号、句号、问号、感叹号、引号和括号等句读；必要时保留单个空格。技术记号如 `1.8米`、`30%`、`-5℃`、`0—100km/h`、`CX-5` 保留。SRT时间轴自身的冒号、逗号和箭头必须保留。

### 汽车术语校准

每次可以提供本次车型、年款、配置、人物姓名及参考资料。智能体对照两份转写和资料形成修订记录：时间、原识别、修订结果、依据和确认状态。语言推断和两个模型一致都不等于事实已确认。

可参考原片清晰的标牌或自带字幕；这样得到的结果应注明使用了画面辅助。不能把某次视频中的姓名、玩笑或同音梗自动加入所有项目的词库。资料不足时列待审，不凭常识强改。用户要求严格离线时，仅使用本地资料；需要联网核对时遵循用户的数据范围。

## 隐私与交付

音视频在本地识别，不需要购买语音识别 API。首次环境准备需要下载依赖和模型。**当前智能体使用在线语言模型时，转写文字会交给在线模型处理，因此默认流程不等于全链路离线。** 如文字也必须离线，需另外准备本地文本模型，并确认智能体实际使用它。

每次交付包括 UTF-8 SRT，以及必要的术语修订和待审记录。存在未决内容、人物轮次或时间疑点时，文件使用 `.review.srt`；经用户审阅确认后再保存正式发布版。只有实际回听或播放核对过的部分，才标为已验证同步。

团队已用单人、多人音频和约9分31秒的横屏汽车视频进行过试用，当前用户认可样本成稿效果。长片测试中使用过原片字幕辅助，仍有时间和快速问答待审项；没有独立人工参考稿的统一准确率评分。测试素材与转写不随仓库发布。

## 常见问题

**下载后能直接拖入视频出字幕吗？**

先安装 Skill 并准备模型，然后由智能体执行完整流程。模型权重和运行环境不会随小体积的 Skill 文件自动安装。

**为什么不直接使用一个模型的输出？**

不同模型会出现不同的粤语同音词、姓名和车型错误；第二份转写用于交叉检查，最后仍需语境和证据校准。两者不是机械拼接，也不按“谁说得更像常识”投票。

**视频横屏，但我要发布竖屏怎么办？**

告诉智能体最终尺寸，如1080×1920。最终画幅优先于源视频，按竖屏重新断句和定位。

**为什么导入剪辑软件后还是折成两行？**

检查软件中的字体、字号、安全区和自动折行设置。字幕正文单行不代表任意字号都能放入画面。

**对齐模型给出了时间，是否就代表同步正确？**

需要检查零时长、错位、原媒体偏移及多人轮次。结构检查通过只证明文件格式和基本边界可用，不能代替实际同步核对。

## 文件结构

```text
README.md
skills/auto-cantonese-subtitles/
├── SKILL.md                    智能体工作指令
├── agents/openai.yaml          Codex显示信息
├── references/
│   ├── editorial-rules.md      语言及术语校准
│   ├── local-testing.md        本地模型与测试方法
│   ├── environment-recovery.md 环境复用与故障恢复
│   ├── task-progress.md        进度清单与中断续接
│   └── subtitle-layout.md      画幅、断句及输出格式
└── scripts/
    ├── requirements.txt
    ├── prepare_models.py       下载模型并记录快照
    ├── transcribe.py           本地短样本识别
    ├── subtitle_profile.py     探测显示画幅
    └── export_srt.py           清理、检查并导出SRT
```

</details>
