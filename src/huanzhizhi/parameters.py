"""Small, stdlib-only parameter contract used by tasks and the settings UI."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class IntegerParameter:
    key: str
    label: str
    default: int
    minimum: int
    maximum: int
    help: str = ""
    suffix: str = ""


def parse_parameters(raw: list) -> tuple[IntegerParameter, ...]:
    if not isinstance(raw, list):
        raise ValueError("parameters 必须是列表")
    result = []
    keys = set()
    required = {"key", "label", "default", "minimum", "maximum"}
    for item in raw:
        if not isinstance(item, dict) or not required <= item.keys() or item.keys() - required - {"help", "suffix"}:
            raise ValueError("参数定义字段不完整或包含未知字段")
        if not isinstance(item["key"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", item["key"]) or item["key"] in keys:
            raise ValueError("参数 key 非法或重复")
        if not isinstance(item["label"], str) or not item["label"].strip():
            raise ValueError("参数标签不能为空")
        for field in ("default", "minimum", "maximum"):
            if type(item[field]) is not int:
                raise ValueError(f"参数 {field} 必须是整数")
        if not -(2**31) <= item["minimum"] <= item["default"] <= item["maximum"] < 2**31:
            raise ValueError("参数默认值或范围非法")
        if any(not isinstance(item.get(field, ""), str) for field in ("help", "suffix")):
            raise ValueError("参数说明和单位必须是字符串")
        keys.add(item["key"])
        result.append(IntegerParameter(**item))
    return tuple(result)


def validate_values(parameters: tuple[IntegerParameter, ...], values: dict) -> dict[str, int]:
    if not isinstance(values, dict) or set(values) - {item.key for item in parameters}:
        raise ValueError("参数配置包含未知字段")
    result = {}
    for item in parameters:
        value = values.get(item.key, item.default)
        if type(value) is not int or not item.minimum <= value <= item.maximum:
            raise ValueError(f"{item.label} 必须在 {item.minimum} 到 {item.maximum} 之间")
        result[item.key] = value
    return result
