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
    runtime: bool = False


@dataclass(frozen=True)
class ChoiceParameter:
    key: str
    label: str
    default: str
    choices: tuple[tuple[str, str], ...]
    help: str = ""
    runtime: bool = False


def parse_parameters(raw: list) -> tuple[IntegerParameter | ChoiceParameter, ...]:
    if not isinstance(raw, list):
        raise ValueError("parameters 必须是列表")
    result = []
    keys = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("参数定义必须是对象")
        choice = item.get("type") == "choice"
        required = {"key", "label", "default", "type", "choices"} if choice else {"key", "label", "default", "minimum", "maximum"}
        optional = {"help", "runtime"} if choice else {"help", "suffix", "runtime"}
        if not required <= item.keys() or item.keys() - required - optional:
            raise ValueError("参数定义字段不完整或包含未知字段")
        if not isinstance(item["key"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", item["key"]) or item["key"] in keys:
            raise ValueError("参数 key 非法或重复")
        if not isinstance(item["label"], str) or not item["label"].strip():
            raise ValueError("参数标签不能为空")
        if any(not isinstance(item.get(field, ""), str) for field in ("help", "suffix")):
            raise ValueError("参数说明和单位必须是字符串")
        if type(item.get("runtime", False)) is not bool:
            raise ValueError("参数 runtime 必须是布尔值")
        if choice:
            choices = item["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError("选项必须是非空列表")
            options = []
            for option in choices:
                if (not isinstance(option, dict) or set(option) != {"value", "label"}
                        or any(not isinstance(option[field], str) or not option[field].strip()
                               for field in ("value", "label"))):
                    raise ValueError("选项必须包含非空字符串 value 和 label")
                options.append((option["value"], option["label"]))
            values = [value for value, _ in options]
            if len(set(values)) != len(values) or not isinstance(item["default"], str) or item["default"] not in values:
                raise ValueError("选项重复或默认值不在选项中")
            result.append(ChoiceParameter(item["key"], item["label"], item["default"],
                                          tuple(options), item.get("help", ""), item.get("runtime", False)))
            keys.add(item["key"])
            continue
        for field in ("default", "minimum", "maximum"):
            if type(item[field]) is not int:
                raise ValueError(f"参数 {field} 必须是整数")
        if not -(2**31) <= item["minimum"] <= item["default"] <= item["maximum"] < 2**31:
            raise ValueError("参数默认值或范围非法")
        keys.add(item["key"])
        result.append(IntegerParameter(**item))
    return tuple(result)


def validate_values(parameters: tuple[IntegerParameter | ChoiceParameter, ...], values: dict) -> dict[str, int | str]:
    if not isinstance(values, dict) or set(values) - {item.key for item in parameters}:
        raise ValueError("参数配置包含未知字段")
    result = {}
    for item in parameters:
        value = values.get(item.key, item.default)
        if isinstance(item, ChoiceParameter):
            if not isinstance(value, str) or value not in [option[0] for option in item.choices]:
                raise ValueError(f"{item.label} 必须选择有效选项")
        elif type(value) is not int or not item.minimum <= value <= item.maximum:
            raise ValueError(f"{item.label} 必须在 {item.minimum} 到 {item.maximum} 之间")
        result[item.key] = value
    return result
