"""Shared capabilities and verified skill-local rules over live recognition data."""
from dataclasses import asdict, is_dataclass
import inspect
import json
import math
from pathlib import Path

import numpy as np
import cv2
from PIL import Image

from huanzhizhi import vision


# One source for the AI's table and the human-readable capability reference.
VISION = {
    'CropRect': '裁剪区域，保存为图片，可交给其他识别方法',
    'sample_abs_rgb': '按像素坐标取色',
    'sample_rgb': '按0～1比例坐标取色',
    'AverageColor': '区域平均RGB颜色',
    'ColorSimilarity': '两种颜色相似度，0～255',
    'ColorsSimilar': '两种颜色是否达到相似度阈值',
    'IsColor': '比例坐标处是否满足RGB颜色规则',
    'BuildColorMask': '按RGB或HSV范围生成掩膜',
    'BuildSimilarColorMask': '按参考颜色及相似度生成掩膜',
    'CountRgb': '统计符合RGB规则的像素数',
    'CountHsv': '统计符合HSV规则的像素数',
    'CountSimilarColor': '统计与参考颜色相似的像素数',
    'FindColorRectList': '查找符合颜色规则的区域',
    'FindSimilarColorRectList': '查找与参考颜色相似的区域',
    'FindColorComponents': '按颜色找连通区域，并按面积、宽高、形状过滤；返回候选列表，每项含rect=[left,top,right,bottom]，坐标相对输入原图',
    'BuildForegroundMask': '去除指定背景色，得到前景掩膜',
    'FindForegroundComponents': '去除背景后查找物体区域',
    'FindMaskComponents': '从掩膜查找区域并按面积、形状过滤',
    'GetBBox': '获取图像中的边界范围',
    'GetMaskBBox': '获取掩膜中非零部分的边界范围',
    'NmsRectList': '去除重复、重叠候选区域',
    'FindTemplate': '找图，返回最佳{rect:[left,top,right,bottom],score,...}或null；省略image以搜索当前完整截图，template用crop/asset，roi相对搜索图；须检查分数和歧义',
    'FindTemplateList': '找图，返回候选列表[{rect:[left,top,right,bottom],score,...}]；省略image以搜索当前完整截图，template用crop/asset，roi相对搜索图；适合重复图标',
    'FindTemplateLuma': '按亮度找图，适合颜色变化较大的画面',
    'FindTemplateLumaList': '按亮度查找多个图案',
    'FindTemplateBinary': '二值化后找图，适合轮廓清楚的图案',
    'FindTemplateBinaryList': '二值化后查找多个图案',
    'FindTemplateByMask': '只匹配模板掩膜覆盖的有效部分',
    'FindTemplateWithColorCheck': '找图并核对颜色，区分形状相同颜色不同的目标',
    'FindTemplateWithColorCheckList': '找图加颜色核对，返回多个候选',
    'FindFeature': 'SIFT/ORB特征匹配，适合有纹理且缩放、旋转的图案；不适合纯色小按钮',
    'ImageHash': '计算图像哈希，用于快速比较画面',
    'HashDistance': '比较两个图像哈希，越小越相似',
    'BuildColorBlockSignature': '建立小图的色块特征，需先裁剪至512×512以内',
    'CompareColorBlockSignatures': '比较已建立的色块特征',
    'MatchColorBlockTemplateList': '将小图与参考样本分类匹配，需先裁剪',
    'MatchPatchSignatureList': '比较小图样本并返回排序、分数和差距，需先裁剪',
    'GroupPoints': '把邻近点分组',
    'EstimateGridStep': '从坐标估计重复排列的间距',
    'BuildGridAxis': '根据坐标和间距建立规则轴',
    'FindGridAxes': '从点或矩形估计横纵规则排列',
    'FindRegularGridCells': '从候选点或矩形构建规则网格；不包含游戏规则',
    'FindGridTileCells': '根据候选中心和前景估计网格单元',
    'HasGridTileAt': '检查指定规则单元内是否存在前景',
    'BuildGridTileSignature': '为规则单元构建视觉特征',
    'FindTextLikeBBox': '定位亮色或暗色文字形状区域，不识别文字内容',
    'ExtractLetters': '按参考文字颜色提取字形图，暗色表示接近文字颜色',
    'ExtractWhiteLetters': '提取白色字形图，暗色表示接近白色',
    'ReadText': 'Windows OCR读取文字及原图坐标；language如zh-Hans-CN/en-US，无置信度',
    'FindText': 'Windows OCR查找文字，忽略中文字符之间的OCR空格；返回匹配行列表[{text,words,rect}]，先用SelectOne选择唯一行，再引用其rect；未找到返回[]',
    'DiagnoseCandidates': '查看候选区域的颜色、面积等诊断依据',
    'DiagnoseMatch': '查看找图、颜色规则的诊断依据',
    'DiagnoseState': '核对多个指定位置的颜色条件',
}

