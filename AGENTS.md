# AGENTS.md — 本仓库的 shell 事实与调用纪律

> 面向在本仓库工作的 AI / 人。**先读这一页，再决定怎么发命令。**
> 下面每条都标了「实测」或来源行号，不要凭通用经验覆盖它。

## 1. 你的命令实际跑在哪个 shell

| 项 | 实测值 |
|----|--------|
| 工具发命令用的 shell | **Git Bash 5.3.9（MSYS2，POSIX sh）**，`LANG=C.UTF-8` |
| `pwsh` | 7.6.7，**MSIX 包 + App Execution Alias**：`%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh` → `Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_*\pwsh.exe` |
| `powershell.exe` | 5.1.22621.6133（System32，一直在 PATH） |
| 机器级 PS7 安装 | **没有**（`Program Files\PowerShell\7\pwsh.exe` 不存在） |
| Python | 3.12.4 @ `D:\Python\python`，另有 `py` 启动器 |

**结论：装了 PS7 ≠ 调用走 PS7。** 不显式点名就是 POSIX sh。而
[`EXECUTION.md` §2 的常用命令](EXECUTION.md)（第 38–58 行）是 **PowerShell 口吻**写的——
`.\scripts\start-host.ps1`、`.\gradlew`、`%TEMP%` 在这里直接粘必挂。这是本仓库「转义失败」的第一来源。

## 2. 选 shell 的规则：**每多穿一层，转义需求乘一次**

| 要做的事 | 用什么 | 为什么 |
|----------|--------|--------|
| 读写文件、git、grep、python、pytest | 直接 POSIX（不要包一层 shell） | 零解析层 |
| 只有一条 PowerShell cmdlet（`Get-ChildItem`/`Select-String`…） | `pwsh -NoProfile -Command '<单引号包住>'` | bash 不展开单引号内内容，PS 只解析一次 |
| 任何带中文 / 多行 / 嵌套的逻辑 | **写成 `.ps1`，用 `pwsh -NoProfile -File x.ps1`** | 把内联字符串彻底去掉，是最有效的一刀 |
| 必须内联且含中文或引号地狱 | `pwsh -EncodedCommand <base64(UTF-16LE)>` | 一次绕开所有解析层 |
| 调用Unciv 游戏仓（本仓之外） | 命令在 `C:\Users\<user>\Desktop\Unciv-Source` 里跑，见 §4 | `gradlew` 是 `.bat`，POSIX 下要 `cmd.exe //c` 或直接 PowerShell |

## 3. 三类「转义失败」，成因各不相同

### 3.1 引号：层数问题，不是版本问题

Windows 没有统一的 argv 约定，每个程序按自己的规则重解一遍原始命令行字符串。**升 PS 版本不减少层数**，
所以 `bash → pwsh -Command → python -c → SQL/JSON` 到第 3 层以后必然开始漏。
2 层（`pwsh -Command "python -c \"...\""`）实测可以过，别把它当成 3 层也能过的证据。

- 单引号优先：bash 不解析单引号内任何内容，`$` 反引号 `\` 都安全。
- 需要 PS 展开变量时才用双引号，此时 bash 侧也必须用单引号包整条 `-Command`。
- 参数逐个传（`-File x.ps1 -- --a 1`），**不要**自己拼成一个字符串再交给 shell。
- PS 侧要「后面原样不要解析」时用停止标记 `--%`。

### 3.2 编码：最常伪装成「转义失败」

**实测：从 Git Bash 调 pwsh 7.6.7，`[Console]::OutputEncoding` 仍是 `gb2312`，中文照样乱码。**
PS7「默认 UTF-8」在这里救不了你——它默认的是 *文件读写* UTF-8，输出编码继承系统代码页。

- 凡是**要读回它的输出**的调用，前置一句：
  `pwsh -NoProfile -Command '[Console]::OutputEncoding=[Text.Encoding]::UTF8; ...'`
- **不要用肉眼判断成败**：5.1 下「ANSI 读入 UTF-8 文件」+「GBK 写出」两次错会互相抵消，看起来恰好正确。
- 文本 I/O 一律显式编码：`-Encoding utf8NoBOM`（读也写）。
  历史事故：PowerShell `Add-Content` 写坏 `Simplified_Chinese.properties` 的 UTF-8，
  改用 Python 脚本 → [EXECUTION.md:251](EXECUTION.md)。
- `.ps1` 里要写中文：优先 PS7 + UTF-8 无 BOM；若必须兼容 5.1 则存 UTF-8 **带 BOM**；
  最稳的做法是**脚本里不出现非 ASCII**，把中文放进 `.json`/`.txt` 数据文件按 `-Encoding utf8` 读。
- Python 侧同类坑已固化成工具：GBK 控制台下**一个 `⚠` 就 `UnicodeEncodeError` 打断整次评测**，
  所以有 `arena/paths.make_stdout_safe()` 与全 ASCII 标记 → [EXECUTION.md:673](EXECUTION.md)。别绕过它自己 print。

### 3.3 路径与变量：MSYS 会在背后改写你的参数

- **不要把 POSIX 路径交给 Windows 原生程序**：实测 `/tmp/x.txt` 传到 pwsh 变成 `C:\tmp\x.txt`（不存在）。
  用 `cygpath -w /tmp/x` 或直接写 `C:\Users\...`。
- 反斜杠在 bash 里是转义符：路径用 `/c/Users/<user>/...` 或整体加引号。
- **`%VAR%` 是 cmd 的，`$env:VAR` 是 PowerShell 的，`$VAR` 是 bash 的。** 混用不会报错，只会静默变空值——
  比报错更难查。
- 中文文件名（`测试arena.json`、资源 `醴`）失败点常在 shell 展开**之前**，报的是「参数不对」而不是「编码错」。
  只在最外层引号一次，内部不再叠加转义。

## 4. 本仓库常用命令（POSIX 版，可直接粘）

```bash
# 起大脑（探测 2335，未起则拉起）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/start-host.ps1

# 大脑测试
python -m pytest tests/ -q

# 训练场（arena）——注意路径里带中文，整体用单引号
python arena/report.py --kind rotation --maps '测试arena.json'
python arena/nation_strength.py --write --md arena/ranking-total.md --maps '测试arena.json'

# 游戏仓（本目录之外，JDK 17；Gradle 8.11.1 跑不了 JDK 25）
cd /c/Users/<user>/Desktop/Unciv-Source
cmd.exe //c gradlew.bat :tests:test
```

`scripts/start-host.bat` 第 4 行现在写的是 `powershell`（5.1）。想少走一层 ANSI 解析，
就把它和上面的调用统一成 `pwsh`。

## 5. 已经付过代价的坑，别再重新发现一次

| 坑 | 出处 / 现状 |
|----|-------------|
| `Add-Content` 写坏 UTF-8 翻译文件 | 改用 Python 脚本 [EXECUTION.md:251](EXECUTION.md) |
| GBK 控制台 `UnicodeEncodeError` 打断评测 | `paths.make_stdout_safe()` [EXECUTION.md:673](EXECUTION.md) |
| 按 **cmdline** 匹配杀残留进程，会把**正在执行该命令的 bash 自己**杀掉（连中两次，exit 15） | 现按**进程名**过滤 [EXECUTION.md:904](EXECUTION.md) |
| 带空格/中文的文明名 `Sheng Tang` 未加引号 → console 分词错、`URL can't contain control characters` | 命令一律加引号 + `URLEncoder.encode` |
| `pip install -r requirements.txt` 把 Flask/Werkzeug 降级 | 禁止，按需单装 [EXECUTION.md:62](EXECUTION.md) |
