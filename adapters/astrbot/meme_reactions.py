"""Small, bounded reaction matching; no model call, image decoding or history scan."""
import re
from hosted_memory import tokens

# These describe emotions/actions, not arbitrary topic words. Ambiguous positive
# phrases and aggressive/joking-threat packs are never selected automatically.
SIGNALS = {
    'affection': r'想你|想我|喜欢你|喜欢我|爱你|爱我|抱抱|亲亲|摸摸|摸头|贴贴|谢谢|辛苦了',
    'happy': r'开心|高兴|哈哈|笑死|恭喜|庆祝|太棒|好耶|太好了|好消息|成功了',
    'confused': r'困惑|疑惑|不懂|没懂|不明白|没明白|没看懂|没想明白|搞不懂|什么意思|啥意思|怎么回事|懵了',
    'sad': r'难过|伤心|委屈|不开心|不高兴|想哭|哭了|失落',
    'sleep': r'困了|想睡|睡觉|晚安|休息|歇一会|歇会|好累',
    'shy': r'害羞|脸红|不好意思',
    'food': r'吃饭|开饭|饿了|好饿|白饭|米饭|好吃|美食',
    'greeting': r'早安|早上好|晚上好|回来啦|回来了|我来了|你好|冒泡',
    'surprised': r'吓一跳|吓到|吓死|震惊|没想到',
}
TITLES = {
    'affection': r'爱心|抱|亲|摸头|玫瑰|贴',
    'happy': r'笑|开心|庆祝|鼓掌|兴奋|点头|得分',
    'confused': r'问|惑|挠头|沉思|思考|迷茫|懵',
    'sad': r'哭|泪|委屈|难过|伤心',
    'sleep': r'睡|困|休息|小憩',
    'shy': r'害羞|脸红|羞|不好意思',
    'food': r'吃|饭|饿|食|面|饺|蛋糕|汉堡',
    'greeting': r'招手|挥手|冒泡|探头|你好|早安|来了|Raid',
    'surprised': r'惊|吓|害怕|震',
}
SERIOUS = re.compile(r'去世|自杀|丧事|重病|葬礼|抢救|病危|求救|家暴')


def categories(text, questions=True):
    result = set()
    for category, pattern in SIGNALS.items():
        for match in re.finditer(pattern, text):
            # Fail closed on nearby negation, including "不是不喜欢你".
            if category not in ('sad', 'confused') and re.search(r'不|没|别|无', text[max(0, match.start()-3):match.start()]):
                continue
            result.add(category)
    if 'sad' in result: result.discard('happy')
    if not result and questions and re.search(r'[?？]',text):result.add('confused')
    return result


def relevance(item, text, reactions=None):
    reactions = categories(text) if reactions is None else reactions
    title = re.split(r'[；;]', item['meaning'], maxsplit=1)[0]
    query = tokens(text)
    category = item.get('category')
    matching = category in reactions
    expressive = bool(matching and re.search(TITLES.get(category, r'(?!)'), title))
    return 30*matching + 10*expressive + 4*len(query & tokens(title)) + len(query & tokens(item['meaning']))


def automatic_image(items, offered, response, request):
    if SERIOUS.search(request + '\n' + response): return None
    reactions = categories(response) or categories(request, questions=False)
    approved = {item['id']: item for item in items if item['id'].startswith('bf_') and not item.get('sticker')}
    offered_ids={item.get('id') for item in offered}
    if not approved:
        approved={item['id']:item for item in items if item['id'] in offered_ids and not item.get('sticker') and not item['id'].startswith('qqf_')}
    choices = []
    for item in approved.values():
        if item.get('category') not in reactions: continue
        title = re.split(r'[；;]', item['meaning'], maxsplit=1)[0]
        if re.search(TITLES.get(item['category'], r'(?!)'), title): choices.append(item)
    # The final reply can introduce an expression absent from the incoming
    # message's eight-item menu. Search this bot's currently enabled pack only,
    # rather than requiring the model to have anticipated its final wording.
    if not choices: choices=list(approved.values())
    return max(choices, key=lambda item: (relevance(item, response+'\n'+request, reactions) if reactions else 0, bool(re.search(r'思考|沉思|冒泡|探头|喝茶|点头',item['meaning'].split('；')[0])), relevance(item, response+'\n'+request, reactions), item['id'] in offered_ids), default=None)
