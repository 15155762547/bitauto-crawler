"""Official category labels; source names remain stable across UI renames."""

CATEGORY_LABELS = [
    ("General Information", "基本信息"),
    ("Body", "车身"),
    ("Electric Motor", "电动机"),
    ("Battery/Recharge", "电池／补能"),
    ("Engine", "发动机"),
    ("Transmission", "变速箱"),
    ("Chassis Steering", "底盘转向"),
    ("Wheel Braking", "车轮制动"),
    ("Active Safety", "主动安全"),
    ("Passive Safety", "被动安全"),
    ("Assistance/Control Features", "辅助／操控配置"),
    ("ADAS Features", "辅助驾驶功能"),
    ("ADAS Hardware", "辅助驾驶硬件"),
    ("External Features", "外部配置"),
    ("Interior Features", "内部配置"),
    ("Seat Features", "座椅配置"),
    ("Infotainment System/Connectivity", "车机／互联"),
    ("Multimedia System", "影音娱乐"),
    ("Lighting Functions", "灯光功能"),
    ("Glass/Rearview Mirror", "玻璃／后视镜"),
    ("Air Conditioning/Cooling", "空调／制冷"),
]
CATEGORIES_ORDER = [(en, f"{zh} {en.replace('/', '／')}") for en, zh in CATEGORY_LABELS]


def category_table_map(tables):
    """Resolve old/new labels to the same ID; reject duplicates rather than guess."""
    result = {table["name"]: table["id"] for table in tables}
    for source, display in CATEGORIES_ORDER:
        aliases = {source, source.replace("/", "／"), display}
        ids = {table["id"] for table in tables if table["name"] in aliases}
        if len(ids) > 1:
            raise ValueError(f"Ambiguous category tables: {source}: {sorted(ids)}")
        if ids:
            result.update(dict.fromkeys(aliases, next(iter(ids))))
    return result
