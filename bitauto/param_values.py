"""Lossless display of official parameter text, including nested options."""


def render_param_value(value: dict) -> str:
    parts = []
    direct = value.get("value")
    if direct not in (None, ""):
        parts.append(str(direct))
    nested = value.get("subList") or []
    for option in nested:
        label = option.get("name") or option.get("value")
        if label not in (None, "") and str(label) not in parts:
            parts.append(str(label))
    separator = ", " if nested and all(o.get("name") for o in nested) else "；"
    return separator.join(parts)


def ensure_select_options(run_cli, table_id: str, records: list[dict]) -> None:
    """Extend existing select options before writing newly observed source details."""
    import json
    import time

    fields = run_cli("+table-get", ["--table-id", table_id])["data"]["fields"]
    changed = False
    for field in fields:
        if field["type"] != "select":
            continue
        known = {o["name"] for o in field["options"]}
        values = set()
        for record in records:
            value = record.get(field["name"])
            values.update(value if isinstance(value, list) else [value] if value else [])
        missing = sorted(values - known)
        if not missing:
            continue
        definition = {k: v for k, v in field.items() if k != "id"}
        definition["options"] = field["options"] + [
            {"name": v, "hue": "Orange" if "○" in v else "Green", "lightness": "Lighter"}
            for v in missing
        ]
        result = run_cli(
            "+field-update",
            [
                "--table-id",
                table_id,
                "--field-id",
                field["id"],
                "--json",
                json.dumps(definition),
                "--yes",
            ],
        )
        if not result.get("ok"):
            raise RuntimeError(result)
        changed = True
    if changed:
        time.sleep(3)
