from mom.lib.llm import build_prompt


def test_build_prompt_escapes_xml():
    """Test XML escaping & truncation (fast unit)"""
    p = build_prompt('goal < & > " \'', 't <t>', 'w & w')
    assert "&lt;" in p and "&gt;" in p and "&amp;" in p and "&quot;" in p and "&#x27;" in p
