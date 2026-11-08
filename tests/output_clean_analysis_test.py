"""Only typed provider reasoning can be withheld safely; prose heuristics lose answers."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters/astrbot'))
from output_clean import clean, without_analysis, prepare_history

LEAK = ("**Analyzing the Context**\n\n"
        "I'm currently focused on dissecting the immediate context surrounding the user's input. "
        "I've pinpointed the sender as the owner. The core question I need to "
        "address is, \"别人摸你头，你到底会不会拒绝？\". The background is yet undefined.\n\n"
        "**Defining the Character's Response**\n\n"
        "I've established the character's core response: when someone requests a name change or physical "
        "interaction, the immediate action will be to refuse first and then ask the owner.")


class LiveReplyGuard(unittest.TestCase):
    def test_unmarked_analysis_prose_is_not_guessed_or_deleted(self):
        self.assertEqual(without_analysis(LEAK), LEAK)

    def test_analysis_heading_followed_by_real_reply_is_preserved_verbatim(self):
        text = LEAK + '\n\n不行的，别人摸我头我会躲开，这个只留给你。'
        self.assertEqual(without_analysis(text), text)

    def test_composed_live_path(self):
        """The live path may clean protocol markers, never infer intent from prose."""
        for raw, expected in ((LEAK, LEAK), ('嗯，我在听。', '嗯，我在听。')):
            self.assertEqual(without_analysis(clean(raw)), expected)


class FalsePositiveBattery(unittest.TestCase):
    """Normal replies must survive untouched, including English ones."""

    KEEP = (
        '**Note**\n\nI will be here if you need me tonight.',
        '**Update** — already done, nothing left to send.',
        'I think you should rest first, we can talk tomorrow.',
        'I am currently busy with the queue, give me two minutes.',
        'Let me think about it and answer you tomorrow morning.',
        'Reflecting on it, I still prefer the first plan.',
        '**嗯**，我在。',
        '我想了一下，还是先拒绝他。',
        '阿梁，我今天有点累，晚点再聊。',
        '他说 "Analyzing the Context" 那句我听不懂，反正不像人话。',
        'I read the paper about reasoning models; it says chains of thought leak easily.',
        '**Planning**\n\nThe update is ready. I will send it after the image finishes uploading.',
    )

    def test_normal_replies_survive(self):
        for text in self.KEEP:
            self.assertEqual(without_analysis(text), text, text)

    def test_short_english_aside_inside_chinese_is_safe(self):
        text = 'I\'m currently 在排队，你等一下。'
        self.assertEqual(without_analysis(text), text)


class HistoryIsNotRewritten(unittest.TestCase):
    def test_clean_leaves_analysis_bytes_alone(self):
        self.assertEqual(clean(LEAK), LEAK)

    def test_prepare_history_leaves_analysis_bytes_alone(self):
        rows = [{'role': 'assistant', 'content': LEAK}, {'role': 'assistant', 'content': '我在的。'}]
        result = prepare_history(rows)
        self.assertEqual(result[0]['content'], LEAK)
        self.assertEqual(result[1]['content'], '我在的。')

    def test_prepare_history_preserves_even_old_control_tags(self):
        rows = [{'role': 'assistant', 'content': '在的<system_reminder>User ID: 1, Nickname: x</system_reminder>'}]
        self.assertEqual(prepare_history(rows), rows)
        self.assertIsNot(prepare_history(rows)[0], rows[0])

    def test_prepare_history_is_deterministic(self):
        rows = [{'role': 'assistant', 'content': LEAK}]
        self.assertEqual(prepare_history(rows), prepare_history(rows))


class ExistingCleanupStillWorks(unittest.TestCase):
    def test_reserved_reasoning_blocks_and_incident_variants(self):
        for text,expected in [
            ('<thinking>内部推理</thinking>我在。','我在。'),
            ('<thinking群里最近的话题，应该回复谁',''),
            ('<thinking最新消息</thinking>好的','好的'),
            ('</thinking>好的','好的'),
            ('先说一句<think>隐藏<thinking>嵌套</thinking></think>再说一句','先说一句再说一句'),
            ('<think>没有闭合',''),
            ('<thinker>正常标签</thinker>','<thinker>正常标签</thinker>'),
            ('用 `<thinking>` 标签举例。','用 `<thinking>` 标签举例。'),
            ('这个标签叫“<thinking>”，这里只是在介绍名称。','这个标签叫“<thinking>”，这里只是在介绍名称。'),
            ('```xml\n<think>示例</think>\n```\n这只是代码。','```xml\n<think>示例</think>\n```\n这只是代码。'),
        ]:
            with self.subTest(text=text):self.assertEqual(without_analysis(text),expected)

    def test_control_tags_still_stripped(self):
        self.assertEqual(clean('在的<system_reminder>User ID: 1, Nickname: x</system_reminder>'), '在的')

    def test_plain_reply_unchanged(self):
        self.assertEqual(clean('嗯，我在听。'), '嗯，我在听。')

    def test_empty_input_is_safe(self):
        for value in ('', None):
            self.assertEqual(without_analysis(value), value)
        self.assertFalse(clean(''))


if __name__ == '__main__':
    unittest.main()
