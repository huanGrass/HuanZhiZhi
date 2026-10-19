"""Windows' installed OCR engine; no downloads or model-generated code."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile

from PIL import Image

from .rects import _crop_array


def ReadText(image, roi=None, language=''):
    """Return text, language and word rectangles in original image pixels.

    Windows does not provide confidence scores. Unsupported languages fail
    explicitly; there is no silent switch to another engine or language.
    """
    if not isinstance(language, str) or len(language) > 32:
        raise ValueError('Invalid OCR language')
    crop, rect = _crop_array(image, roi)
    executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    with tempfile.TemporaryDirectory(prefix='zhizhi-ocr-') as directory:
        path = Path(directory) / 'image.png'
        Image.fromarray(crop).save(path)
        result = subprocess.run(
            [str(executable), '-NoProfile', '-NonInteractive', '-File',
             str(Path(__file__).with_name('windows_ocr.ps1')), '-ImagePath', str(path),
             '-Language', language], capture_output=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Windows OCR failed: ' + result.stderr.decode('utf-8', errors='replace')[-1200:])
    data = json.loads(result.stdout.decode('utf-8-sig'))
    for line in data['lines']:
        for word in line['words']:
            left, top, right, bottom = word['rect']
            word['rect'] = [left+rect.left, top+rect.top, right+rect.left, bottom+rect.top]
        if line['words']:
            boxes = [word['rect'] for word in line['words']]
            line['rect'] = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return data


def FindText(image, text, roi=None, language='', exact=False):
    """Return all matching OCR line boxes; callers must resolve ambiguity."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('text must not be empty')
    # Windows OCR inserts spaces between Chinese words even within a label.
    # Keep Latin word boundaries and return the original OCR text/boxes.
    def normalized(value):
        return re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', value)
    query = normalized(text)
    return [line for line in ReadText(image, roi, language)['lines']
            if (normalized(line['text']) == query if exact else query in normalized(line['text']))]
