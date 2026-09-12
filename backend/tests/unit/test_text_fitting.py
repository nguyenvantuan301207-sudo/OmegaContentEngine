import pytest

from omega.application.text_fitting import fit_text


@pytest.mark.parametrize(
    "role,text",
    [
        ("title", "Short title"),
        ("title", "A medium title with several useful words and numbers 12345"),
        ("title", "A very long title " * 30),
        ("body", "Short body"),
        ("body", "Medium body text with Unicode punctuation — and a question?"),
        ("body", "Long body copy " * 80),
        ("body", "https://example.com/" + "single-long-token-" * 30),
    ],
)
def test_text_fitting_matrix_is_bounded_and_deterministic(role, text):
    kwargs = dict(
        role=role,
        initial_font_size=72 if role == "title" else 36,
        min_font_size=36 if role == "title" else 22,
        max_lines=3 if role == "title" else 7,
        chars_per_line_at_initial_size=24 if role == "title" else 48,
    )
    first = fit_text(text, **kwargs)
    second = fit_text(text, **kwargs)

    assert first == second
    assert first.line_count <= first.max_lines
    assert first.font_size >= kwargs["min_font_size"]
    assert first.rendered_text
    assert first.text_truncated == ("…" in first.rendered_text)


def test_text_fitting_uses_wrap_then_downscale_before_truncation():
    medium = fit_text(
        "one two three four five six seven",
        role="title",
        initial_font_size=60,
        min_font_size=40,
        max_lines=2,
        chars_per_line_at_initial_size=15,
    )
    extreme = fit_text(
        "word " * 100,
        role="body",
        initial_font_size=40,
        min_font_size=30,
        max_lines=2,
        chars_per_line_at_initial_size=20,
    )

    assert medium.font_size < 60
    assert medium.text_truncated is False
    assert extreme.font_size == 30
    assert extreme.text_truncated is True
