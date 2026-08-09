# 关键词共现网络优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复关键词共现网络的跨范围错误关联，并通过强关系筛选和前端视觉反馈提升图表可读性。

**Architecture:** 后端在 `stats.py` 中先按同一论文范围生成候选共现边，再用确定性的最低次数、全局上限和节点度上限筛选边；前端继续消费 `{nodes, links}`，但在 D3 渲染时清理旧状态、强化强边并改善悬停和空状态。

**Tech Stack:** FastAPI、SQLite、Python pytest、D3.js、原生 JavaScript。

## Global Constraints

- 保持 `/api/stats/network` 返回 `{nodes, links}` 结构。
- 不修改数据库结构，不引入新的关联度模型。
- 具体分类时两个关键词必须来自同一分类；`All` 时按日期和语言隔离。
- 强关系规则：共现至少 2 次，全局最多 180 条边，每个节点最多 12 条边。

---

### Task 1: 提取并测试共现边筛选逻辑

**Files:**
- Create: `tests/test_stats_network.py`
- Modify: `server_modules/stats.py`

**Interfaces:**
- Produces private helper `_filter_network_links(links, min_value=2, max_links=180, max_degree=12)`，输入边字典列表，返回筛选后的边字典列表。

- [ ] **Step 1: Write failing unit tests**

```python
from server_modules.stats import _filter_network_links


def test_filter_network_links_removes_weak_edges_and_limits_degree():
    links = [
        {"source": "a", "target": "b", "value": 5},
        {"source": "a", "target": "c", "value": 4},
        {"source": "a", "target": "d", "value": 1},
    ]

    result = _filter_network_links(links, min_value=2, max_links=180, max_degree=1)

    assert result == [{"source": "a", "target": "b", "value": 5}]


def test_filter_network_links_has_deterministic_tie_breaking_and_global_limit():
    links = [
        {"source": "b", "target": "c", "value": 2},
        {"source": "a", "target": "d", "value": 2},
        {"source": "a", "target": "c", "value": 3},
    ]

    result = _filter_network_links(links, min_value=2, max_links=2, max_degree=12)

    assert result == [
        {"source": "a", "target": "c", "value": 3},
        {"source": "a", "target": "d", "value": 2},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stats_network.py -q`

Expected: FAIL because `_filter_network_links` is not defined.

- [ ] **Step 3: Implement the minimal pure helper**

Implement `_filter_network_links` in `server_modules/stats.py`: normalize source/target to strings for sorting, discard values below `min_value`, sort by descending numeric value then source and target ascending, greedily accept an edge only when neither endpoint has reached `max_degree`, and stop at `max_links`.

- [ ] **Step 4: Run the focused tests**

Run: `pytest tests/test_stats_network.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_stats_network.py server_modules/stats.py
git commit -m "test(stats): cover keyword network edge filtering"
```

### Task 2: 修复后端范围关联并接入筛选

**Files:**
- Modify: `server_modules/stats.py:251-363`
- Modify: `tests/test_stats_network.py`

**Interfaces:**
- `get_network_stats` continues returning nodes with `id`, `value`, `group` and links with `source`, `target`, `value`.

- [ ] **Step 1: Add a regression test for scope isolation**

Create an isolated SQLite schema with `keyword_stats` and `paper_keywords`, insert rows sharing `paper_id` but differing by date/category, call `get_network_stats` with mocked auth/config, and assert only same-date, same-language, same-category pairs are returned. Also assert a value-1 candidate is absent and filtered links do not exceed the configured limits.

- [ ] **Step 2: Run the regression test to verify it fails**

Run: `pytest tests/test_stats_network.py -q`

Expected: FAIL because the current JOIN only matches `paper_id` and the category query only constrains `pk1`.

- [ ] **Step 3: Update both SQL branches**

Extend the `pk1`/`pk2` JOIN with equal `paper_date`, `language`, and `category` predicates. In the category branch, retain the selected category predicate for both sides through the same-scope JOIN. Group by both keywords, convert rows to link dictionaries, then call `_filter_network_links` before community detection. Rebuild `nodes` from endpoints present in filtered links, preserving their original frequency values.

- [ ] **Step 4: Run focused and existing backend tests**

Run: `pytest tests/test_stats_network.py tests/test_server.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server_modules/stats.py tests/test_stats_network.py
git commit -m "fix(stats): constrain and sparsify keyword cooccurrence"
```

### Task 3: 优化 D3 网络图重绘和视觉编码

**Files:**
- Modify: `js/statistic.js:1231-1241,2157-2309`

**Interfaces:**
- `renderNetwork(dataOrPapers)` remains the public renderer used by `window.updateCharts`.

- [ ] **Step 1: Add renderer changes**

At the start of `renderNetwork`, remove prior SVG and tooltip elements before creating new ones. Handle `nodes.length === 0` and `links.length === 0` with explicit messages. Add a small summary element containing node and edge counts. Use link opacity based on normalized `value`, keep width mapped to value, and use the existing node frequency scale. Preserve zoom, drag, click, and community colors.

- [ ] **Step 2: Add hover adjacency highlighting**

Build an undirected adjacency map from source/target IDs after D3 has potentially replaced link endpoints with node objects. On node mouseover, set node/label opacity to full only for the hovered node or adjacent nodes, and set link opacity to full only for incident links. On mouseout restore the encoded base opacity rather than a hard-coded value.

- [ ] **Step 3: Run JavaScript syntax validation**

Run: `node --check js/statistic.js`

Expected: no output and exit code 0.

- [ ] **Step 4: Commit**

```bash
git add js/statistic.js
git commit -m "feat(stats): improve keyword network readability"
```

### Task 4: 全量验证与差异检查

**Files:**
- No new files.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Verify the frontend file and changed paths**

Run: `node --check js/statistic.js && git status --short && git diff HEAD~3 --stat`

Expected: JavaScript syntax succeeds; only the design/plan docs and intended stats/test files are changed.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff HEAD~3 -- server_modules/stats.py js/statistic.js tests/test_stats_network.py`

Confirm the SQL scope predicates, deterministic edge filtering, D3 cleanup, empty state, and hover opacity behavior are present without unrelated refactoring.
