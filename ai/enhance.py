import os
import json
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict
from queue import Queue
from threading import Lock
# INSERT_YOUR_CODE
import requests

import dotenv
import argparse
from tqdm import tqdm

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from json_repair import repair_json
from tenacity import retry, stop_after_attempt, wait_random_exponential

try:
    from ai.structure import Structure
except ImportError:
    from structure import Structure

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(root_dir, '.env')):
    dotenv.load_dotenv(os.path.join(root_dir, '.env'))
elif os.path.exists('.env'):
    dotenv.load_dotenv()

# 清理环境变量中的空白符和换行符，防止因 \r 导致 URL 解析报错
for k in os.environ:
    os.environ[k] = os.environ[k].strip()

template = open(os.path.join(current_dir, "template.txt"), "r", encoding="utf-8").read()
system = open(os.path.join(current_dir, "system.txt"), "r", encoding="utf-8").read()

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True, help="jsonline data file")
    parser.add_argument("--max_workers", type=int, default=15, help="Maximum number of parallel workers")
    return parser.parse_args()

def preprocess_latex_escapes(text: str) -> str:
    """预处理转义，将 LaTeX 命令（如 \frac, \alpha 等）补充转义为 \\frac"""
    if not text:
        return ""
    return re.sub(r"\\([a-zA-Z]{2,})", r"\\\\\1", text)

def repair_and_extract_json(raw_text: str) -> Dict:
    """智能从不规范文本或异常堆栈中提取并修复 JSON 对象"""
    if not raw_text:
        return {}
    processed_text = preprocess_latex_escapes(raw_text)
    try:
        res = repair_json(processed_text, return_objects=True)
        if isinstance(res, dict) and res:
            return res
    except Exception:
        pass

    if "Function Structure arguments:" in raw_text:
        try:
            json_str = raw_text.split("Function Structure arguments:", 1)[1].strip()
            if "are not valid JSON" in json_str:
                json_str = json_str.split("are not valid JSON")[0].strip()
            json_str = preprocess_latex_escapes(json_str)
            res = repair_json(json_str, return_objects=True)
            if isinstance(res, dict) and res:
                return res
        except Exception:
            pass

    return {}

def invoke_chain_with_retry(chain, inputs: Dict) -> Structure:
    """带指数退避和并发重试的 Chain 调用"""
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(min=1, max=6),
        reraise=True
    )
    def _invoke():
        return chain.invoke(inputs)
    return _invoke()

def process_single_item(chain, item: Dict, language: str) -> Dict:
    """处理单个数据项"""

    # Default structure with meaningful fallback values
    default_ai_fields = {
        "translated_title": "",
        "tldr": "Summary generation failed",
        "motivation": "Motivation analysis unavailable",
        "method": "Method extraction failed",
        "result": "Result analysis unavailable",
        "conclusion": "Conclusion extraction failed",
        "remote_sensing_cross": "Remote sensing cross-disciplinary scheme unavailable"
    }

    inputs = {
        "language": language,
        "content": item.get('summary', ''),
        "title": item.get('title', '')
    }

    try:
        response: Structure = invoke_chain_with_retry(chain, inputs)
        item['AI'] = response.model_dump()
    except langchain_core.exceptions.OutputParserException as e:
        error_msg = str(e)
        print(f"OutputParserException for {item.get('id', 'unknown')}, trying json_repair...", file=sys.stderr)
        partial_data = repair_and_extract_json(error_msg)
        item['AI'] = {**default_ai_fields, **partial_data}
        print(f"Using repaired AI data for {item.get('id', 'unknown')}: {list(partial_data.keys())}", file=sys.stderr)
    except Exception as e:
        error_msg = str(e)
        print(f"Unexpected error for {item.get('id', 'unknown')}: {e}", file=sys.stderr)
        partial_data = repair_and_extract_json(error_msg)
        item['AI'] = {**default_ai_fields, **partial_data}

    # Final validation to ensure all required fields exist
    for field in default_ai_fields.keys():
        if field not in item['AI'] or not item['AI'][field]:
            if field not in item['AI']:
                item['AI'][field] = default_ai_fields[field]

    return item

def process_all_items(data: List[Dict], model_name: str, language: str, max_workers: int) -> List[Dict]:
    """并行处理所有数据项"""
    if model_name.lower().startswith("gemini"):
        llm = ChatGoogleGenerativeAI(
            model=model_name,
        ).with_structured_output(Structure)
    else:
        llm = ChatOpenAI(
            model=model_name,
            extra_body={"thinking": {"type": "disabled"}}
        ).with_structured_output(Structure, method="function_calling")

    print('Connect to:', model_name, file=sys.stderr)
    
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system),
        HumanMessagePromptTemplate.from_template(template=template)
    ])

    chain = prompt_template | llm
    
    # 使用线程池并行处理
    processed_data = [None] * len(data)  # 预分配结果列表
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_idx = {
            executor.submit(process_single_item, chain, item, language): idx
            for idx, item in enumerate(data)
        }
        
        # 使用tqdm显示进度
        for future in tqdm(
            as_completed(future_to_idx),
            total=len(data),
            desc="Processing items"
        ):
            idx = future_to_idx[future]
            try:
                result = future.result()
                processed_data[idx] = result
            except Exception as e:
                print(f"Item at index {idx} generated an exception: {e}", file=sys.stderr)
                # Add default AI fields to ensure consistency
                processed_data[idx] = data[idx]
                processed_data[idx]['AI'] = {
                    "translated_title": "",
                    "tldr": "Processing failed",
                    "motivation": "Processing failed",
                    "method": "Processing failed",
                    "result": "Processing failed",
                    "conclusion": "Processing failed",
                    "remote_sensing_cross": "Processing failed"
                }
    
    return processed_data

def main():
    args = parse_args()
    model_name = os.environ.get("MODEL_NAME", 'deepseek-chat')
    language = os.environ.get("LANGUAGE", 'Chinese')

    # 检查并删除目标文件
    target_file = args.data.replace('.jsonl', f'_AI_enhanced_{language}.jsonl')
    if os.path.exists(target_file):
        os.remove(target_file)
        print(f'Removed existing file: {target_file}', file=sys.stderr)

    # 读取数据
    data = []
    with open(args.data, "r", encoding="utf-8") as f:
        for line in f:
            data.append(json.loads(line))

    # 去重
    seen_ids = set()
    unique_data = []
    for item in data:
        if item['id'] not in seen_ids:
            seen_ids.add(item['id'])
            unique_data.append(item)

    data = unique_data
    print('Open:', args.data, file=sys.stderr)
    
    # 并行处理所有数据
    processed_data = process_all_items(
        data,
        model_name,
        language,
        args.max_workers
    )
    
    # 保存结果
    with open(target_file, "w", encoding="utf-8") as f:
        for item in processed_data:
            if item is not None:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()
