# Task 3 Report: Normalise translated_title and Render Bilingual Titles in JS

## What Was Implemented

1. **FastAPI Integration Test & Mock Update**:
   - Modified `tests/test_server.py` to write a mock paper that contains a nested `translated_title` inside the `AI` key.
   - Added an assertion checking that the retrieved paper response from the `/api/papers` endpoint preserves the `translated_title` property in the `AI` schema.

2. **JavaScript Normalize Logic**:
   - Updated `normalizePaper` in [js/app.js](file:///home/default_user/research_acc/js/app.js) to extract `translated_title` from `paper.AI.translated_title` and default it to an empty string if missing.
   - Updated `normalizePaper` in [js/statistic.js](file:///home/default_user/research_acc/js/statistic.js) similarly to ensure consistency across search, list, and statistics views.

3. **Frontend Rendering Logic for Bilingual Titles**:
   - Updated `renderPapers` in [js/app.js](file:///home/default_user/research_acc/js/app.js) to display the bilingual title (original English title + Chinese translation directly below it with CSS class `paper-title-zh`) on paper list cards.
   - Updated `showPaperDetails` in [js/app.js](file:///home/default_user/research_acc/js/app.js) to render the bilingual title (with CSS class `paper-modal-title-zh` for the translation) in the paper detail modal view.
   - Updated `showRelatedPapers` in [js/statistic.js](file:///home/default_user/research_acc/js/statistic.js) to display the bilingual title in the sidebar list for keyword search results under the statistics tab.

---

## Files Changed

- [tests/test_server.py](file:///home/default_user/research_acc/tests/test_server.py) (Mock paper update and API schema assertion)
- [js/app.js](file:///home/default_user/research_acc/js/app.js) (Normalized data extraction & rendering in cards/modals)
- [js/statistic.js](file:///home/default_user/research_acc/js/statistic.js) (Normalized data extraction & rendering in statistics sidebar)

---

## TDD Evidence

### 1. RED (Failing Test Run)
First, the assertion `assert response.json()[0]["AI"]["translated_title"] == "测试论文标题"` was added without updating the mock data generator:

```bash
$ PYTHONPATH=. .venv/bin/pytest tests/test_server.py -v
============================= test session starts ==============================
...
tests/test_server.py::test_auth_and_data_apis FAILED                     [100%]

=================================== FAILURES ===================================
___________________________ test_auth_and_data_apis ____________________________
...
    # Get papers
    response = client.get("/api/papers?date=2026-07-09&lang=Chinese", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "Test Paper"
>   assert response.json()[0]["AI"]["translated_title"] == "测试论文标题"
E   KeyError: 'translated_title'

tests/test_server.py:84: KeyError
==================== 1 failed, 5 passed, 1 warning in 0.29s ====================
```

### 2. GREEN (Passing Test Run)
After updating the mock data file generator to include `translated_title` under the nested `AI` key, all tests successfully passed:

```bash
$ PYTHONPATH=. .venv/bin/pytest tests/test_server.py -v
============================= test session starts ==============================
...
tests/test_server.py::test_index_page PASSED                             [ 16%]
tests/test_server.py::test_login_page PASSED                             [ 33%]
tests/test_server.py::test_settings_page PASSED                          [ 50%]
tests/test_server.py::test_statistic_page PASSED                         [ 66%]
tests/test_server.py::test_static_files PASSED                           [ 83%]
tests/test_server.py::test_auth_and_data_apis PASSED                     [100%]

========================= 6 passed, 1 warning in 0.28s =========================
```

---

## Self-Review Findings

- **Data Safety**: Checked checks for null/undefined/missing properties in the JSON response payload. Handled gracefully by fallback to empty string `''`.
- **Duplicate Prevention**: Title translation is only displayed if it is present and not identical to the original English title.
- **Test Integrity**: Ensured FastAPI test properly cleans up its created temporary files (`test_file`) in a `finally` block.

---

## Issues or Concerns
None. The code and tests conform exactly to requirements.
