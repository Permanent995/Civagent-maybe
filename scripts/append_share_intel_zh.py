# -*- coding: utf-8 -*-
from pathlib import Path

path = Path(r"C:\Users\<user>\Desktop\Unciv-Source\android\assets\jsons\translations\Simplified_Chinese.properties")
text = path.read_text(encoding="utf-8")
extra = """
Attachments = 附件
Share intelligence = 情报交换
Upload image = 上传图片
Clear attachment = 清除附件
Must include a text claim. Does not reveal the map. = 必须填写文字主张。不会开启迷雾/地图。
About civilization = 关于文明
Claim = 主张
Send intelligence = 发送情报
e.g. Rome is massing troops on our northern border = 例如：罗马正在我方北部边境集结部队
A text claim is required for intelligence exchange. = 情报交换必须填写文字主张。
Pick which civilization the claim is about. = 请选择主张所涉及的文明。
Choose an image = 选择图片
Could not read the image file. = 无法读取该图片。
Image too large (max 4 MB). = 图片过大（最大 4 MB）。
Upload failed — is the CivAgent brain running? = 上传失败——CivAgent 大脑是否在运行？
Image attached. Send a message or share intelligence. = 图片已附加。请发送消息或进行情报交换。
Attached: [[name]] (tap + to clear) = 已附加：[[name]]（点 + 可清除）
"""
# Avoid duplicate appends
for line in extra.strip().splitlines():
    key = line.split(" = ", 1)[0]
    if key and f"\n{key} =" not in text and not text.startswith(f"{key} ="):
        text = text.rstrip() + "\n" + line + "\n"
path.write_text(text, encoding="utf-8")
print("zh keys ok")
