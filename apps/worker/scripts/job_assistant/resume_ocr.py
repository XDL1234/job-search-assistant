"""在本机渲染和识别扫描版简历，无需浏览器或在线模型。"""

from contextlib import closing
from pathlib import Path
from time import monotonic


MAX_IMAGE_SIDE = 2200
MODELS = {
    'Det.model_path': 'PP-OCRv6_det_small.onnx',
    'Cls.model_path': 'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
    'Rec.model_path': 'PP-OCRv6_rec_small.onnx',
}


def _text_lines(boxes, texts):
    if boxes is None or texts is None:
        return []
    pieces = []
    for box, text in zip(boxes, texts):
        if text := text.strip():
            left, top = min(point[0] for point in box), min(point[1] for point in box)
            right, bottom = max(point[0] for point in box), max(point[1] for point in box)
            pieces.append(((top + bottom) / 2, max(1, bottom - top), left, right, text))
    rows = []
    for piece in sorted(pieces):
        # shortcut: 以水平行合并表格字段，多栏独立阅读顺序可在有可靠版面模型时进一步还原。
        if not rows or abs(piece[0] - rows[-1][0][0]) > 0.45 * min(piece[1], rows[-1][0][1]):
            rows.append([])
        rows[-1].append(piece)
    lines = []
    for row in rows:
        row.sort(key=lambda piece: piece[2])
        _, height, _, right, line = row[0]
        for _, next_height, left, next_right, text in row[1:]:
            line += ('  ' if left - right > 0.8 * min(height, next_height) else ' ') + text
            right = max(right, next_right)
        lines.append(line)
    return lines


def ocr_pdf_pages(path, pages):
    """识别指定的 1-based 页码。两处有明显水平间隔的识别块以双空格分隔。"""
    deadline = monotonic() + 100
    page_numbers = tuple(dict.fromkeys(pages))
    if any(type(number) is not int or number < 1 for number in page_numbers):
        raise ValueError('扫描 PDF 页码无效')
    if not page_numbers:
        return {}
    try:
        import pypdfium2
        import rapidocr
    except (ImportError, OSError) as error:
        raise ValueError('本机文字识别组件不可用，请检查安装包是否完整') from error

    models = Path(rapidocr.__file__).resolve().parent / 'models'
    if any(not (models / filename).is_file() for filename in MODELS.values()):
        raise ValueError('本机文字识别模型缺失，请重新安装求职助手')
    params = {key: str(models / filename) for key, filename in MODELS.items()}
    params.update({
        'Global.max_side_len': MAX_IMAGE_SIDE,
        'Global.log_level': 'error',
        'EngineConfig.onnxruntime.intra_op_num_threads': 2,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
    })
    lines = {}
    page_failed = False
    try:
        engine = rapidocr.RapidOCR(params=params)
        with closing(pypdfium2.PdfDocument(path)) as document:
            for number in page_numbers:
                if monotonic() >= deadline:
                    break
                try:
                    with closing(document[number - 1]) as page:
                        scale = min(3, MAX_IMAGE_SIDE / max(page.get_size()))
                        with closing(page.render(scale=scale)) as bitmap:
                            with bitmap.to_pil() as image:
                                result = engine(image)
                        lines[number] = _text_lines(result.boxes, result.txts)
                except Exception:
                    page_failed = True
    except Exception as error:
        raise ValueError('扫描 PDF 识别失败，请检查文件并重新导入') from error
    if not lines and page_failed:
        raise ValueError('扫描 PDF 识别失败，请检查文件并重新导入')
    return lines
