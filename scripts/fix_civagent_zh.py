# -*- coding: utf-8 -*-
from pathlib import Path

path = Path(r"C:\Users\<user>\Desktop\Unciv-Source\android\assets\jsons\translations\Simplified_Chinese.properties")
text = path.read_text(encoding="utf-8", errors="replace")
marker = "########################### CivAgent ###########################"
if marker in text:
    text = text.split(marker)[0].rstrip() + "\n\n"

entries = """########################### CivAgent ###########################
Leader Dialogue = 领袖对话
Fill empty seats with CivAgent = 空位填入 CivAgent
Enable CivAgent = 启用 CivAgent
Computer players will use CivAgent mode.\\nThe game will try to auto-start the local brain (127.0.0.1:2335).\\nConfigure the API key under Options → CivAgent first. = 电脑玩家将接入 CivAgent 模式。\\n游戏会尝试自动启动本地大脑（127.0.0.1:2335）。\\n请先在「选项 → CivAgent」中配置 API 密钥。
No one to talk to yet.\\nEither no CivAgent seats are filled (set them up at game start),\\nor you have not met any CivAgent civ so far - explore and meet them first. = 暂时无人可谈。\\n可能尚未勾选 CivAgent 席位（请在开局设置），\\n或你还未见过任何 CivAgent 文明——先探索并接触对方。
Send = 发送
Type a message… = 输入消息…
(The leader awaits your words.) = （领袖静候你的话语。）
CivAgent brain unreachable - AI seats won't reply. = CivAgent 大脑无法连接——AI 席位不会回复。
Draft (next turn): = 草稿（下回合生效）：
Drag the title to move · drag the corner to resize = 拖动标题栏移动 · 拖动右下角缩放
CivAgent = CivAgent
Provider = 厂商
API key = API 密钥
Base URL = Base URL
Brain URL = 大脑地址
Repo path = 仓库路径
Save = 保存
Test connection = 测试连接
Start brain = 启动大脑
Clear key = 清除密钥
Keys stay on this PC (~/.civagent/secrets.json).\\nThey are never written into saves or multiplayer uploads. = 密钥仅保存在本机（~/.civagent/secrets.json）。\\n不会写入存档或联机上传。
No key saved yet = 尚未保存密钥
CivAgent wants to act = CivAgent 请求行动
The CivAgent seat proposes the following this turn.\\nReject and the seat plays a full native turn instead. = 本回合 CivAgent 席位提出以下行动。\\n拒绝则该席改走完整原版 AI 回合。
"""

path.write_text(text + entries, encoding="utf-8")
chunk = path.read_text(encoding="utf-8").split(marker, 1)[1]
assert "领袖对话" in chunk
print("translations OK")
print(chunk.splitlines()[1])
