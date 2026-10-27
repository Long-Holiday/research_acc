from pydantic import BaseModel, Field


class Structure(BaseModel):
    translated_title: str = Field(description="translate the paper's title into Chinese (or the specified target language)")
    tldr: str = Field(description="generate a too long; didn't read summary. If abstract is empty/missing, fill '缺失摘要'")
    motivation: str = Field(description="describe the motivation in this paper. If abstract is empty/missing, fill '缺失摘要'")
    method: str = Field(description="method of this paper. If abstract is empty/missing, fill '缺失摘要'")
    result: str = Field(description="result of this paper. If abstract is empty/missing, fill '缺失摘要'")
    conclusion: str = Field(description="conclusion of this paper. If abstract is empty/missing, fill '缺失摘要'")
