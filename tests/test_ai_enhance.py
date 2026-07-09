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
