
from apps.automations.models import AutomationKeyword
from apps.automations.services.matching import matches, normalize


def rule(**kwargs) -> AutomationKeyword:
    """An unsaved keyword rule; matching never touches the database."""
    defaults = {
        "keyword": "LINK",
        "match_type": AutomationKeyword.MatchType.EXACT,
        "case_sensitive": False,
    }
    return AutomationKeyword(**{**defaults, **kwargs})


# --- normalisation ---------------------------------------------------

def test_normalize_collapses_whitespace_and_trims():
    assert normalize("  LINK   please \n") == "LINK please"


def test_normalize_strips_zero_width_characters():
    assert normalize("LI​NK") == "LINK"


def test_normalize_handles_none_and_empty():
    assert normalize(None) == ""
    assert normalize("") == ""


def test_normalize_truncates_absurdly_long_comments():
    assert len(normalize("a" * 5000)) == 2200


# --- exact -----------------------------------------------------------

def test_exact_match():
    assert matches(rule(), "LINK")


def test_exact_match_is_case_insensitive_by_default():
    assert matches(rule(), "link")
    assert matches(rule(), "LiNk")


def test_exact_match_respects_case_sensitivity():
    assert not matches(rule(case_sensitive=True), "link")
    assert matches(rule(case_sensitive=True), "LINK")


def test_exact_match_rejects_extra_words():
    assert not matches(rule(), "send me the LINK")


def test_exact_match_ignores_surrounding_whitespace():
    assert matches(rule(), "   link  ")


# --- contains --------------------------------------------------------

def test_contains_match():
    r = rule(match_type=AutomationKeyword.MatchType.CONTAINS)
    assert matches(r, "please send the LINK now")
    assert matches(r, "blinking")  # substring, by design


# --- whole word ------------------------------------------------------

def test_whole_word_matches_a_standalone_word():
    r = rule(match_type=AutomationKeyword.MatchType.WHOLE_WORD)
    assert matches(r, "please send the link now")
    assert matches(r, "link!")
    assert matches(r, "(link)")


def test_whole_word_rejects_substrings():
    r = rule(match_type=AutomationKeyword.MatchType.WHOLE_WORD)
    assert not matches(r, "blinking")
    assert not matches(r, "linked")


def test_whole_word_handles_regex_metacharacters_in_the_keyword():
    r = rule(keyword="c++", match_type=AutomationKeyword.MatchType.WHOLE_WORD)
    assert matches(r, "I want c++")
    assert not matches(r, "I want c")


# --- any comment -----------------------------------------------------

def test_any_comment_matches_everything():
    r = rule(match_type=AutomationKeyword.MatchType.ANY, keyword="")
    assert matches(r, "literally anything")
    assert matches(r, "")


def test_empty_keyword_never_matches_for_keyword_rules():
    assert not matches(rule(keyword=""), "LINK")


def test_emoji_and_unicode_comments_do_not_crash():
    assert matches(rule(match_type=AutomationKeyword.MatchType.CONTAINS), "link 🔥🔥")