INPUT = {
    'sequence': ('一次求出整批click/right_click/key动作；逐项只做局部when/after核对，完成后统一核对整批结果', {'steps': '由当前识别结果计算的动作列表，1～200项；每项action/target/parameters/when/after/skip'}),
    'click': ('左键单击目标中心', {}),
    'right_click': ('右键单击目标中心', {}),
    'double_click': ('双击目标中心', {}),
    'drag': ('从目标中心按住左键拖到end，再释放；duration为秒', {'end': [300, 400], 'duration': 0.5}),
    'scroll': ('在目标中心滚轮；delta为120的倍数，正数向上', {'delta': -120}),
    'key': ('向目标窗口发送单个按键，不支持组合键；先确保输入焦点', {'key': 'ENTER'}),
    'text': ('向目标窗口发送Unicode文字；先确保输入焦点，不自动提交', {'text': '示例文字'}),
}


class NoMatch(ValueError):
    """A valid recognition returned no value at the requested path."""


def SelectOne(items, field='', value=None):
    """Select a unique candidate; never silently take the first match."""
    if not isinstance(items, (list, tuple)):
        raise ValueError('SelectOne需要候选列表')
    candidates = [item for item in items if not field or (isinstance(item, dict) and item.get(field) == value)]
    if len(candidates) > 1:
        raise ValueError('识别到多个候选，无法确定唯一目标')
    return candidates[0] if candidates else None


def CountItems(items):
    return len(items)


def RectCenter(rect):
    left, top, right, bottom = rect
    return [int((left+right)/2), int((top+bottom)/2)]


DATA = {'SelectOne': (SelectOne, '从列表选择唯一项；field/value可按字段精确筛选；多项时报错'),
        'CountItems': (CountItems, '统计列表或文字的长度，可用于分支判断'),
        'RectCenter': (RectCenter, '把识别出的[left,top,right,bottom]转换成中心坐标')}


def method(name, directory=None, stopped=lambda: False):
    if name in DATA:
        return DATA[name][0]
    if name in VISION:
        return getattr(vision, name)
    if isinstance(name, str) and name.startswith('custom_') and directory is not None:
        from huanzhizhi.skill_methods import call_method, load_method
        load_method(directory, name)
        def custom(data):
            return call_method(directory, name, data, stopped)
        return custom
    raise ValueError('未知通用方法：'+str(name))


def catalog():
    table = []
    for name, description in {**VISION, **{key: value[1] for key, value in DATA.items()}}.items():
        signature = inspect.signature(method(name))
        params = [str(p.replace(annotation=inspect.Parameter.empty)) for p in signature.parameters.values()]
        table.append({'name': name, 'purpose': description, 'parameters': ', '.join(params)})
    return table


