# Design Specification: AI Paper Title Translation & Bilingual Display

This specification details the design for translating paper titles from English to Chinese during the AI-enhancement process and presenting the bilingual titles in the frontend user interface.

## 1. Background & Goals

Currently, the AI enhancement process reads the paper abstract, generates structured fields (TLDR, motivation, method, result, conclusion, remote sensing crossover suggestion) in Chinese, but leaves the paper title in its original English. This design extends the system to:
- Translate the English title to Chinese (or another target language) concurrently during the LLM structure generation.
- Support bilingual display in the frontend cards, detail modals, and statistics sidebar with high visual aesthetics.
- Ensure backwards compatibility with older data items that do not contain a translated title.

## 2. Proposed System Architecture

### 2.1 Backend Changes (AI Enhancement)

#### A. Pydantic Structure Definition
We will add a new field `translated_title` in [structure.py](file:///home/default_user/research_acc/ai/structure.py):
```python
class Structure(BaseModel):
    translated_title: str = Field(description="translate the paper's title into Chinese (or the specified target language)")
    tldr: str = Field(...)
    # Other existing fields...
```

#### B. Template Changes
We will update [template.txt](file:///home/default_user/research_acc/ai/template.txt) to supply the title to the LLM model:
```text
Title: {title}
Please analyze the following abstract of papers. 

Content:
{content}
```

#### C. AI Enhance script Changes
We will edit [enhance.py](file:///home/default_user/research_acc/ai/enhance.py) to:
- Update `default_ai_fields` to include `"translated_title": "Title translation failed"`.
- Supply `"title": item.get("title", "")` to `chain.invoke`.
- Ensure fallbacks in thread executors correctly output `"translated_title": "Processing failed"` on exception.

### 2.2 Frontend Changes (JS & CSS)

#### A. Data Normalization
We will update `normalizePaper` in both [app.js](file:///home/default_user/research_acc/js/app.js) and [statistic.js](file:///home/default_user/research_acc/js/statistic.js):
```javascript
function normalizePaper(paper, date) {
  // ... existing code ...
  return {
    title: paper.title || '',
    translated_title: paper.AI && paper.AI.translated_title ? paper.AI.translated_title : '',
    // ... existing fields ...
  };
}
```

#### B. Frontend Render Logic (app.js)
1. **Paper Card Rendering (app.js: `renderPapers`)**:
   In `paperCard.innerHTML`, the title structure:
   ```html
   <h3 class="paper-card-title">${highlightedTitle}</h3>
   ```
   will be updated to:
   ```html
   <h3 class="paper-card-title">
     ${highlightedTitle}
     ${paper.translated_title && paper.translated_title !== paper.title ? `<div class="paper-title-zh">${paper.translated_title}</div>` : ''}
   </h3>
   ```
2. **Modal View Rendering (app.js: `showPaperDetails`)**:
   The `modalTitle.innerHTML` rendering:
   ```javascript
   modalTitle.innerHTML = paperIndex ? `<span class="paper-index-badge">${paperIndex}</span> ${highlightedTitle}` : highlightedTitle;
   ```
   will be updated to:
   ```javascript
   let titleHtml = highlightedTitle;
   if (paper.translated_title && paper.translated_title !== paper.title) {
     titleHtml += `<div class="paper-modal-title-zh">${paper.translated_title}</div>`;
   }
   modalTitle.innerHTML = paperIndex ? `<span class="paper-index-badge">${paperIndex}</span> ${titleHtml}` : titleHtml;
   ```

#### C. Sidebar Rendering (statistic.js)
The keyword search related papers rendering in [statistic.js](file:///home/default_user/research_acc/js/statistic.js):
```html
<a href="${paper.url}" target="_blank" class="paper-title">${paper.title}</a>
```
will be updated to:
```html
<a href="${paper.url}" target="_blank" class="paper-title">
  ${paper.title}
  ${paper.translated_title && paper.translated_title !== paper.title ? `<div class="paper-title-zh">${paper.translated_title}</div>` : ''}
</a>
```

#### D. Stylings (css/index.css or style block)
We will add styles in CSS for `.paper-title-zh` and `.paper-modal-title-zh`:
```css
.paper-title-zh {
  font-size: 0.85em;
  color: var(--text-secondary, #666);
  margin-top: 6px;
  font-weight: 500;
  line-height: 1.4;
}

.paper-modal-title-zh {
  font-size: 0.8em;
  color: var(--text-muted, #888);
  margin-top: 8px;
  font-weight: 500;
  line-height: 1.4;
}
```

## 3. Verification Plan
- **Backend**: Run `python ai/enhance.py --data <test_file.jsonl>` and inspect output to confirm that the `translated_title` field is populated correctly by the LLM.
- **Frontend**: Check that papers displaying in both the list, detail modal, and statistics view show bilingual titles beautifully.
