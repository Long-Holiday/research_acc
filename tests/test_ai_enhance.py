import pytest
from pydantic import ValidationError
from ai.structure import Structure

def test_structure_translated_title():
    # Missing translated_title should fail validation
    data = {
        "tldr": "TLDR",
        "motivation": "Motivation",
        "method": "Method",
        "result": "Result",
        "conclusion": "Conclusion",
        "remote_sensing_cross": "交叉/改进可行性：80%。方案内容"
    }
    with pytest.raises(ValidationError):
        Structure(**data)
        
    # Complete data should pass
    data["translated_title"] = "翻译后的标题"
    obj = Structure(**data)
    assert obj.translated_title == "翻译后的标题"

def test_validate_remote_sensing_cross_standard():
    # Standard format with various prefixes
    res1 = Structure.validate_remote_sensing_cross("交叉/改进可行性：95%。具体方案内容")
    assert res1 == "交叉/改进可行性：95%。具体方案内容"

    res2 = Structure.validate_remote_sensing_cross("交叉可行性：85%。具体方案")
    assert res2 == "交叉/改进可行性：85%。具体方案"

    res3 = Structure.validate_remote_sensing_cross("改进可行性：90%。具体方案")
    assert res3 == "交叉/改进可行性：90%。具体方案"

def test_validate_remote_sensing_cross_multiline():
    # Multi-line format
    input_val = "交叉/改进可行性：95%。\n第一行方案内容\n第二行方案内容"
    res = Structure.validate_remote_sensing_cross(input_val)
    assert res == "交叉/改进可行性：95%。\n第一行方案内容\n第二行方案内容"

def test_validate_remote_sensing_cross_spaces():
    # Spaces in prefix
    res = Structure.validate_remote_sensing_cross("交叉/改进可行性  :  95  %  .   具体方案内容")
    assert res == "交叉/改进可行性：95%。具体方案内容"

def test_validate_remote_sensing_cross_invalid_prefix_with_percentage():
    # Invalid prefix but having percentage
    res1 = Structure.validate_remote_sensing_cross("该方案具有 85% 的可行性，需要进一步优化。")
    assert res1 == "交叉/改进可行性：85%。该方案具有 85% 的可行性，需要进一步优化。"
    
    # Prefix not matching at start but percentage exists
    res2 = Structure.validate_remote_sensing_cross("一些前导文字 交叉可行性：85%。方案内容")
    assert res2 == "交叉/改进可行性：85%。一些前导文字 交叉可行性：85%。方案内容"

def test_validate_remote_sensing_cross_fallback():
    # Default fallback when no percentage is provided
    res = Structure.validate_remote_sensing_cross("这是一个完全没有百分比的改进方案内容。")
    assert res == "交叉/改进可行性：70%。这是一个完全没有百分比的改进方案内容。"

from ai.enhance import process_single_item, repair_and_extract_json
import langchain_core.exceptions

class MockChain:
    def __init__(self, should_fail=False, raise_parser_error=False):
        self.should_fail = should_fail
        self.raise_parser_error = raise_parser_error
        
    def invoke(self, inputs):
        if self.raise_parser_error:
            raise langchain_core.exceptions.OutputParserException(
                r'Function Structure arguments: {"translated_title": "修复标题", "tldr": "公式 \alpha + \beta 成功提取", "motivation": "动机", "method": "方法", "result": "结果", "conclusion": "结论", "remote_sensing_cross": "交叉/改进可行性：85%。具体方案"} are not valid JSON'
            )
        if self.should_fail:
            raise Exception("Mock invocation failure")
        # Return mock structure
        return Structure(
            translated_title=f"翻译：{inputs.get('title')}",
            tldr="TLDR summary",
            motivation="Motivation summary",
            method="Method summary",
            result="Result summary",
            conclusion="Conclusion summary",
            remote_sensing_cross="交叉/改进可行性：80%。方案内容"
        )

def test_process_single_item_success():
    chain = MockChain()
    item = {
        "title": "Pixel Stress Indexing",
        "summary": "Plant diseases cause global losses in agricultural production and food security."
    }
    res = process_single_item(chain, item, "Chinese")
    assert "AI" in res
    assert res["AI"]["translated_title"] == "翻译：Pixel Stress Indexing"

def test_process_single_item_fallback():
    chain = MockChain(should_fail=True)
    item = {
        "title": "Pixel Stress Indexing",
        "summary": "Plant diseases cause global losses in agricultural production and food security."
    }
    res = process_single_item(chain, item, "Chinese")
    assert "AI" in res
    assert res["AI"]["translated_title"] == ""
    assert res["AI"]["tldr"] == "Summary generation failed"

def test_process_single_item_parser_error_repaired():
    chain = MockChain(raise_parser_error=True)
    item = {
        "title": "LaTeX Test",
        "summary": "LaTeX summary"
    }
    res = process_single_item(chain, item, "Chinese")
    assert "AI" in res
    assert res["AI"]["translated_title"] == "修复标题"
    assert "alpha" in res["AI"]["tldr"]

def test_repair_and_extract_json():
    bad_str = r'{"translated_title": "标题", "tldr": "含有 \frac{1}{2} 公式", "motivation": "动机", "method": "方法", "result": "结果", "conclusion": "结论", "remote_sensing_cross": "交叉/改进可行性：90%。方案"}'
    parsed = repair_and_extract_json(bad_str)
    assert parsed["translated_title"] == "标题"
    assert "frac" in parsed["tldr"]


def test_validate_remote_sensing_cross_missing_abstract():
    assert Structure.validate_remote_sensing_cross("缺失摘要") == "缺失摘要"
    assert Structure.validate_remote_sensing_cross("  缺失摘要  ") == "缺失摘要"


def test_structure_with_missing_abstract_fields():
    data = {
        "translated_title": "遥感图像变化检测新方法",
        "tldr": "缺失摘要",
        "motivation": "缺失摘要",
        "method": "缺失摘要",
        "result": "缺失摘要",
        "conclusion": "缺失摘要",
        "remote_sensing_cross": "缺失摘要"
    }
    s = Structure(**data)
    assert s.translated_title == "遥感图像变化检测新方法"
    assert s.tldr == "缺失摘要"
    assert s.remote_sensing_cross == "缺失摘要"


def test_process_single_item_empty_abstract():
    class MissingMockChain:
        def invoke(self, inputs):
            return Structure(
                translated_title=f"翻译：{inputs.get('title')}",
                tldr="缺失摘要",
                motivation="缺失摘要",
                method="缺失摘要",
                result="缺失摘要",
                conclusion="缺失摘要",
                remote_sensing_cross="缺失摘要"
            )

    chain = MissingMockChain()
    item = {
        "title": "Remote Sensing Foundation Models",
        "summary": "No abstract available in OpenAlex."
    }
    res = process_single_item(chain, item, "Chinese")
    assert "AI" in res
    assert res["AI"]["translated_title"] == "翻译：Remote Sensing Foundation Models"
    assert res["AI"]["tldr"] == "缺失摘要"
    assert res["AI"]["motivation"] == "缺失摘要"
    assert res["AI"]["method"] == "缺失摘要"
    assert res["AI"]["result"] == "缺失摘要"
    assert res["AI"]["conclusion"] == "缺失摘要"
    assert res["AI"]["remote_sensing_cross"] == "缺失摘要"


