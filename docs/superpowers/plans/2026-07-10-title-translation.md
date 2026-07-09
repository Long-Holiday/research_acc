# Paper Title Translation & Bilingual Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate paper titles from English to Chinese during AI enhancement and display the bilingual titles (English top, Chinese bottom with a smaller/lighter style) in the frontend lists, detail modals, and statistics page.

**Architecture:** Extend Pydantic Structure schema to request translation of the paper title, update LangChain prompt template and enhancement engine logic, map the field in frontend normalizing functions, update HTML rendering blocks in JS, and append styles to CSS files.

**Tech Stack:** Python, LangChain, Pydantic, FastAPI, HTML, Vanilla CSS, JS

## Global Constraints
- Do not import external packages not currently listed in pyproject.toml / uv.lock.
- Preserve all existing comments and docstrings.
- All tasks must adhere to TDD: write test first, run to fail, implement, run to pass, commit.

---

### Task 1: Add translated_title Field to Pydantic Model and Prompt Template

**Files:**
- Modify: `ai/structure.py` (Add `translated_title` Pydantic field)
- Modify: `ai/template.txt` (Include `Title: {title}` variable in template)
- Create: `tests/test_ai_enhance.py` (Test case for Pydantic schema validation)

**Interfaces:**
- Produces: `Structure.translated_title` property (string) for the parsed LLM output.

- [ ] **Step 1: Write the failing test**

  Create `tests/test_ai_enhance.py` and write a test ensuring `Structure` expects and validates a `translated_title` field.
  
  ```python
  # tests/test_ai_enhance.py
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
  ```

- [ ] **Step 2: Run test to verify it fails**

  Run: `pytest tests/test_ai_enhance.py::test_structure_translated_title -v`
  Expected: FAIL with ValidationError not raised or ImportError.

