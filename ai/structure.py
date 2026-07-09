from pydantic import BaseModel, Field, field_validator
import re

class Structure(BaseModel):
    translated_title: str = Field(description="translate the paper's title into Chinese (or the specified target language)")
    tldr: str = Field(description="generate a too long; didn't read summary")
    motivation: str = Field(description="describe the motivation in this paper")
    method: str = Field(description="method of this paper")
    result: str = Field(description="result of this paper")
    conclusion: str = Field(description="conclusion of this paper")
    remote_sensing_cross: str = Field(
        description="与遥感交叉或者改进方案。如果本身就是遥感论文，给出有哪些可以改进的地方（改进方案）；如果是其他学科的论文，给出与遥感交叉的具体方案。开头第一句必须先给出可行性百分比，格式为：交叉/改进可行性：XX%。，后面再给出具体方案。"
    )

    @field_validator("remote_sensing_cross")
    @classmethod
    def validate_remote_sensing_cross(cls, v: str) -> str:
        v = v.strip()
        # 尝试匹配文本开头的百分比数字
        # 比如 "交叉/改进可行性：95%。具体方案..." 或 "交叉可行性：85%。具体方案..." 或 "改进可行性：90%。具体方案..."
        match_start = re.match(r"^(?:交叉/改进可行性|交叉可行性|改进可行性)\s*[：:]\s*(\d+)\s*%\s*[。.]?[ \t]*(.*)", v, re.IGNORECASE | re.DOTALL)
        if match_start:
            percent = match_start.group(1)
            content = match_start.group(2).lstrip(' \t').rstrip()
            return f"交叉/改进可行性：{percent}%。{content}"
            
        # 如果不是标准前缀开头，但文本里包含 XX% 格式，则提取第一个百分比
        match_any = re.search(r"(\d+)\s*%", v)
        if match_any:
            percent = match_any.group(1)
            # 清理掉可能存在的其他类似前缀
            content = re.sub(r"^(?:交叉/改进可行性|交叉可行性|改进可行性)\s*[：:]\s*\d+\s*%\s*[。.]?", "", v, flags=re.IGNORECASE).lstrip(' \t').rstrip()
            return f"交叉/改进可行性：{percent}%。{content}"
        else:
            return f"交叉/改进可行性：70%。{v}"
