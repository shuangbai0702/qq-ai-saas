"""Natural chat paragraphs and explicit boundaries share the QQ output plan."""
import re

MESSAGE_BREAK = '[QQ_BREAK]'
_FENCES = re.compile(r'(```|~~~)[\s\S]*?(?:\1|$)')
_STRUCTURED = re.compile(r'(?m:^\s*(?:[-*+#>]|\d+[.)、]|\|))')


def normalize_chat_layout(text):
    """QQ plain text cannot render HTML breaks; retain literal code examples."""
    def prose(value):
        value = re.sub(r'<br\s*/?\s*>', '\n', value, flags=re.I)
        value = re.sub(r'\n[ \t]*(?:[-*_][ \t]*){3,}(?=\n|$)(?:\s*\n)*$', '', value)
        return re.sub(r'\n(?:[ \t]*\n){2,}', '\n\n', value)
    chunks=[]; end=0
    # Inline code as well as fenced code remains literal.
    for match in re.finditer(r'(```|~~~)[\s\S]*?(?:\1|$)|`[^`\n]*`', text):
        chunks.extend((prose(text[end:match.start()]), match.group()))
        end=match.end()
    chunks.append(prose(text[end:]))
    return ''.join(chunks).strip()


def repair_soft_wraps(text):
    """Repair CJK mid-sentence folding without erasing paragraphs or layout."""
    if len(text)>1500 or _FENCES.search(text) or _STRUCTURED.search(text):return text
    def repair(match):
        before=text[:match.start()];after=text[match.end():]
        if match.group().startswith('  ') or before.rstrip().endswith(tuple('。！？!?；;：:…')):return match.group()
        left=before.rstrip()[-1:];right=after.lstrip()[:1]
        if not re.search(r'[\u3400-\u9fff，。！？；：、）】」』]',left+right):return match.group()
        return ''
    return re.sub(r'(?<=\S)[ \t]*\r?\n[ \t]*(?=\S)',repair,text)


def split_messages(text, limit=4):
    """Keep prose paragraphs separate; code/list formatting is not chat pacing."""
    text = normalize_chat_layout(text)
    chunks = ['']
    end = 0
    for match in _FENCES.finditer(text):
        pieces = text[end:match.start()].split(MESSAGE_BREAK)
        chunks[-1] += pieces[0]
        chunks.extend(pieces[1:])
        chunks[-1] += match.group()
        end = match.end()
    pieces = text[end:].split(MESSAGE_BREAK)
    chunks[-1] += pieces[0]
    chunks.extend(pieces[1:])
    chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
    structured=bool(_FENCES.search(text) or _STRUCTURED.search(text))
    if not structured:chunks=[repair_soft_wraps(chunk) for chunk in chunks]
    if len(chunks)==1 and not structured:
        chunks=[part.strip() for part in re.split(r'\r?\n\s*\r?\n+',chunks[0]) if part.strip()]
    limit = max(1, min(4, limit))
    if len(chunks) > limit:
        chunks = chunks[:limit-1] + ['\n\n'.join(chunks[limit-1:])]
    return chunks or ['']


def without_boundaries(text):
    return split_messages(text, limit=1)[0]
