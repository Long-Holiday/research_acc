import os
import json
import sys
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
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

def build_chain(model_name: str):
    """构建 LangChain 处理链（供流式处理复用，避免每个 worker 重复创建）。"""
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

    return prompt_template | llm


def _count_unique_ids(filepath: str) -> int:
    """快速统计去重后的论文条数（仅用于进度条，不做完整 JSON 解析）。"""
    seen = set()
    id_pat = re.compile(r'"id"\s*:\s*"([^"]+)"')
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            match = id_pat.search(line)
            if match:
                seen.add(match.group(1))
    return len(seen)


def process_all_items_streaming(
    input_path: str,
    output_path: str,
    model_name: str,
    language: str,
    max_workers: int,
):
    """流式并行处理所有数据项：边读边去重、边处理边写结果文件。

    相比一次性把全部数据读入内存（data + 去重副本 + 结果副本 + 全部 future），
    这里用「滑动窗口」限制同时在途的任务数为 max_workers，结果完成后立即落盘，
    内存占用只与并发窗口大小相关，不随论文总数增长。
    """
    chain = build_chain(model_name)
    total = _count_unique_ids(input_path)

    with ThreadPoolExecutor(max_workers=max_workers) as executor, \
            open(input_path, "r", encoding="utf-8") as fin, \
            open(output_path, "w", encoding="utf-8") as fout:

        seen_ids = set()
        pending = deque()

        def flush_one(future):
            try:
                result = future.result()
            except Exception as e:
                print(f"Item generated an exception: {e}", file=sys.stderr)
                return
            if result is not None:
                fout.write(json.dumps(result, ensure_ascii=False) + "\n")

        with tqdm(total=total, desc="Processing items") as pbar:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue

                pid = item.get('id')
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)

                pending.append(
                    executor.submit(process_single_item, chain, item, language)
                )

                # 窗口满时，按提交顺序写出最早完成的结果，控制内存
                if len(pending) >= max_workers:
                    flush_one(pending.popleft())
                    pbar.update(1)

            while pending:
                flush_one(pending.popleft())
                pbar.update(1)


def main():
    args = parse_args()
    model_name = os.environ.get("MODEL_NAME", 'deepseek-chat')
    language = os.environ.get("LANGUAGE", 'Chinese')

    # 检查并删除目标文件
    target_file = args.data.replace('.jsonl', f'_AI_enhanced_{language}.jsonl')
    if os.path.exists(target_file):
        os.remove(target_file)
        print(f'Removed existing file: {target_file}', file=sys.stderr)

    print('Open:', args.data, file=sys.stderr)

    process_all_items_streaming(
        input_path=args.data,
        output_path=target_file,
        model_name=model_name,
        language=language,
        max_workers=args.max_workers,
    )

if __name__ == "__main__":
    main()
