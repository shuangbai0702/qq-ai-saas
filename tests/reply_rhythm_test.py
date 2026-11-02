import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from output_clean import prepare_history,normalize_image_markers,without_image_markers
from initiative import split_text
from message_layout import without_boundaries,repair_soft_wraps


class ReplyRhythmTest(unittest.TestCase):
    def test_mid_sentence_cjk_folding_is_repaired_without_merging_bubbles(self):
        raw='开发\n我？！\n\n你个代码怪！\n大半夜不睡觉，居然还在连\n夜“开发”本鱼，说\n得本鱼好像是什么待上线的新\n产品一样！\n\n我警告你啊\n，本鱼只吃白米饭，不\n吃任何奇怪的数据流！'
        expected=['开发我？！','你个代码怪！\n大半夜不睡觉，居然还在连夜“开发”本鱼，说得本鱼好像是什么待上线的新产品一样！','我警告你啊，本鱼只吃白米饭，不吃任何奇怪的数据流！']
        self.assertEqual(split_text(raw),expected)
        self.assertEqual(split_text('[QQ_BREAK]'.join(expected)),expected)
        self.assertEqual(prepare_history([{'role':'assistant','content':raw}])[0]['content'],'\n\n'.join(expected))
        self.assertEqual(prepare_history([{'role':'user','content':raw}])[0]['content'],raw)

    def test_soft_wrap_repair_preserves_structured_and_explicit_formatting(self):
        for raw in ['```py\n名字=1\n```','- 第一个\n- 第二个','> 引用\n下一行','| 名字 |\n| 数值 |','姓名：\n双白','甲句。\n乙句。','first\nsecond','保留  \n换行']:
            self.assertEqual(repair_soft_wraps(raw),raw)
    def test_short_assistant_history_keeps_natural_paragraphs(self):
        history=[{'role':'assistant','content':'听起来不错。\n\n我也想看看。\n你觉得呢？'}]
        before=copy.deepcopy(history)
        self.assertEqual(prepare_history(history),history)
        self.assertEqual(history,before)

    def test_user_tool_and_call_arguments_are_untouched(self):
        history=[{'role':'user','content':'第一行\n第二行'},
                 {'role':'tool','content':'a\nb','tool_call_id':'a'},
                 {'role':'assistant','content':None,'tool_calls':[{'function':{'arguments':'{"text":"a\\nb"}'}}]}]
        self.assertEqual(prepare_history(history),history)

    def test_code_lists_tables_and_long_answers_keep_layout(self):
        for text in ['```py\na()\n```','- 第一项\n- 第二项','1. first\n2. second','> quotation\ncontinued','| a | b |\n| c | d |','长'*1501+'\n结束']:
            with self.subTest(text=text[:20]):
                self.assertEqual(prepare_history([{'role':'assistant','content':text}])[0]['content'],text)

    def test_current_model_still_chooses_one_through_four_bubbles(self):
        for count in range(1,5):
            parts=['完整想法'+str(i) for i in range(count)]
            self.assertEqual(split_text('[QQ_BREAK]'.join(parts)),parts)
        self.assertEqual(split_text('一[QQ_BREAK]二[QQ_BREAK]三[QQ_BREAK]四[QQ_BREAK]五'),['一','二','三','四\n\n五'])

    def test_natural_one_to_four_paragraphs_are_distinct_messages(self):
        for count in range(1,5):
            parts=['自然内容'+str(i) for i in range(count)]
            self.assertEqual(split_text('\n\n'.join(parts)),parts)
        self.assertEqual(split_text('一句话。\n\n换行排版。'),['一句话。','换行排版。'])
        self.assertEqual(split_text('同一段的\n自动折行。'),['同一段的自动折行。'])
        self.assertEqual(split_text('一\n\n二\n\n三\n\n四\n\n五'),['一','二','三','四\n\n五'])

    def test_boundaries_in_code_are_literal(self):
        text='```text\n[QQ_BREAK]\n```'
        self.assertEqual(split_text(text+'[QQ_BREAK]结束'),[text,'结束'])
        self.assertEqual(without_boundaries(text+'[QQ_BREAK]结束'),text+'\n\n结束')

    def test_disabled_splitting_and_history_never_leak_boundary_tokens(self):
        self.assertEqual(without_boundaries('第一条[QQ_BREAK]第二条'),'第一条\n\n第二条')
        self.assertEqual(prepare_history([{'role':'assistant','content':'第一条[QQ_BREAK]第二条'}])[0]['content'],'第一条\n\n第二条')

    def test_old_internal_tags_are_not_reintroduced(self):
        result=prepare_history([{'role':'assistant','content':'<system_reminder>internal</system_reminder>第一句\n第二句'}])
        self.assertEqual(result[0]['content'],'第一句第二句')

    def test_wrapped_image_controls_never_become_dialogue_or_new_image_ids(self):
        text='[MEM\nE:bf_00dfc51307818\ne05]回应'
        self.assertEqual(normalize_image_markers(text),'[MEME:bf_00dfc51307818e05]回应')
        self.assertEqual(without_image_markers(text),'回应')
        self.assertEqual(prepare_history([{'role':'assistant','content':text}])[0]['content'],'回应')
        self.assertEqual(normalize_image_markers('[MEME:../../private]安全'),'安全')
        self.assertEqual(without_image_markers('正常的[备忘：下一行\n再说]'),'正常的[备忘：下一行\n再说]')


if __name__=='__main__':unittest.main()
