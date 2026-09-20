#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from json import encoder
import re

def is_english(texts):
    print("[DEBUG] enter is_english | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    eng = 0
    if not texts: return False
    for t in texts:
        if re.match(r"[ `a-zA-Z.,':;/\"?<>!\(\)-]", t.strip()):
            eng += 1
    if eng / len(texts) > 0.8:
        return True
    return False

def num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    print("[DEBUG] enter num_tokens_from_string | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    try:
        return len(encoder.encode(string))
    except Exception:
        return 0
    
def clean_text(text):
    # 替换多个换行符为一个换行符
    print("[DEBUG] enter clean_text | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    cleaned_text = "\n".join(text.splitlines())
    # 去除每行前后的空白
    cleaned_text = "\n".join([line.strip() for line in cleaned_text.split("\n") if line.strip() != ""])
    return cleaned_text

def chunk_text(text, max_length=5000):
    print("[DEBUG] enter chunk_text | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    chunks = []
    if len(text) > max_length:
        for i in range(0, len(text), max_length):
            chunks.append(text[i:i+max_length])
    else:
        chunks.append(text)
    return chunks   