- [ ] **Step 3: Write minimal implementation**

  Modify [ai/structure.py](file:///home/default_user/research_acc/ai/structure.py) by adding `translated_title` field:
  
  ```python
  # ai/structure.py
  # ... existing imports ...
  class Structure(BaseModel):
      translated_title: str = Field(description="translate the paper's title into Chinese (or the specified target language)")
      tldr: str = Field(description="generate a too long; didn't read summary")
      # ... existing fields ...
  ```

  And modify [ai/template.txt](file:///home/default_user/research_acc/ai/template.txt) to include `title`:
  
  ```text
  Title: {title}
  Please analyze the following abstract of papers. 

  Content:
  {content}
  ```

- [ ] **Step 4: Run test to verify it passes**

  Run: `pytest tests/test_ai_enhance.py::test_structure_translated_title -v`
  Expected: PASS

- [ ] **Step 5: Commit**

  ```bash
  git add tests/test_ai_enhance.py ai/structure.py ai/template.txt
  git commit -m "feat(ai): add translated_title field to Pydantic model and prompt template"
  ```

---

### Task 2: Update AI Enhance Engine logic in enhance.py

**Files:**
- Modify: `ai/enhance.py` (Add defaults, pass title to model invoke, handle fallback failures)
- Modify: `tests/test_ai_enhance.py` (Add test for process_single_item)

**Interfaces:**
- Consumes: `item['title']` from input jsonl entry.
- Produces: Enhanced `item['AI']['translated_title']` string inside processed JSONL structure.

- [ ] **Step 1: Write the failing test**

  Add test for `process_single_item` functionality under `tests/test_ai_enhance.py`:
  
  ```python
  # tests/test_ai_enhance.py
  # ... existing imports ...
  from ai.enhance import process_single_item

  class MockChain:
      def __init__(self, should_fail=False):
          self.should_fail = should_fail
          
      def invoke(self, inputs):
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
          "summary": "Plant diseases cause global losses."
      }
      res = process_single_item(chain, item, "Chinese")
      assert "AI" in res
      assert res["AI"]["translated_title"] == "翻译：Pixel Stress Indexing"

  def test_process_single_item_fallback():
      chain = MockChain(should_fail=True)
      item = {
          "title": "Pixel Stress Indexing",
          "summary": "Plant diseases cause global losses."
      }
      res = process_single_item(chain, item, "Chinese")
      assert "AI" in res
      assert res["AI"]["translated_title"] == "Title translation failed"
  ```

- [ ] **Step 2: Run test to verify it fails**

  Run: `pytest tests/test_ai_enhance.py -v`
  Expected: FAIL (specifically `test_process_single_item_success` and `test_process_single_item_fallback` will fail or throw errors due to missing fields or invoke mismatch)

- [ ] **Step 3: Write minimal implementation**

  Modify [ai/enhance.py](file:///home/default_user/research_acc/ai/enhance.py):
  1. Update `default_ai_fields` at line 94 to:
     ```python
     default_ai_fields = {
         "translated_title": "Title translation failed",
         "tldr": "Summary generation failed",
         "motivation": "Motivation analysis unavailable",
         "method": "Method extraction failed",
         "result": "Result analysis unavailable",
         "conclusion": "Conclusion extraction failed",
         "remote_sensing_cross": "Remote sensing cross-disciplinary scheme unavailable"
     }
     ```
  2. Pass `title` in the chain invoke arguments at line 104:
     ```python
         response: Structure = chain.invoke({
             "language": language,
             "content": item['summary'],
             "title": item.get('title', '')
         })
     ```
  3. Update parallel thread failure default dictionary mapping in `process_all_items` (line 180):
     ```python
                 processed_data[idx]['AI'] = {
                     "translated_title": "Processing failed",
                     "tldr": "Processing failed",
                     "motivation": "Processing failed",
                     "method": "Processing failed",
                     "result": "Processing failed",
                     "conclusion": "Processing failed",
                     "remote_sensing_cross": "Processing failed"
                 }
     ```

- [ ] **Step 4: Run test to verify it passes**

  Run: `pytest tests/test_ai_enhance.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**

  ```bash
  git add ai/enhance.py tests/test_ai_enhance.py
  git commit -m "feat(ai): pass title to chain invoke and update default AI fields"
  ```

---

### Task 3: Normalise translated_title and Render Bilingual Titles in JS

**Files:**
- Modify: `js/app.js` (Extract `translated_title` in normalize; update card and modal title rendering HTML)
- Modify: `js/statistic.js` (Extract `translated_title` in normalize; update sidebar list rendering HTML)
- Modify: `tests/test_server.py` (Verify FastAPI integration keeps new JSON schema intact)

**Interfaces:**
- Consumes: API response with `AI.translated_title`.
- Produces: DOM elements containing bilingual titles.

- [ ] **Step 1: Write the failing test**

  We will write a test case in `tests/test_server.py` ensuring the FastAPI `/api/papers` returns the schema with `translated_title` nested under `AI`.
  Modify `tests/test_server.py:70-85`:
  
  ```python
      # Setup temp data folder for testing
      os.makedirs("data", exist_ok=True)
      test_file = "data/2026-07-09_AI_enhanced_Chinese.jsonl"
      with open(test_file, "w") as f:
          f.write(json.dumps({
              "id": "123",
              "title": "Test Paper", 
              "authors": ["Author 1"], 
              "categories": ["cs.CV"], 
              "AI": {
                  "translated_title": "测试论文标题",
                  "tldr": "Tldr"
              }
          }) + "\n")
  ```
  Ensure testing code reads `translated_title` verification:
  ```python
        # Get papers
        response = client.get("/api/papers?date=2026-07-09&lang=Chinese", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["AI"]["translated_title"] == "测试论文标题"
  ```

- [ ] **Step 2: Run test to verify it fails**

  Run: `pytest tests/test_server.py -v`
  Expected: FAIL on assertion `assert response.json()[0]["AI"]["translated_title"] == "测试论文标题"`.

- [ ] **Step 3: Write minimal implementation**

  1. **Fix tests/test_server.py**: Ensure dummy data structure matches what's written.
  2. **Modify JS Normalize Logic**:
     - In [js/app.js](file:///home/default_user/research_acc/js/app.js) find `normalizePaper` (line 707) and add:
       ```javascript
       translated_title: paper.AI && paper.AI.translated_title ? paper.AI.translated_title : '',
       ```
     - In [js/statistic.js](file:///home/default_user/research_acc/js/statistic.js) find `normalizePaper` (line 177) and add:
       ```javascript
       translated_title: paper.AI && paper.AI.translated_title ? paper.AI.translated_title : '',
       ```
  3. **Modify JS Render Logic**:
     - In [js/app.js](file:///home/default_user/research_acc/js/app.js) inside `renderPapers` (line 1295):
       ```javascript
       // Find: <h3 class="paper-card-title">${highlightedTitle}</h3>
       // Replace with:
       <h3 class="paper-card-title">
         ${highlightedTitle}
         ${paper.translated_title && paper.translated_title !== paper.title ? `<div class="paper-title-zh">${paper.translated_title}</div>` : ''}
       </h3>
       ```
     - In [js/app.js](file:///home/default_user/research_acc/js/app.js) inside `showPaperDetails` (line 1342):
       ```javascript
       // Find: modalTitle.innerHTML = paperIndex ? `<span class="paper-index-badge">${paperIndex}</span> ${highlightedTitle}` : highlightedTitle;
       // Replace with:
       let titleHtml = highlightedTitle;
       if (paper.translated_title && paper.translated_title !== paper.title) {
         titleHtml += `<div class="paper-modal-title-zh">${paper.translated_title}</div>`;
       }
       modalTitle.innerHTML = paperIndex ? `<span class="paper-index-badge">${paperIndex}</span> ${titleHtml}` : titleHtml;
       ```
     - In [js/statistic.js](file:///home/default_user/research_acc/js/statistic.js) inside `showRelatedPapers` (line 962):
       ```javascript
       // Find: <a href="${paper.url}" target="_blank" class="paper-title">${paper.title}</a>
       // Replace with:
       <a href="${paper.url}" target="_blank" class="paper-title">
         ${paper.title}
         ${paper.translated_title && paper.translated_title !== paper.title ? `<div class="paper-title-zh">${paper.translated_title}</div>` : ''}
       </a>
       ```

- [ ] **Step 4: Run test to verify it passes**

  Run: `pytest tests/test_server.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**

  ```bash
  git add js/app.js js/statistic.js tests/test_server.py
  git commit -m "feat(frontend): extract and render bilingual paper titles in card, modal, and statistics"
  ```

---

### Task 4: Add Styling Rules in CSS

**Files:**
- Modify: `css/styles.css` (Style classes for bilingual rendering)
- Modify: `css/statistic.css` (Style classes for bilingual rendering in sidebar)

- [ ] **Step 1: Write styling classes in CSS**

  Append styles to the end of [css/styles.css](file:///home/default_user/research_acc/css/styles.css):
  
  ```css
  /* Bilingual paper title styles */
  .paper-title-zh {
    font-size: 14px;
    color: var(--text-secondary, #666);
    margin-top: 6px;
    font-weight: 500;
    line-height: 1.4;
  }

  .paper-modal-title-zh {
    font-size: 16px;
    color: var(--text-muted, #888);
    margin-top: 8px;
    font-weight: 500;
    line-height: 1.4;
  }
  ```

  And append styles to the end of [css/statistic.css](file:///home/default_user/research_acc/css/statistic.css):
  
  ```css
  /* Bilingual paper title styles for statistics sidebar */
  .paper-title-zh {
    font-size: 13px;
    color: var(--text-secondary, #666);
    margin-top: 4px;
    font-weight: 500;
    line-height: 1.4;
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add css/styles.css css/statistic.css
  git commit -m "style: add css styles for bilingual titles"
  ```
