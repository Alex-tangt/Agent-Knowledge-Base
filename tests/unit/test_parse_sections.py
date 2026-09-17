"""切节与窗口压制的纯函数单测（不依赖模型 / 解析引擎）。"""
from memory_agent.parse import Section, enforce_window, split_markdown_sections


def test_split_by_heading_levels_keeps_titles_and_markdown():
    markdown = "# Intro\n\nalpha\n\n## Sub\n\nbeta\n\n# End\n\ngamma\n"

    sections = split_markdown_sections(markdown, fallback_title="doc")

    assert [(s.title, s.level) for s in sections] == [("Intro", 1), ("Sub", 2), ("End", 1)]
    assert "alpha" in sections[0].markdown
    assert "## Sub" in sections[1].markdown


def test_preamble_before_first_heading_gets_fallback_title():
    sections = split_markdown_sections("intro text\n\n# A\n\nbody\n", fallback_title="Doc")

    assert sections[0].title == "Doc"
    assert sections[0].markdown.startswith("# Doc")
    assert sections[1].title == "A"


def test_headingless_document_is_single_self_contained_section():
    sections = split_markdown_sections("just text\nmore text\n", fallback_title="Plain")

    assert len(sections) == 1
    assert sections[0].title == "Plain"
    assert sections[0].markdown.startswith("# Plain")


def test_heading_inside_fenced_code_is_not_split():
    markdown = "# Real\n\n```\n# not a heading\n```\n\ntail\n"

    sections = split_markdown_sections(markdown)

    assert [s.title for s in sections] == ["Real"]
    assert "# not a heading" in sections[0].markdown


def test_heading_section_without_body_is_dropped():
    sections = split_markdown_sections("# Parent\n\n## Child\n\nbody\n")

    assert [s.title for s in sections] == ["Child"]


def test_blank_input_returns_no_sections():
    assert split_markdown_sections("   \n\n") == []


def test_heading_only_document_falls_back_to_one_section():
    sections = split_markdown_sections("# Only", fallback_title="doc")

    assert [s.title for s in sections] == ["Only"]


def test_enforce_window_is_noop_within_limit():
    section = Section(title="A", level=1, markdown="# A\n\nshort\n")

    assert enforce_window([section], 6000) == [section]


def test_enforce_window_splits_oversize_by_paragraph_and_keeps_title():
    body = "\n\n".join(f"Para {i} " + "x" * 400 for i in range(30))
    section = Section(title="Long", level=2, markdown=f"## Long\n\n{body}")

    parts = enforce_window([section], 1200)

    assert len(parts) > 1
    assert all(p.title.startswith("Long") for p in parts)
    assert parts[0].title == "Long"
    assert all(p.markdown.startswith("## Long") for p in parts)
    assert all(len(p.markdown) <= 1200 for p in parts)
    assert [p.order for p in parts] == list(range(len(parts)))


def test_enforce_window_hard_splits_single_giant_paragraph():
    section = Section(title="Giant", level=1, markdown="# Giant\n\n" + "y" * 5000)

    parts = enforce_window([section], 800)

    assert len(parts) >= 7
    assert all(len(p.markdown) <= 800 for p in parts)
    assert parts[0].markdown.startswith("# Giant")
    assert parts[1].title == "Giant (part 2)"


def test_enforce_window_disabled_when_limit_invalid():
    section = Section(title="A", level=1, markdown="# A\n\n" + "z" * 100)

    assert enforce_window([section], 0) == [section]