def _validate_json(value, depth=0):
    # A batch definition wraps sequence test cases in additional containers.
    if depth > 16:
        raise ValueError('参数嵌套过深')
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError('参数过多')
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError('参数名必须是文字')
            _validate_json(child, depth+1)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise ValueError('列表过长，请缩小识别范围')
        for child in value:
            _validate_json(child, depth+1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('参数必须是有限数值')
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError('参数必须是JSON数据')


def arguments(text):
    if not isinstance(text, str) or len(text) > 24000:
        raise ValueError('参数JSON过长或类型错误')
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        nearby = text[max(0, error.pos-100):error.pos+140]
        raise ValueError(f'参数JSON在第{error.lineno}行第{error.colno}列无效：{error.msg}；'
                         f'错误附近={json.dumps(nearby, ensure_ascii=False)}') from error
    if not isinstance(value, dict):
        raise ValueError('参数必须是JSON对象')
    _validate_json(value)
    return value


class VisionCalls:
    """Keep raw results for composing methods; persist JSON summaries and images."""
    def __init__(self, directory, stopped=lambda: False):
        self.directory = Path(directory)
        self.stopped = stopped
        self.results = {}
        self.definitions = {}

    def _resolve(self, value, image):
        if isinstance(value, dict):
            if set(value) == {'crop'}:
                from huanzhizhi.visual_flow import rectangle
                return image.crop(rectangle(value['crop'], image.size))
            if set(value) == {'asset'}:
                path = (self.directory / value['asset']).resolve()
                if path.parent != self.directory.resolve() or path.suffix != '.png':
                    raise ValueError('图片必须是本次探索目录中的PNG素材')
                with Image.open(path) as source:
                    return source.copy()
            if set(value) in ({'result'}, {'result', 'path'}):
                result = self.results[value['result']]
                for part in value.get('path', []):
                    if result is None:
                        raise NoMatch('识别结果为空')
                    try:
                        result = result[part]
                    except (KeyError, IndexError) as error:
                        raise NoMatch('识别结果中没有所需字段') from error
                return result
            return {key: self._resolve(child, image) for key, child in value.items()}
        if isinstance(value, list):
            return [self._resolve(child, image) for child in value]
        return value

    def evaluate(self, name, params, image):
        _validate_json(params)
        function = method(name, self.directory, self.stopped)
        signature = inspect.signature(function)
        values = self._resolve(params, image)
        if name.startswith('custom_'):
            # Rules receive values, never Python objects that expose host IO.
            values = self._rule_data(values)
        first = next(iter(signature.parameters))
        if first in ('image', 'img') and first not in values:
            values[first] = image
        bound = signature.bind(**values)
        for key, limit in (('max_count', 1000), ('size', 100), ('block_size', 100), ('radius', 100)):
            if key in values and (type(values[key]) is not int or not 1 <= values[key] <= limit):
                raise ValueError(key+f'必须在1～{limit}之间')
        if 'Signature' in name or name == 'MatchColorBlockTemplateList':
            for sample in [values.get(first), *values.get('templates', [])]:
                if isinstance(sample, (Image.Image, np.ndarray)):
                    array = np.asarray(sample)
                    if array.shape[0] > 512 or array.shape[1] > 512:
                        raise ValueError('色块特征需要小图，请先裁剪至512×512以内')
        if 'template' in values and name.startswith('FindTemplate'):
            array = np.asarray(values['template'])
            if array.ndim < 2 or array.size == 0 or array.std(axis=(0, 1)).max() < 1:
                raise ValueError('模板缺少可区分的图案；纯色目标请使用颜色识别')
        try:
            return function(*bound.args, **bound.kwargs)
        except cv2.error as error:
            raise ValueError('图像或参数不适用于该识别方法：'+str(error)) from error

    def _rule_data(self, value):
        if isinstance(value, (Image.Image, np.ndarray)):
            array = np.asarray(value)
            if array.ndim not in (2, 3) or max(array.shape[:2]) > 512:
                raise ValueError('自定义像素方法需要先裁剪至512×512以内')
            return array.tolist()
        if is_dataclass(value):
            return self._rule_data(asdict(value))
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {str(key): self._rule_data(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._rule_data(child) for child in value]
        return value

    def call(self, name, params, image, *, identifier=None):
        identifier = identifier or 'inspect-'+str(len(self.results)+1)
        import re
        if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', identifier)
                or identifier in self.definitions):
            raise ValueError('调用id无效或重复')
        result = self.evaluate(name, params, image)
        self.results[identifier] = result
        self.definitions[identifier] = dict(id=identifier, method=name, arguments=params)
        exported = self._export(result, identifier)
        record = dict(id=identifier, method=name, arguments=params, result=exported)
        (self.directory / (identifier+'.json')).write_text(
            json.dumps(record, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return record

    def _export(self, value, identifier):
        if isinstance(value, (Image.Image, np.ndarray)):
            array = np.asarray(value)
            if array.ndim in (2, 3) and array.dtype in (np.uint8, bool):
                asset = identifier+'.png'
                Image.fromarray(array.astype(np.uint8)*255 if array.dtype == bool else array).save(self.directory / asset)
                return {'asset': asset, 'size': [array.shape[1], array.shape[0]]}
            return array.tolist()
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {str(key): self._export(child, identifier+'-'+str(index))
                    for index, (key, child) in enumerate(value.items()) if key != 'blocks'}
        if isinstance(value, (list, tuple)):
            return [self._export(child, identifier+'-'+str(index)) for index, child in enumerate(value)]
        return value


if __name__ == '__main__':
    print(json.dumps({'vision': catalog(), 'input': INPUT}, ensure_ascii=False, indent=2))
