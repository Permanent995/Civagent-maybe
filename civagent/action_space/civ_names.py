civ_name_spaces = {
    "china": "中国",
    "rome": "罗马",
    "egypt": "埃及",
    "mongolia": "蒙古",
    "aztecs": "阿兹特克",
    "greece": "希腊",
    "all": "所有人",
}


def civ_display_name(name: str) -> str:
    """Chinese display name for a civ id, falling back to the raw in-game name.

    The table only covers vanilla nations. Mods (e.g. Emperors and Deities) use custom
    nations, so a missing key must never raise KeyError - it just shows the in-game name.
    """
    if name is None:
        return ""
    text = str(name)
    return civ_name_spaces.get(text.strip().lower(), text)
