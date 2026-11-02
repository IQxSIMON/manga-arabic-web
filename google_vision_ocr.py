import base64
import requests

def extract_google_vision(image_bytes, api_key, timeout=(15, 90)):
    endpoint = 'https://vision.googleapis.com/v1/images:annotate'
    payload = {'requests': [{'image': {'content': base64.b64encode(image_bytes).decode('ascii')}, 'features': [{'type': 'DOCUMENT_TEXT_DETECTION'}]}]}
    response = requests.post(endpoint, params={'key': api_key}, json=payload, timeout=timeout)
    response.raise_for_status()
    result = (response.json().get('responses') or [{}])[0]
    if result.get('error'):
        raise RuntimeError(result['error'].get('message', 'Google Vision OCR failed'))
    annotation = result.get('fullTextAnnotation') or {}
    text = (annotation.get('text') or '').strip()
    if not text:
        raise RuntimeError('Google Vision did not detect readable text.')
    words = []
    for page in annotation.get('pages', []):
        for block in page.get('blocks', []):
            for paragraph in block.get('paragraphs', []):
                for word in paragraph.get('words', []):
                    value = ''.join(x.get('text', '') for x in word.get('symbols', []))
                    vertices = (word.get('boundingBox') or {}).get('vertices') or []
                    if not value or not vertices: continue
                    xs = [float(p.get('x', 0)) for p in vertices]
                    ys = [float(p.get('y', 0)) for p in vertices]
                    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
                    if right > left and bottom > top:
                        words.append({'text': value, 'left': left, 'top': top, 'right': right, 'bottom': bottom, 'cy': (top + bottom) / 2, 'height': bottom - top})
    words.sort(key=lambda w: (w['cy'], w['left']))
    groups = []
    for word in words:
        group = next((g for g in groups if abs(g['cy'] - word['cy']) <= max(8, min(g['height'], word['height']) * 0.65)), None)
        if group is None:
            groups.append({'words': [word], 'cy': word['cy'], 'height': word['height']})
        else:
            group['words'].append(word)
            group['cy'] = sum(x['cy'] for x in group['words']) / len(group['words'])
            group['height'] = sum(x['height'] for x in group['words']) / len(group['words'])
    lines = []
    for group in groups:
        row = sorted(group['words'], key=lambda w: w['left'])
        l, t = min(w['left'] for w in row), min(w['top'] for w in row)
        r, b = max(w['right'] for w in row), max(w['bottom'] for w in row)
        lines.append({'text': ' '.join(w['text'] for w in row), 'left': round(l, 2), 'top': round(t, 2), 'width': round(r-l, 2), 'height': round(b-t, 2)})
    lines.sort(key=lambda x: (x['top'], x['left']))
    return text, lines