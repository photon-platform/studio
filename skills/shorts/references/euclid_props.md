# Euclid Propositions Shorts Playbook (`I.1` – `XIII.18` • All 465 Propositions)

This reference defines the exact canonical structure, data sources, progressive SVG state machine, and `"type": "euclid_step"` schema for producing all **465 Propositions of Euclid's *Elements*** as `1080×1920` (`9:16`) vertical YouTube Shorts.

---

## 1. Dual Source-of-Truth Repositories

Every Euclid Proposition Short draws from two synchronized local repositories:

1. **`geometor/euclid` (Heath Text, Folios & Interactive SVG)**:
   - Raw semantic RST: `/home/phi/PROJECTS/geometor/euclid/resources/heath/volume_<V>/book_<b>/propositions/<folio>.rst` (e.g. `0255.rst` for `I.1`).
   - Canonical Vector SVG: `/home/phi/PROJECTS/geometor/euclid/resources/heath/volume_<V>/book_<b>/propositions/<folio>.g1.svg` (e.g. `0255.g1.svg`).
   - Folio PNG scan: `/home/phi/PROJECTS/geometor/euclid/resources/heath/volume_<V>/book_<b>/propositions/<folio>.png`.
2. **`geometor/elements` (G-Index Canonical ID, Signature, Pseudocode Steps & Python Model)**:
   - Pseudocode & Dependency DAG: `/home/phi/PROJECTS/geometor/elements/docsrc/elements2/G.<n>/index.rst` (e.g. `G.5/index.rst` for `I.1`).
   - Symbolic Python Construction: `/home/phi/PROJECTS/geometor/elements/docsrc/elements2/G.<n>/g1.py`.

---

## 2. The 3-Act Euclidean Short Structure (35–55 Seconds)

Every Proposition Short follows Euclid's classical 6-part division compressed into **3 harmonic `[bell:...]` segments** and **6 to 8 progressive `"euclid_step"` sub-slides**:

### Act I — `[bell:proposition]` (The Enunciation & Setting-Out / *Protasis* & *Ekthesis*)
- **Sub-slide 1 (`ENUNCIATION` / `GOAL`)**:
  - Shows the proposition badge (`BOOK I • PROP. 1`), the G-Index badge (`G.5`), the formal Enunciation, the `.. signature::` (`equilateral_triangle(/ A B /) → < A B C >`), and the complete target figure or 1908 Heath folio scan inset.
- **Sub-slide 2 (`GIVEN` / *Ekthesis*)**:
  - Shows **only** the given elements in the SVG (`visible_ids` = `["el-seg-AB", "el-pt-A", "el-pt-B", "lbl-A", "lbl-B"]`), with the given element highlighted in **GEOMETOR Gold (`#e8b85a`)** (`active_ids` = `["el-seg-AB", "hl-seg-AB", "el-pt-A", "el-pt-B", "lbl-A", "lbl-B"]`).

### Act II — `[bell:construction]` (The Machinery / *Kataskeuē*)
- **Sub-slides 3..K (`CONSTRUCT` & `INTERSECT`)**:
  - One sub-slide per construction step (`[I.Post.3]`, `[I.Post.1]`, `[I.Post.2]`, or previously proved proposition `[I.1 • G.5]`).
  - Previously constructed elements stay in warm ivory (`#d6cfc5` in `visible_ids`); the newly constructed circle, line, or intersection point glows in **GEOMETOR Gold (`#e8b85a` in `active_ids`)**.
  - Displays the symbolic function call from `elements` (`circle(A, / A B /) → ( B C D )`) alongside the Heath clause and the prerequisite pill (`[I.Post.3 • G.4] To describe a circle with any centre and distance`).

### Act III — `[bell:insight]` (The Demonstration & Conclusion / *Apodeixis* & *Symperasma*)
- **CRITICAL RULE — One Equality Statement at a Time (`ASSERT` Sub-slides)**:
  - **Never** cram two or three separate equality statements onto a single sub-slide! When a proof chains multiple equalities (e.g., `AC = AB` from circle `CDB`, then `BC = BA` from circle `CAE`, then `CA = AB = BC`), give **each equality its own dedicated sub-slide**:
    1. First sub-slide: *"This is equal to that"* — highlight **only** the first pair of equal elements in **Assertion Green (`#5ec27e` in `equal_ids` / `tick_ids`)** and the single geometric entity (center/circle/triangle) that justifies it in **GEOMETOR Gold (`active_ids`)**.
    2. Next sub-slide: *"And that is equal to that"* — unhighlight the previous pair back to calm cream (`#d6cfc5`) and highlight **only** the second pair of equal elements in **Assertion Green** alongside its justifying entity in **Gold**.
    3. Conclusion sub-slide (`Q.E.F.` / `Q.E.D.`): *"Therefore this is equal to that"* — combine the proved equalities on the completed figure, display the `Q.E.F.` / `Q.E.D.` badge, and show the prerequisite & downstream impact summary (`4 Prerequisites → Powers 247 Downstream Propositions`).
- **Header vs. Graphic Box HUD Layout**:
  - **Top Header**: Displays `[BOOK I • PROP. N]` and the full Heath reference `[Heath (1908) • Folio NNNN • p. NNN]` above the classical Heath enunciation.
  - **Graphic Stage Top HUD**: Connects `[G-INDEX • G.k]` directly with the GEOMETOR symbolic formula (`equilateral_triangle(/ A B /) → < A B C >`).

---

## 3. `"type": "euclid_step"` Scene Schema for `shorts.py`

In `<short_name>_scenes.json`, each sub-slide uses `"type": "euclid_step"`:

```json
{
  "type": "euclid_step",
  "weight": 1.0,
  "prop_id": "BOOK I • PROP. 1",
  "g_id": "G.5",
  "folio": "Folio 0255 • p. 241",
  "enunciation": "On a given finite straight line to construct an equilateral triangle.",
  "step_num": 2,
  "step_total": 7,
  "step_kind": "CONSTRUCT",
  "svg_path": "/home/phi/PROJECTS/geometor/euclid/resources/heath/volume_I/book_i/propositions/0255.g1.svg",
  "visible_ids": ["el-seg-AB", "el-pt-A", "el-pt-B", "lbl-A", "lbl-B", "el-circle-BCD", "el-pt-D", "lbl-D"],
  "active_ids": ["el-circle-BCD", "hl-circle-BCD", "el-pt-A", "lbl-A", "el-seg-AB"],
  "equal_ids": [],
  "tick_ids": [],
  "symbolic": "circle(A, / A B /) → ( B C D )",
  "clause_html": "With centre <span class=\"g-tok\">A</span> and distance <span class=\"g-tok\">AB</span> let the circle <span class=\"g-tok\">BCD</span> be described.",
  "just_id": "I.Post.3 • G.4",
  "just_title": "To describe a circle with any centre and distance."
}
```

### Inline Token Classes in `clause_html`:
- `<span class="g-tok">AB</span>`: Gold geometric token pill (`#e8b85a` border/text, matches active gold SVG element).
- `<span class="eq-tok">equal</span>`: Green assertion token pill (`#5ec27e` border/text, matches green SVG equality & tick marks `|`).
